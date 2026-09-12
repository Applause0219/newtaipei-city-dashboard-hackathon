// Insight Validator / Scorer / Artifact Builder（blueprint §10-§11）
//
// 這支檔案存在的唯一理由：
//
//   ── AI 洞察系統最大的風險不是「找不到東西」，是「找到一個資料撐不住的故事」──
//
// 所以這裡的順序是刻意的：**先閘門，後評分**。
// 閘門（gate）是硬拒絕，任何候選只要踩到一條就直接出局，不進報告；
// 分數只決定「已經合法的結論之間誰先講」，永遠不能反過來把違法的結論撈回來。
//
// 為什麼要把閘門寫成一個「有名字、有順序」的陣列，而不是一串 if：
// 被拒絕的時候必須能回答「是哪一關擋的、為什麼」。比賽現場被評審追問
// 「你怎麼知道這個結論不是硬湊的」，我們要能指著某一關說出具體理由，
// 而不是說「驗證器覺得不行」。
//
// 與其他模組的關係：本檔只認 schema.js 宣告的形狀。
// age.js（AgeScope）與 mining.js（InsightCandidate）是平行開發的，
// 這裡不 import 它們——耦合到實作就等於綁死開發順序。

import {
	AGE_CLASS,
	AGGREGATION_RULE,
	INSIGHT_TYPE,
	POLICY_YOUTH,
	validateAgeScope,
	validateArtifact,
	validateCandidate,
} from "./schema.js";

// ─────────────────────────────────────────────────────────────
// 0. 常數與小工具
// ─────────────────────────────────────────────────────────────

/**
 * 法定青年標籤。一律從 POLICY_YOUTH 組出來，不硬寫 "18-35"。
 * 理由：法規若修正，只會改 schema.js 一處；若在十幾個字串裡硬寫，
 * 就會出現「報告寫 18-35、驗證器檢查 18-30」這種對不上的狀態。
 */
export const POLICY_AGE_LABEL = `${POLICY_YOUTH.lower}-${POLICY_YOUTH.upper}`;

/** 效果量地板。低於此值一律視為雜訊，不是發現。 */
export const EFFECT_SIZE_FLOOR = 0.1;

/**
 * 各偵測器最少需要幾個資料點。
 * 這不是品味問題：點數不足時，統計量算得出來但沒有意義。
 *   trend         3 點才談得上方向（2 點只是一條連線，任何兩點都「有趨勢」）
 *   change_point  5 點才可能有「前段／轉折／後段」（前後各至少 2 點）
 *   outlier       5 點才有分布可言，3 點裡的極端值只是最大值
 *   rank_change   4 點＝至少 2 個對象 × 2 個時間，否則排名沒有「變化」
 *   group_difference 2 組才叫比較
 */
export const MIN_POINTS_BY_TYPE = Object.freeze({
	trend: 3,
	change_point: 5,
	outlier: 5,
	rank_change: 4,
	group_difference: 2,
});

/** 各類型對應的分析方法名稱，報告的「分析方法」區塊由此產生。 */
export const METHOD_BY_TYPE = Object.freeze({
	trend: ["Trend Detection"],
	change_point: ["Trend Detection", "Change Point Detection"],
	outlier: ["Outlier Detection"],
	rank_change: ["Rank Change Detection"],
	group_difference: ["Group Difference Test"],
});

/** 類型的中文標題詞，用來組 title。 */
const TYPE_LABEL_ZH = Object.freeze({
	trend: "趨勢變化",
	change_point: "趨勢轉折",
	outlier: "異常值",
	rank_change: "排名變動",
	group_difference: "區域差異",
});

/**
 * blueprint §13.3 的 type → chart 對照。寫成表而不是 switch，
 * 是因為儀表板那邊也要讀同一份對照；分兩處寫早晚會不一致。
 */
export const CHART_BY_TYPE = Object.freeze({
	trend: "line",
	change_point: "line",
	group_difference: "bar",
	rank_change: "bar",
	outlier: "bar",
});

/**
 * 因果語氣的標記詞。
 * 這張表是「不准把相關講成因果」這條規則的執行工具，不是文字潔癖：
 * 人口下降與房價上漲同時發生，資料能證明的只有「同時發生」。
 * 只要 claim 或事實敘述裡出現這些詞，就代表有人把推測寫進了事實欄位。
 */
export const CAUSAL_MARKERS = Object.freeze([
	"導致", "造成", "因為", "由於", "使得", "引起", "引發",
	"促使", "促成", "肇因", "因此", "所以", "歸因",
]);

/** 回傳第一個命中的因果詞，沒有則回傳 null。 */
export function findCausalMarker(text) {
	if (typeof text !== "string") return null;
	return CAUSAL_MARKERS.find((w) => text.includes(w)) || null;
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isStr = (v) => typeof v === "string" && v.length > 0;
const clamp01 = (v) => Math.max(0, Math.min(1, v));

/**
 * 千分位。不用 toLocaleString：它的輸出跟執行環境的 locale 有關，
 * 同一份 artifact 在不同機器上會 render 出不同字串，
 * 「報告可重現」這個承諾就破了。
 */
function thousands(n, dec = 0) {
	const neg = n < 0;
	const [i, d] = Math.abs(n).toFixed(dec).split(".");
	const s = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
	return (neg ? "-" : "") + s + (d ? "." + d : "");
}

/**
 * 帶正負號的數值。用在斜率、差距這類「單獨列出、沒有方向詞可以依靠」的數字。
 *
 * 小數位數由呼叫端傳入，而**不是**依每個數字各自的大小決定。
 * 舊版是後者，於是同一串清單會同時出現「+79.12」與「-3,861」：
 * 讀者無從判斷那兩位小數代表精確度、還是只是排版差異。
 * 一個 fact block 的量級是固定的，格式就該是固定的。
 */
function signedNum(v, dec = 0) {
	const sign = v > 0 ? "+" : v < 0 ? "-" : "";
	return sign + thousands(Math.abs(v), dec);
}

/**
 * 這個 fact block 統一使用的小數位數，依「被量測的量級」決定。
 * 人口數講到小數點沒有意義；比率指標（老化指數 190.77、年變化 -0.42）
 * 取整則會把發現直接抹平成 0。
 */
function decimalsFor(level) {
	const a = Math.abs(level);
	if (!isNum(a) || a === 0) return 2;
	if (a >= 1000) return 0;
	if (a >= 10) return 2;
	return 3;
}

/**
 * 一個「每期變化率」小到什麼程度，就不該再宣稱它的方向。
 *
 * 判斷基準是**相對於基期水準**，不是絕對值：
 * 新莊區的 +79 人/年落在 171,342 人的基期上是 0.046%，與持平無法區分；
 * 同樣的 79 人/年放在平溪區（基期約 1,400 人）卻是 5.6%，是非常真實的變化。
 * 絕對門檻會同時放過前者、誤殺後者。
 *
 * 下限取 0.1%／期的理由：
 *   1. 這個量級的年變動落在戶籍資料本身的雜訊裡（遷徙登記時點、行政區界
 *      修正、人口普查校正），不是可解讀的人口變化。
 *   2. 更關鍵的是**符號會隨估計方法翻面**。新莊區 2000-2013 這一段，
 *      最小平方法得到 +79、頭尾兩點得到 -77——同一段資料、兩種同樣合理的
 *      算法，方向相反。方向會翻面的數字不是事實，不該印在「事實」標題底下。
 *      這正是本專案唯一的可信度來源：報出來的數字要站得住被追問。
 *
 * statistical_strength 會放寬這個帶寬：mining 對一段上下擺盪的區間本來就會
 * 給出低強度，證據越弱，我們越不該對方向下斷言。
 * 強度 1 時帶寬 0.1%，強度 0 時放寬到 0.5%。
 */
const FLAT_RATE_FLOOR = 0.001;   // 0.1%／期：強證據下的下限
const FLAT_RATE_CEIL = 0.005;    // 0.5%／期：統計強度為 0 時的上限

function flatBand(strength) {
	const s = isNum(strength) ? clamp01(strength) : 1;
	return FLAT_RATE_FLOOR + (1 - s) * (FLAT_RATE_CEIL - FLAT_RATE_FLOOR);
}

/**
 * 描述一個每期變化率。
 *
 * 幅度相對基期可忽略時，只說「接近持平」並給出帶寬上界，不給帶符號的數字。
 * 精確值**仍然留在 evidence.facts 裡**——這裡抑制的是敘述語氣，不是資料。
 * 要查原始斜率的人查得到；讀報告的人不會被一個方向不穩的數字誤導。
 */
function describeRate(v, base, dec, unit, strength, period = "每年") {
	const band = flatBand(strength);
	if (isNum(base) && Math.abs(base) > 0 && Math.abs(v) / Math.abs(base) < band) {
		return `接近持平（${period}變動不到 ${(band * 100).toFixed(1)}%）`;
	}
	return `${period} ${signedNum(v, dec)}${unit}`;
}

/** 已經是百分比的變化量，同樣套用可忽略判斷（band 本身就是百分比尺度）。 */
function describeRatePct(v, strength, period = "") {
	const band = flatBand(strength) * 100;
	if (Math.abs(v) < band) return `接近持平（變動不到 ${band.toFixed(1)}%）`;
	return `${period}${signedPct(v)}`;
}

/**
 * 百分比文字（不帶正負號）。只用在方向已經由「上升／下降」講清楚的句子裡。
 * 固定一位小數——超過一位小數對政策解讀沒有增益，只會假裝精確。
 */
function pct(v) {
	return `${Math.abs(v).toFixed(1)}%`;
}

/**
 * 帶正負號的百分比。
 * 這不是排版偏好：「轉折前變化：1.2%／轉折後變化：5.9%」會被讀成兩段都在成長，
 * 但後半段其實是 -5.9%。單獨列出的變化量沒有方向詞可以依靠，符號就是方向本身。
 */
function signedPct(v) {
	return `${v > 0 ? "+" : v < 0 ? "-" : ""}${Math.abs(v).toFixed(1)}%`;
}

/**
 * facts 的變化幅度可能以「百分點」(6.2) 或「比例」(0.062) 表示。
 * 這裡**不猜**：只認明確命名的欄位，全都沒有就回傳 null，
 * 讓 claim 少講一句話，而不是講一個可能差一百倍的數字。
 *
 * pct_change / rel_change 是 mining.js 實際使用的欄位名，
 * change_pct / change_ratio 是本模組文件裡宣告的別名。兩套都收，
 * 是為了讓兩個模組可以各自改名而不會在報告上安靜地少一句話。
 */
function changePctOf(f) {
	if (isNum(f?.pct_change)) return f.pct_change;
	if (isNum(f?.change_pct)) return f.change_pct;
	if (isNum(f?.rel_change)) return f.rel_change * 100;
	if (isNum(f?.change_ratio)) return f.change_ratio * 100;
	return null;
}

/**
 * 方向詞正規化。
 * mining.js 用 increasing／decreasing，本模組文件寫 up／down。
 * 不正規化的話，「方向與預期相反加分」那條規則永遠不會觸發——
 * 而且不會報錯，只會安靜地讓逆勢的發現少拿 0.15 分。
 */
function normDirection(f) {
	const d = f?.direction;
	if (!isStr(d)) return null;
	if (/^(up|increasing|rising|increase)$/i.test(d)) return "up";
	if (/^(down|decreasing|falling|decrease)$/i.test(d)) return "down";
	if (/^(flat|stable|none)$/i.test(d)) return "flat";
	return null;
}

/** 取第一個存在的數值欄位。給「同一個量有兩套欄位名」的情況用。 */
function pick(f, ...keys) {
	for (const k of keys) {
		if (f && isNum(f[k])) return f[k];
	}
	return null;
}

/** FNV-1a。只是要一個穩定的短 id——同樣的輸入永遠得到同樣的 insight_id。 */
function hash(str) {
	let h = 0x811c9dc5;
	for (let i = 0; i < str.length; i++) {
		h ^= str.charCodeAt(i);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	return h.toString(36).padStart(7, "0").slice(0, 7);
}

/** 時間範圍正規化成一個字串（validateArtifact 要求 scope.time 是非空字串）。 */
function normalizeTimeRange(timeRange, candidate) {
	if (isStr(timeRange)) return timeRange;
	if (Array.isArray(timeRange) && timeRange.length >= 2) return `${timeRange[0]}-${timeRange[timeRange.length - 1]}`;
	if (timeRange && isNum(timeRange.start) && isNum(timeRange.end)) return `${timeRange.start}-${timeRange.end}`;
	if (timeRange && isStr(timeRange.start) && isStr(timeRange.end)) return `${timeRange.start}-${timeRange.end}`;
	const xr = candidate?.evidence?.x_range;
	if (Array.isArray(xr) && xr.length >= 2) return `${xr[0]}-${xr[xr.length - 1]}`;
	return "";
}

// ─────────────────────────────────────────────────────────────
// 1. 閘門（硬拒絕）
// ─────────────────────────────────────────────────────────────

/**
 * 把呼叫端給的東西整理成閘門與評分共用的 ctx。
 * metricDef 允許傳單一物件或陣列——跨資料集比較時要能看到兩邊的定義，
 * 不然「比較的是不是同一件事」根本無從檢查。
 */
function toContext(input, opts = {}) {
	const metricDefs = Array.isArray(input.metricDef)
		? input.metricDef.filter(Boolean)
		: (input.metricDef ? [input.metricDef] : []);
	const datasetIds = Array.isArray(input.datasetIds) ? input.datasetIds.filter(isStr) : [];
	const compareAgeScopes = Array.isArray(input.compareAgeScopes) ? input.compareAgeScopes.filter(Boolean) : [];
	return {
		// 標記已正規化。scoreInsight 可能收到原始 input 或已經整理過的 ctx，
		// 沒有這個旗標就會把 ctx 再正規化一次——第二次讀不到 input.metricDef，
		// metricDefs 會靜靜地變成空陣列，Q 分數莫名其妙少 0.1。
		_ctx: true,
		candidate: input.candidate,
		ageScope: input.ageScope,
		metricDefs,
		datasetIds,
		compareAgeScopes,
		sql: input.sql,
		queryId: input.queryId,
		geography: input.geography,
		timeRange: input.timeRange,
		opts: {
			effectFloor: isNum(opts.effectFloor) ? opts.effectFloor : EFFECT_SIZE_FLOOR,
			minPoints: opts.minPoints || MIN_POINTS_BY_TYPE,
			minStatisticalStrength: isNum(opts.minStatisticalStrength) ? opts.minStatisticalStrength : 0,
			weights: opts.weights || SCORE_WEIGHTS,
		},
	};
}

/**
 * 閘門清單。**順序是契約的一部分**：
 * 先擋「定義與資料」（結構性的、便宜的、錯了連統計都不該算），
 * 再擋「統計」（要用到 effect size / strength）。
 * funnel 報「通過定義與資料檢查 M 個 → 通過統計檢查 K 個」就是照這個分段數的，
 * 順序一改，那兩個數字的意思就變了。
 */
export const GATES = Object.freeze([
	{
		name: "candidate_shape",
		stage: "definition",
		label: "候選格式",
		// 形狀不對就往下跑，會在後面某個地方拿到 undefined 然後靜靜地算出 NaN。
		// NaN 一路傳到報告就是「下降 NaN%」——寧可在第一關大聲失敗。
		check(ctx) {
			const r = validateCandidate(ctx.candidate);
			if (r.ok) return null;
			return { reason: `候選不符合 InsightCandidate 契約：${r.errors.join("；")}` };
		},
	},
	{
		name: "age_scope",
		stage: "definition",
		label: "年齡範圍",
		// D 級＝組距超出 15-40 可用邊界。這種資料當然也能算出趨勢，
		// 但那個趨勢不是「青年」的趨勢。放行的話，報告會用青年政策的語言
		// 描述一群不是青年的人——這是本專案最不能犯的錯。
		check(ctx) {
			const r = validateAgeScope(ctx.ageScope);
			if (!r.ok) return { reason: `年齡範圍不合法：${r.errors.join("；")}` };
			const cls = ctx.ageScope.classification;
			if (cls === AGE_CLASS.D.code) {
				return {
					reason: `年齡分級為 D（${AGE_CLASS.D.desc}）：${ctx.ageScope.label} 超出可用邊界，`
						+ `不得作為青年（${POLICY_AGE_LABEL} 歲）分析`,
				};
			}
			return null;
		},
	},
	{
		name: "metric_compatibility",
		stage: "definition",
		label: "指標定義相容",
		// 兩張表都有一個叫「人口」的欄位，不代表兩邊算的是同一件事：
		// 一邊是人數（可加總）、一邊是佔比（不可加總），擺在同一句結論裡
		// 得到的數字看起來很合理，但它不對應任何真實的量。
		// 跨資料集卻拿不出兩邊的定義時也一樣擋掉——無法證明相容 ≠ 相容。
		check(ctx) {
			if (ctx.datasetIds.length <= 1) return null;
			if (ctx.metricDefs.length < 2) {
				return {
					reason: `比較了 ${ctx.datasetIds.length} 個資料集（${ctx.datasetIds.join("、")}），`
						+ `但只拿到 ${ctx.metricDefs.length} 份指標定義，無法證明兩邊算的是同一個量`,
				};
			}
			const base = ctx.metricDefs[0];
			for (let i = 1; i < ctx.metricDefs.length; i++) {
				const m = ctx.metricDefs[i];
				if (m.type !== base.type) {
					return { reason: `指標型別不相容：${base.label || base.column} 是 ${base.type}，${m.label || m.column} 是 ${m.type}` };
				}
				if (m.aggregation !== base.aggregation) {
					return { reason: `聚合規則不相容：${base.label || base.column} 是 ${base.aggregation}，${m.label || m.column} 是 ${m.aggregation}` };
				}
				if ((m.unit || "") !== (base.unit || "")) {
					return { reason: `單位不相容：${base.unit || "（無）"} 對 ${m.unit || "（無）"}` };
				}
				if (base.aggregation === AGGREGATION_RULE.REQUIRE_DENOMINATOR
					&& (m.denominator || "") !== (base.denominator || "")) {
					return { reason: `比率的分母不同（${base.denominator || "未指定"} 對 ${m.denominator || "未指定"}），兩個百分比不可互相比較` };
				}
			}
			return null;
		},
	},
	{
		name: "evidence_reproducible",
		stage: "definition",
		label: "證據可重現",
		// blueprint §10.1：沒有 SQL 就無法重現，無法重現的結論不准進報告。
		// 這關不是形式主義——被追問「這個數字哪來的」時，答案必須是一段能再跑一次的查詢。
		check(ctx) {
			if (!isStr(ctx.sql)) return { reason: "缺少可重現的 SQL，結論無法被第三方驗算" };
			if (!/select/i.test(ctx.sql)) return { reason: "evidence.sql 不是一段查詢，無法重現" };
			if (ctx.datasetIds.length === 0) return { reason: "缺少 dataset_ids，無法指出資料來源" };
			return null;
		},
	},
	{
		name: "min_data_points",
		stage: "definition",
		label: "資料點數",
		// 點數不足時統計量照樣算得出來，而且往往特別好看
		// （兩點的線性趨勢 R² 永遠是 1）。這關擋的就是這種假的漂亮。
		check(ctx) {
			const need = ctx.opts.minPoints[ctx.candidate.type] ?? 3;
			const n = ctx.candidate?.evidence?.n;
			if (!isNum(n)) return { reason: `evidence.n 未提供，無法確認資料點數是否足夠（${ctx.candidate.type} 需要 ${need} 點）` };
			if (n < need) return { reason: `資料點僅 ${n} 個，少於 ${ctx.candidate.type} 所需的 ${need} 個` };
			return null;
		},
	},
	{
		name: "effect_size_floor",
		stage: "statistical",
		label: "效果量門檻",
		// 統計顯著不等於重要。樣本夠大時，0.3% 的差異也會「顯著」。
		// 政策報告要的是值得行動的幅度，所以效果量先過門檻，才談 p 值。
		check(ctx) {
			const floor = ctx.opts.effectFloor;
			const e = ctx.candidate.effect_size;
			if (e < floor) {
				return { reason: `效果量 ${e.toFixed(3)} 低於門檻 ${floor}，只是微小波動，不構成發現` };
			}
			const s = ctx.candidate.statistical_strength;
			if (s < ctx.opts.minStatisticalStrength) {
				return { reason: `統計強度 ${s.toFixed(3)} 低於門檻 ${ctx.opts.minStatisticalStrength}` };
			}
			return null;
		},
	},
]);

/**
 * 依序跑完所有閘門。
 * @returns {{ok:true, ctx:object}|{ok:false, gate:string, stage:string, reason:string, ctx:object}}
 */
export function runGates(input, opts = {}) {
	const ctx = toContext(input, opts);
	for (const gate of GATES) {
		let hit = null;
		try {
			hit = gate.check(ctx);
		} catch (err) {
			// 閘門自己炸掉也算沒過。讓例外靜靜地把候選放行，等於整條防線失效。
			hit = { reason: `閘門執行失敗：${err.message}` };
		}
		if (hit) {
			return { ok: false, gate: gate.name, stage: gate.stage, label: gate.label, reason: hit.reason, ctx };
		}
	}
	return { ok: true, ctx };
}

// ─────────────────────────────────────────────────────────────
// 2. 評分（只有全數通過閘門的候選才會走到這裡）
// ─────────────────────────────────────────────────────────────

/**
 * InsightScore = wE·E + wS·S + wN·N + wP·P + wQ·Q
 *
 * 權重集中在這一個常數，而不是散在程式各處的魔術數字：
 * 「為什麼這則排第一」在現場一定會被問，答案要能指著這五個數字講完。
 * 要調整優先序就改這裡（或呼叫時傳 opts.weights），不要去改計算式。
 */
export const SCORE_WEIGHTS = Object.freeze({ E: 0.30, S: 0.25, N: 0.15, P: 0.20, Q: 0.10 });

/**
 * N：各類型的基礎新奇度。
 * 依據很單純——趨勢是大家本來就預期會看到的東西；
 * 轉折、異常、排名翻轉則是「與既有預期不同」，這才是資訊量所在。
 */
export const NOVELTY_BY_TYPE = Object.freeze({
	change_point: 0.90,
	outlier: 0.85,
	rank_change: 0.75,
	group_difference: 0.60,
	trend: 0.45,
});

/**
 * 已知常識清單：命中就扣新奇度。
 * 「台灣人口老化」不需要一份 AI 報告來告訴決策者。
 * 這是人工維護的表，不是模型判斷——加一條就等於宣告「這件事我們已經知道了」。
 */
export const KNOWN_PATTERNS = Object.freeze([
	{ match: /老化指數|老年人口|高齡/, types: ["trend"], penalty: 0.20, why: "人口老化是公共常識" },
	{ match: /總人口/, types: ["trend"], penalty: 0.10, why: "總人口趨勢已被大量既有報告覆蓋" },
]);

/**
 * 指標的「預期方向」。逆勢才是新聞：
 * 青年人口在全國性下降的背景下，某個區反而上升，資訊量遠高於再降一次。
 * 誠實說：這張表是人工設定的先驗，不是從資料學出來的。
 */
export const EXPECTED_DIRECTION = Object.freeze([
	{ match: /青年|15-19|20-24|25-29|30-34|35-39/, direction: "down" },
	{ match: /老化指數|老年人口|扶養比/, direction: "up" },
]);

/**
 * P：政策相關度對照表。
 *
 * 誠實揭露：**這是人工設定的啟發式**，不是從政策文件量化出來的權重，
 * 也沒有經過任何驗證。它存在的意義只是「把主觀判斷攤在明處」——
 * 要改變報告的政策優先序，請改這張表，不要去改評分函式。
 * 評審若不同意這個排序，他要能一眼看到自己在不同意什麼。
 */
export const POLICY_RELEVANT = Object.freeze([
	{ match: /青年|15-19|20-24|25-29|30-34|35-39/, domain: "青年人口", weight: 1.00 },
	{ match: /扶養比|老化指數|工作年齡|15-64/, domain: "人口結構與扶養負擔", weight: 0.80 },
	{ match: /遷入|遷出|社會增加|住宅|租金|房價/, domain: "居住與人口移動", weight: 0.85 },
	{ match: /就業|失業|薪資|勞動/, domain: "青年就業", weight: 0.85 },
	{ match: /總人口/, domain: "人口總量", weight: 0.55 },
]);

/** 沒有命中政策表時的底分。不給 0——沒被列進表不代表無關，只代表沒被優先。 */
const POLICY_BASELINE = 0.35;

function matchText(ctx) {
	// 比對用的文字：指標標籤 + 欄位名 + 年齡標籤。
	// 用 percent9 這種沒有語意的欄位名比對永遠不會中，所以 label 必須參與。
	return [
		ctx.candidate?.metric_label,
		ctx.candidate?.metric,
		ctx.metricDefs?.[0]?.label,
		ctx.ageScope?.label,
	].filter(isStr).join(" ");
}

/** N：新奇度／意外性。決定性規則，不呼叫任何模型。 */
function scoreNovelty(ctx) {
	const reasons = [];
	let n = NOVELTY_BY_TYPE[ctx.candidate.type] ?? 0.5;
	reasons.push(`類型 ${ctx.candidate.type} 基礎新奇度 ${n.toFixed(2)}`);

	const text = matchText(ctx);
	for (const k of KNOWN_PATTERNS) {
		if (k.match.test(text) && k.types.includes(ctx.candidate.type)) {
			n -= k.penalty;
			reasons.push(`已知常識扣分 -${k.penalty.toFixed(2)}（${k.why}）`);
		}
	}

	// 用正規化後的方向，否則 mining 的 "decreasing" 永遠對不上表裡的 "down"，
	// 逆勢的發現會安靜地少拿這 0.15 分。
	const dir = normDirection(ctx.candidate?.facts);
	if (isStr(dir)) {
		const exp = EXPECTED_DIRECTION.find((d) => d.match.test(text));
		if (exp && exp.direction !== dir) {
			n += 0.15;
			reasons.push(`方向與預期相反（預期 ${exp.direction}、實際 ${dir}）+0.15`);
		}
	}
	return { value: clamp01(n), reasons };
}

/** P：政策相關度。同樣是查表，不是猜。 */
function scorePolicy(ctx) {
	const reasons = [];
	const text = matchText(ctx);
	const hit = POLICY_RELEVANT.find((p) => p.match.test(text));
	let p = hit ? hit.weight : POLICY_BASELINE;
	reasons.push(hit
		? `命中政策領域「${hit.domain}」，權重 ${hit.weight.toFixed(2)}（人工設定）`
		: `未命中政策對照表，給底分 ${POLICY_BASELINE.toFixed(2)}`);

	// A/B 級代表涵蓋（或近似涵蓋）完整法定青年，結論可以直接對應青年政策；
	// C 級只是其中一段，可行動性天生較低。
	const cls = ctx.ageScope?.classification;
	if (cls === "A" || cls === "B") {
		p += 0.10;
		reasons.push(`年齡分級 ${cls}，涵蓋完整法定青年 ${POLICY_AGE_LABEL} +0.10`);
	}
	return { value: clamp01(p), reasons };
}

/** Q：資料品質。全部從 evidence 與定義推出來，沒有一項需要人主觀評斷。 */
function scoreQuality(ctx) {
	const reasons = [];
	let q = 1.0;

	const need = ctx.opts.minPoints[ctx.candidate.type] ?? 3;
	const n = ctx.candidate?.evidence?.n ?? 0;
	if (n < need * 2) {
		q -= 0.15;
		reasons.push(`資料點 ${n} 個，僅勉強達到 ${ctx.candidate.type} 的最低需求 ${need} -0.15`);
	}

	const cls = ctx.ageScope?.classification;
	if (cls === "B") { q -= 0.05; reasons.push("年齡分級 B（近似而非精確）-0.05"); }
	if (cls === "C") { q -= 0.20; reasons.push("年齡分級 C（僅涵蓋青年子區段）-0.20"); }

	const def = ctx.metricDefs[0];
	if (def?.aggregation === AGGREGATION_RULE.REQUIRE_DENOMINATOR && !isStr(def.denominator)) {
		q -= 0.20;
		reasons.push("比率指標但未指定分母，跨組距合併的正確性無法保證 -0.20");
	}
	if (def?.aggregation === AGGREGATION_RULE.NOT_AGGREGATABLE) {
		q -= 0.20;
		reasons.push("指標本質不可合併（中位數／指數）-0.20");
	}
	if (!def) {
		q -= 0.10;
		reasons.push("未提供 MetricDefinition，無法檢查聚合規則 -0.10");
	}

	if (hasAgeMismatch(ctx)) {
		q -= 0.15;
		reasons.push("跨資料集年齡範圍不一致 -0.15");
	}
	return { value: clamp01(q), reasons };
}

/** 兩份資料的年齡範圍是否不同——影響 Q、限制說明與報告的敘述強度。 */
function hasAgeMismatch(ctx) {
	if (!ctx.compareAgeScopes || ctx.compareAgeScopes.length === 0) return false;
	const base = ctx.ageScope?.label;
	return ctx.compareAgeScopes.some((s) => s && s.label !== base);
}

/**
 * 算分。回傳 0..100 與完整拆解——拆解要能直接印在報告裡，
 * 「87 分是怎麼來的」必須有答案。
 */
export function scoreInsight(input, opts = {}) {
	const ctx = input && input._ctx ? input : toContext(input, opts);
	const weights = opts.weights || ctx.opts?.weights || SCORE_WEIGHTS;

	const sum = weights.E + weights.S + weights.N + weights.P + weights.Q;
	// 權重不合 1 的話分數就不再是 0..100，報告上的「87 / 100」會變成謊話。
	if (!isNum(sum) || Math.abs(sum - 1) > 1e-9) {
		throw new Error(`SCORE_WEIGHTS 五項加總必須為 1，收到 ${sum}`);
	}

	const E = clamp01(ctx.candidate.effect_size);
	const S = clamp01(ctx.candidate.statistical_strength);
	const N = scoreNovelty(ctx);
	const P = scorePolicy(ctx);
	const Q = scoreQuality(ctx);

	const raw = weights.E * E + weights.S * S + weights.N * N.value + weights.P * P.value + weights.Q * Q.value;
	return {
		score: Math.round(raw * 100),
		components: { E, S, N: N.value, P: P.value, Q: Q.value },
		weights: { ...weights },
		reasons: {
			E: [`效果量 ${E.toFixed(3)}（由 mining 產出，跨類型可比較）`],
			S: [`統計強度 ${S.toFixed(3)}（由 mining 產出）`],
			N: N.reasons,
			P: P.reasons,
			Q: Q.reasons,
		},
	};
}

// ─────────────────────────────────────────────────────────────
// 3. 敘述生成（三個語域：事實／判讀／假設）
// ─────────────────────────────────────────────────────────────

/**
 * 可選的假設庫。
 *
 * 假設**只能**從這張人工維護的表取得，或由呼叫端明確傳入——
 * 絕不由程式從數字「推論」出一個原因。表裡每一句都必須自帶保留語氣
 * （可能／仍需驗證），因為它們本來就沒有被這份資料驗證過。
 */
export const HYPOTHESIS_LIBRARY = Object.freeze([
	{ match: /青年|20-24|25-29|30-34/, text: "可能與居住成本、就業機會分布有關，仍需其他資料驗證。" },
	{ match: /老化指數|老年人口/, text: "可能與生育率及平均餘命變化有關，仍需其他資料驗證。" },
]);

/** facts 裡的時間範圍（優先用 facts，其次用 evidence.x_range）。 */
function factRange(f, candidate) {
	// first_x/last_x 是 mining.js 的欄位名，start_x/end_x 是本模組宣告的別名。
	const a = f?.first_x ?? f?.start_x;
	const b = f?.last_x ?? f?.end_x;
	if ((isNum(a) && isNum(b)) || (isStr(a) && isStr(b))) return `${a}-${b}`;
	const xr = candidate?.evidence?.x_range;
	if (Array.isArray(xr) && xr.length >= 2) return `${xr[0]}-${xr[xr.length - 1]}`;
	return null;
}

const DIR_ZH = { up: "上升", down: "下降", flat: "持平" };

/**
 * 從 facts 生出敘述。
 *
 * 兩條硬規則：
 *   1. 只講 facts 裡真的有的數字。任何欄位缺了就少講一句，不補、不估、不四捨五入到「大約」。
 *   2. 不講原因。這裡產出的每一句都必須能直接對回一個 facts 欄位。
 * 這就是為什麼每個 template 都在 push 之前檢查 isNum——
 * 少了這些檢查，缺漏的欄位會變成「下降 NaN%」或「下降 undefined%」進到報告。
 */
function buildNarrative(ctx, metricLabel, ageLabel) {
	const c = ctx.candidate;
	const f = c.facts || {};
	const facts = [];
	let insight = "";

	const range = factRange(f, c);
	const cp = changePctOf(f);
	const dir = DIR_ZH[normDirection(f)] || null;
	// 斜率是「每單位 x」的量，x 對本專案的時間序列一律是年份。
	const unit = isStr(c.evidence?.unit) ? c.evidence.unit : "";
	// 整個 fact block 共用同一個量級與同一種數字格式。
	// level 只用來決定「怎麼印」與「這個變化率是否可忽略」，本身不會被印出來。
	const level = pick(f, "mean_y", "mean_before", "grand_mean", "median_value",
		"expected", "value", "first_y", "high_value", "value_first");
	const dec = decimalsFor(level);
	const num = (v) => thousands(v, dec);
	const strength = c.statistical_strength;
	// base 要傳「這個變化率所依附的那一段的水準」，不能一律用 block level：
	// 轉折前後的基期不同，用錯基期就會把可忽略判成顯著、或反過來。
	const perYear = (v, base) => describeRate(v, base ?? level, dec, unit, strength);

	if (c.type === INSIGHT_TYPE.trend) {
		const y0 = pick(f, "first_y", "start_y");
		const y1 = pick(f, "last_y", "end_y");
		if (dir && range && isNum(cp)) facts.push(`${ageLabel} 歲${metricLabel}於 ${range} 年${dir} ${pct(cp)}。`);
		else if (dir && range) facts.push(`${ageLabel} 歲${metricLabel}於 ${range} 年呈${dir}。`);
		if (isNum(y0)) facts.push(`起點值：${num(y0)}${unit}`);
		if (isNum(y1)) facts.push(`終點值：${num(y1)}${unit}`);
		if (isNum(f.slope)) facts.push(`平均變化：${perYear(f.slope, pick(f, "mean_y", "first_y", "last_y"))}`);
		if (isNum(f.r2)) facts.push(`線性配適度 R²：${f.r2.toFixed(3)}`);
		insight = `變化幅度在可比較的洞察類型中屬於${effectBand(c.effect_size)}效果（效果量 ${c.effect_size.toFixed(2)}）。`;
	} else if (c.type === INSIGHT_TYPE.change_point) {
		const at = f.year ?? f.change_year ?? f.change_x ?? null;
		const sb = pick(f, "slope_before", "before_slope");
		const sa = pick(f, "slope_after", "after_slope");
		if (at !== null && isNum(cp) && dir) facts.push(`${ageLabel} 歲${metricLabel}於 ${at} 年前後轉變，整段期間${dir} ${pct(cp)}。`);
		else if (at !== null) facts.push(`${ageLabel} 歲${metricLabel}的變化在 ${at} 年出現轉折。`);
		if (isNum(sb)) facts.push(`轉折前：${perYear(sb, pick(f, "mean_before", "mean_y", "first_y"))}`);
		if (isNum(sa)) facts.push(`轉折後：${perYear(sa, pick(f, "mean_after", "mean_y", "last_y"))}`);
		if (isNum(f.before_change_pct)) facts.push(`轉折前變化：${describeRatePct(f.before_change_pct, strength)}`);
		if (isNum(f.after_change_pct)) facts.push(`轉折後變化：${describeRatePct(f.after_change_pct, strength)}`);
		if (isNum(f.mean_before) && isNum(f.mean_after)) {
			facts.push(`轉折前後平均：${num(f.mean_before)}${unit} → ${num(f.mean_after)}${unit}`);
		}
		insight = describeChangePoint(f, strength);
	} else if (c.type === INSIGHT_TYPE.group_difference) {
		const hiName = f.high_label || f.high_key;
		const loName = f.low_label || f.low_key;
		if (isStr(hiName) && isStr(loName) && isNum(f.high_value) && isNum(f.low_value)) {
			facts.push(`${metricLabel}最高為${hiName}（${num(f.high_value)}${unit}），最低為${loName}（${num(f.low_value)}${unit}）。`);
		}
		if (isNum(f.gap)) facts.push(`兩者差距：${num(f.gap)}${unit}`);
		if (isNum(f.ratio)) facts.push(`倍數：${f.ratio.toFixed(2)} 倍`);
		// rel_gap 是 差距 ÷ 兩端絕對值總和，**不是**一般認知的「差了幾 %」。
		// 標籤必須把算式講出來，否則讀者會拿它當成成長率來理解。
		if (isNum(f.rel_gap)) facts.push(`相對差距（差距 ÷ 兩端總和）：${pct(f.rel_gap * 100)}`);
		else if (isNum(f.gap_pct)) facts.push(`兩者差距：${pct(f.gap_pct)}`);
		if (isNum(f.eta_squared)) facts.push(`組間變異占比 η²：${f.eta_squared.toFixed(3)}`);
		insight = `區域之間的差距達到${effectBand(c.effect_size)}程度，分布並不均勻。`;
	} else if (c.type === INSIGHT_TYPE.rank_change) {
		const name = f.label || f.key;
		const r0 = pick(f, "rank_first", "from_rank");
		const r1 = pick(f, "rank_last", "to_rank");
		const x0 = f.x_first ?? f.from_x;
		const x1 = f.x_last ?? f.to_x;
		if (isStr(name) && isNum(r0) && isNum(r1)) {
			const move = r0 > r1 ? "上升" : "下降";
			const span = x0 !== undefined && x1 !== undefined ? `${x0}-${x1} 年間` : "";
			facts.push(`${name}在${span}${metricLabel}排名由第 ${r0} 名${move}至第 ${r1} 名。`);
			facts.push(`名次變動：${Math.abs(r0 - r1)} 名`);
		}
		if (isNum(f.value_first) && isNum(f.value_last)) {
			facts.push(`數值變化：${num(f.value_first)}${unit} → ${num(f.value_last)}${unit}`);
		}
		if (Array.isArray(f.crossed_keys) && f.crossed_keys.length) {
			facts.push(`實際被越過的對象：${f.crossed_keys.join("、")}`);
		}
		insight = `名次變動幅度屬於${effectBand(c.effect_size)}程度，相對位置已經改變。`;
	} else if (c.type === INSIGHT_TYPE.outlier) {
		const base = pick(f, "expected", "baseline", "median_value");
		if (isNum(f.value)) {
			const who = isStr(f.label) ? f.label : (isStr(f.key) ? f.key : `${ageLabel} 歲${metricLabel}`);
			const at = f.x !== undefined ? `（${f.x}）` : "";
			facts.push(`${who}${at}的${metricLabel}為 ${num(f.value)}${unit}。`);
		}
		if (isNum(base)) facts.push(`同期基準值：${num(base)}${unit}`);
		// 偏離量是「一次性的差距」而不是每期變化率，方向由 robust_z 佐證，
		// 不套用 flat 判斷；但格式仍與同一區塊一致。
		if (isNum(f.deviation)) facts.push(`偏離基準：${signedNum(f.deviation, dec)}${unit}`);
		if (isNum(f.deviation_pct)) facts.push(`偏離基準：${signedPct(f.deviation_pct)}`);
		if (isNum(f.robust_z)) facts.push(`穩健 z 值：${f.robust_z.toFixed(2)}`);
		insight = `該值偏離整體分布達${effectBand(c.effect_size)}程度，屬於分布中的極端點。`;
	}

	// 一個 facts 都生不出來，代表 mining 給的欄位與這裡的契約對不上。
	// 這時候硬擠一句通則式的敘述最危險——看起來像結論，其實沒有任何數字支撐。
	if (facts.length === 0) {
		facts.push(`${ageLabel} 歲${metricLabel}偵測到 ${TYPE_LABEL_ZH[c.type] || c.type}（facts 未提供可敘述的數值）。`);
	}
	return { facts, insight };
}

/** 效果量分級。門檻寫在同一個地方，報告裡的「明顯／中等」才會前後一致。 */
function effectBand(e) {
	if (e >= 0.6) return "明顯";
	if (e >= 0.3) return "中等";
	return "輕微";
}

/**
 * 轉折的判讀：比較前後段的變化速度。只描述形狀，不說原因。
 *
 * 這裡同樣要套用「可忽略就不宣稱方向」。否則新莊區那種
 * 轉折前 +79/年（基期 171,342，＝0.046%）、轉折後 -3,861/年 的情況，
 * 會被寫成「變化方向在該時點前後反轉」——那句話宣稱 2013 年之前人口在成長，
 * 但那個 + 號只是最小平方法在一段上下擺盪的資料上的產物，換個估計方法就變號。
 * 正確的說法是「由接近持平轉為下降」。
 */
function describeChangePoint(f, strength) {
	const band = flatBand(strength);
	// 回傳 {value, flat}：flat 代表這一段的變化率相對基期小到不該宣稱方向。
	const side = (slopeKeys, pctKey, baseKeys) => {
		const s = pick(f, ...slopeKeys);
		if (isNum(s)) {
			const base = pick(f, ...baseKeys);
			const rel = isNum(base) && Math.abs(base) > 0 ? Math.abs(s) / Math.abs(base) : null;
			return { value: s, flat: rel !== null && rel < band };
		}
		const p = pick(f, pctKey);
		// *_change_pct 本身就是相對量，直接跟帶寬（換算成百分點）比。
		if (isNum(p)) return { value: p, flat: Math.abs(p) < band * 100 };
		return null;
	};

	const b = side(["slope_before", "before_slope"], "before_change_pct", ["mean_before", "mean_y", "first_y"]);
	const a = side(["slope_after", "after_slope"], "after_change_pct", ["mean_after", "mean_y", "last_y"]);
	if (!a || !b) return "資料在該時點前後的變化型態不同，形成趨勢轉折。";

	const word = (v) => (v > 0 ? "上升" : "下降");
	if (b.flat && a.flat) return "前後兩段都接近持平，轉折幅度有限。";
	if (b.flat) return `由接近持平轉為${word(a.value)}，形成趨勢轉折。`;
	if (a.flat) return `由${word(b.value)}轉為接近持平，形成趨勢轉折。`;
	if (Math.sign(a.value) !== Math.sign(b.value)) return "變化方向在該時點前後反轉，形成趨勢轉折。";
	if (Math.abs(a.value) > Math.abs(b.value)) return "變化速度較前期明顯加快，形成趨勢轉折。";
	return "變化速度較前期明顯趨緩，形成趨勢轉折。";
}

// ─────────────────────────────────────────────────────────────
// 4. InsightArtifact
// ─────────────────────────────────────────────────────────────

/**
 * 建立 InsightArtifact（blueprint §11.2）。
 *
 * 這個物件是**唯一真相來源**：Markdown 報告與儀表板圖表都只從它 render，
 * 所以兩邊不可能對不上。任何「報告寫 6.2%、圖上是 5.8%」的情況，
 * 在架構上就不會發生——因為沒有第二個地方可以算數字。
 *
 * 通不過閘門的候選會直接 throw：回傳一個 status=rejected 的 artifact
 * 等於把一顆未爆彈交給下游，只要有人忘記檢查 status 就會印出去。
 *
 * @param {object} args
 * @param {object} args.candidate        InsightCandidate
 * @param {object} args.ageScope         AgeScope
 * @param {string} args.geography        例如 "新北市"
 * @param {string|object} args.timeRange
 * @param {string[]} args.datasetIds
 * @param {string} args.sql              實際跑過、可重現的查詢
 * @param {string} [args.queryId]
 * @param {object|object[]} [args.metricDef]
 * @param {object[]} [args.compareAgeScopes] 跨資料集比較時，另一邊的 AgeScope
 * @param {string|string[]} [args.method]
 * @param {string} [args.hypothesis]     未經驗證的推測，必須自帶保留語氣
 * @param {string[]} [args.sourceUrls]
 * @param {string} [args.snapshotAt]     ISO 時間；不給則用當下時間
 * @param {string[]} [args.extraLimitations]
 * @returns {object} InsightArtifact
 */
export function buildInsightArtifact(args, opts = {}) {
	const gate = runGates(args, opts);
	if (!gate.ok) {
		throw new Error(`候選未通過閘門 ${gate.gate}（${gate.label}）：${gate.reason}`);
	}
	const ctx = gate.ctx;
	const c = ctx.candidate;
	const scope = ctx.ageScope;

	const geography = isStr(args.geography) ? args.geography : "";
	if (!geography) throw new Error("缺 geography——報告不能出現一個不知道在講哪裡的結論");
	const time = normalizeTimeRange(args.timeRange, c);
	if (!time) throw new Error("缺 timeRange，且無法從 evidence.x_range 推得");

	const metricLabel = c.metric_label || ctx.metricDefs[0]?.label || c.metric;
	const ageLabel = scope.label || `${scope.lower}-${scope.upper}`;
	const mismatch = hasAgeMismatch(ctx);

	const { facts, insight } = buildNarrative(ctx, metricLabel, ageLabel);

	// claim 一律由 facts 的第一句組成，再加上地區——
	// 它不是另外寫的一句話，而是同一組數字的門面。這樣「標題與內文不符」不可能發生。
	const claim = `${geography} ${facts[0]}`;
	const causal = findCausalMarker(claim);
	if (causal) {
		// 走到這裡代表 template 被改壞了。與其印出一句因果結論，不如當場失敗。
		throw new Error(`claim 含因果語氣「${causal}」：資料只能證明同時發生，不能證明原因`);
	}

	const score = scoreInsight(ctx, { ...opts, weights: opts.weights || ctx.opts.weights });
	const warnings = [];
	if (mismatch) warnings.push("跨資料集的年齡範圍不一致，敘述強度已降級為族群層級");
	if (scope.classification === "C") warnings.push(`僅涵蓋青年子區段 ${ageLabel}`);

	const limitations = buildLimitations({ ctx, scope, ageLabel, mismatch, extra: args.extraLimitations });

	const methods = normalizeMethods(args.method, c.type);
	const snapshotAt = isStr(args.snapshotAt) ? args.snapshotAt : new Date().toISOString();

	// id 由內容決定（不含時間），同一組輸入永遠得到同一個 id，
	// 報告重跑時的 diff 才看得出「哪一則變了」而不是整份都變。
	const insightId = `ins_${hash([c.type, c.metric, ageLabel, time, geography, ctx.datasetIds.join("|")].join("::"))}`;

	const artifact = {
		insight_id: insightId,
		title: `${ageLabel} 歲${metricLabel}${TYPE_LABEL_ZH[c.type] || ""}`,
		claim,
		insight_type: c.type,
		scope: {
			// 法定定義永遠不會為了遷就資料而被改寫。這是本系統的立場，不是預設值。
			policy_age: POLICY_AGE_LABEL,
			actual_age: ageLabel,
			age_classification: scope.classification,
			time,
			geography,
		},
		// 三個語域分開存。之所以是三個欄位而不是一段文字，
		// 是因為「假設不能用事實的語氣講」這件事必須在結構上成立，
		// 不能寄望每次措辭都剛好夠謹慎。
		narrative: {
			data_fact: facts,
			analytical_insight: insight,
			hypothesis: resolveHypothesis(args.hypothesis, metricLabel, ageLabel),
		},
		// 兩邊年齡範圍不同時，敘述強度降級：只能說「同步現象」，不能說兩者互相解釋。
		claim_strength: mismatch ? "group_level" : "full",
		evidence: {
			query_id: isStr(args.queryId) ? args.queryId : `q_${hash(ctx.sql)}`,
			sql: ctx.sql,
			dataset_ids: ctx.datasetIds,
			source_urls: Array.isArray(args.sourceUrls) ? args.sourceUrls.filter(isStr) : [],
			method: methods.join(" + "),
			methods,
			effect_size: c.effect_size,
			statistical_strength: c.statistical_strength,
			facts: c.facts,
			n: c.evidence?.n ?? null,
			snapshot_at: snapshotAt,
		},
		validation: {
			status: "validated",
			score: score.score,
			warnings,
			score_breakdown: score,
		},
		limitations,
		chart_specs: buildChartSpecs(ctx, metricLabel, ageLabel),
	};

	// 最後一道自我檢查：我們自己產的東西也要過 schema。
	// 少了這行，日後改了欄位名會在 render 階段才爆，而且是以「少一段」的形式安靜地爆。
	const v = validateArtifact(artifact);
	if (!v.ok) throw new Error(`產出的 artifact 不符合契約：${v.errors.join("；")}`);
	return artifact;
}

/** 假設只能來自呼叫端或人工維護的表。程式不從數字推原因。 */
function resolveHypothesis(given, metricLabel, ageLabel) {
	if (isStr(given)) return given;
	const text = `${metricLabel} ${ageLabel}`;
	const hit = HYPOTHESIS_LIBRARY.find((h) => h.match.test(text));
	return hit ? hit.text : null;
}

function normalizeMethods(method, type) {
	if (Array.isArray(method) && method.length) return method.filter(isStr);
	if (isStr(method)) return [method];
	return METHOD_BY_TYPE[type] || ["Statistical Detection"];
}

/**
 * 限制說明。
 *
 * 非 A 級一定要有內容——validateArtifact 會擋。這條規則的實質意義是：
 * 「25-34 歲人口下降」與「青年人口下降」是兩句不同的話，
 * 報告若只講前者卻讓人讀成後者，就等於用資料證明了一件它沒有證明的事。
 */
function buildLimitations({ ctx, scope, ageLabel, mismatch, extra }) {
	const out = [];
	const cls = scope.classification;

	if (cls === "C") {
		out.push(`本結果僅代表 ${ageLabel} 歲青年區段，不代表完整 ${POLICY_AGE_LABEL} 歲。`);
	} else if (cls === "B") {
		out.push(`本結果以 ${ageLabel} 歲組距近似法定青年 ${POLICY_AGE_LABEL} 歲，兩者範圍不完全相同。`);
	}
	if (cls !== "A" && out.length === 0) {
		// 走到這裡代表分級是我們沒預期到的值。寧可留一句籠統的限制，也不要留空白。
		out.push(`本結果的年齡範圍為 ${ageLabel} 歲，與法定青年 ${POLICY_AGE_LABEL} 歲不完全相同。`);
	}

	if (mismatch) {
		out.push("兩資料年齡範圍不同，僅能視為青年族群層級的同步現象。");
	}

	const def = ctx.metricDefs[0];
	if (def?.aggregation === AGGREGATION_RULE.REQUIRE_DENOMINATOR) {
		out.push("本指標為比率，跨年齡組距不可直接相加，合併須回到原始分子與分母。");
	}
	if (def?.aggregation === AGGREGATION_RULE.NOT_AGGREGATABLE) {
		out.push("本指標（中位數／指數）不可跨組距合併，僅能逐組距解讀。");
	}

	const need = ctx.opts.minPoints[ctx.candidate.type] ?? 3;
	const n = ctx.candidate?.evidence?.n;
	if (isNum(n) && n < need * 2) {
		out.push(`資料點僅 ${n} 個，勉強達到分析門檻，趨勢判讀請保守看待。`);
	}

	if (Array.isArray(extra)) out.push(...extra.filter(isStr));
	// 去重但保留順序：同一句限制印兩次會讓人以為是兩個不同的問題。
	return [...new Set(out)];
}

/**
 * ChartSpec（blueprint §13.3）。
 * 與報告共用同一個 artifact，所以圖上的數字與文字裡的數字必然同源。
 */
function buildChartSpecs(ctx, metricLabel, ageLabel) {
	const c = ctx.candidate;
	const chartType = CHART_BY_TYPE[c.type] || "bar";
	const spec = {
		chart_type: chartType,
		title: `${ageLabel} 歲${metricLabel}`,
		x_label: c.type === INSIGHT_TYPE.trend || c.type === INSIGHT_TYPE.change_point ? "年份" : "行政區",
		y_label: metricLabel,
		unit: ctx.metricDefs[0]?.unit || c.evidence?.unit || "",
		series_keys: Array.isArray(c.evidence?.series_keys) ? c.evidence.series_keys : [],
		annotations: [],
	};
	// change_point 的重點就是那個時點。沒有標註的折線圖等於把結論交給讀者自己找。
	if (c.type === INSIGHT_TYPE.change_point) {
		const at = c.facts?.change_year ?? c.facts?.change_x;
		if (at !== undefined && at !== null) {
			spec.annotations.push({ type: "vertical_line", x: at, label: `轉折點 ${at}` });
		}
	}
	return [spec];
}

// ─────────────────────────────────────────────────────────────
// 5. 排序與漏斗
// ─────────────────────────────────────────────────────────────

/**
 * 排序。
 * tie-break 用效果量再用 insight_id，而不是保留原始順序：
 * 原始順序取決於 mining 掃描資料表的先後，那是實作細節，
 * 讓它影響報告的排序等於讓報告在無關的改動下抖動。
 */
export function rankInsights(artifacts, opts = {}) {
	const list = (artifacts || []).filter((a) => a && a.validation?.status === "validated");
	const sorted = [...list].sort((a, b) => {
		const ds = (b.validation?.score ?? 0) - (a.validation?.score ?? 0);
		if (ds !== 0) return ds;
		const de = (b.evidence?.effect_size ?? 0) - (a.evidence?.effect_size ?? 0);
		if (de !== 0) return de;
		return String(a.insight_id).localeCompare(String(b.insight_id));
	});
	const n = Number.isInteger(opts.topN) && opts.topN > 0 ? opts.topN : sorted.length;
	return sorted.slice(0, n);
}

/**
 * 跑完整條 pipeline 並回傳漏斗計數。
 *
 * 為什麼報告一定要印這幾個數字：只給 5 則結論，讀者無從判斷這 5 則是
 * 「掃了 5 個挑 5 個」還是「掃了 120 個擋掉 115 個」。
 * 前者是隨便講，後者才是有把關。這個差別必須看得見。
 *
 * @returns {{scanned:number, passed_definition:number, passed_statistical:number,
 *            recommended:number, top_n:number, artifacts:object[],
 *            rejected:object[], by_gate:object}}
 */
export function runFunnel(inputs, opts = {}) {
	const topN = Number.isInteger(opts.topN) && opts.topN > 0 ? opts.topN : 5;
	const list = Array.isArray(inputs) ? inputs : [];
	const rejected = [];
	const byGate = {};
	const artifacts = [];
	let passedDefinition = 0;
	let passedStatistical = 0;

	for (const input of list) {
		const gate = runGates(input, opts);
		if (!gate.ok) {
			byGate[gate.gate] = (byGate[gate.gate] || 0) + 1;
			rejected.push({
				gate: gate.gate,
				stage: gate.stage,
				label: gate.label,
				reason: gate.reason,
				metric: input?.candidate?.metric ?? null,
				type: input?.candidate?.type ?? null,
			});
			continue;
		}
		passedDefinition += 1;
		passedStatistical += 1;   // 通過全部閘門＝定義與統計都過了

		try {
			artifacts.push(buildInsightArtifact(input, opts));
		} catch (err) {
			// 閘門都過了卻建不出 artifact，代表契約有洞。記下來，不要吞掉。
			byGate.artifact_build = (byGate.artifact_build || 0) + 1;
			rejected.push({ gate: "artifact_build", stage: "build", label: "產出", reason: err.message,
				metric: input?.candidate?.metric ?? null, type: input?.candidate?.type ?? null });
			passedStatistical -= 1;
			passedDefinition -= 1;
		}
	}

	// 只過了定義層、倒在統計層的數量＝定義層通過數 − 統計層通過數。
	// 這裡要重算一次，因為上面的迴圈一旦遇到閘門就 break，
	// 沒辦法直接知道「若不看統計會通過幾個」。
	let defOnly = 0;
	for (const input of list) {
		const ctx = toContext(input, opts);
		let okDef = true;
		for (const g of GATES) {
			if (g.stage !== "definition") continue;
			let hit = null;
			try { hit = g.check(ctx); } catch { hit = { reason: "閘門執行失敗" }; }
			if (hit) { okDef = false; break; }
		}
		if (okDef) defOnly += 1;
	}

	const ranked = rankInsights(artifacts, { topN });
	return {
		scanned: list.length,
		passed_definition: defOnly,
		passed_statistical: artifacts.length,
		recommended: ranked.length,
		top_n: topN,
		artifacts: ranked,
		all_artifacts: artifacts,
		rejected,
		by_gate: byGate,
	};
}

/** 漏斗的一行中文說明。報告開頭就印這一行。 */
export function formatFunnel(funnel) {
	return `掃描 ${funnel.scanned} 個候選 → 通過定義與資料檢查 ${funnel.passed_definition} 個`
		+ ` → 通過統計檢查 ${funnel.passed_statistical} 個 → 推薦 Top ${funnel.recommended}`;
}

/**
 * 單一候選的完整評估（給只想驗一筆的呼叫端用）。
 * 不會 throw——閘門結果本來就是預期內的兩種答案之一。
 */
export function evaluateCandidate(input, opts = {}) {
	const gate = runGates(input, opts);
	if (!gate.ok) {
		return {
			status: "rejected",
			gate: gate.gate,
			stage: gate.stage,
			score: 0,
			warnings: [],
			reject_reasons: [`[${gate.gate}] ${gate.reason}`],
		};
	}
	const s = scoreInsight(gate.ctx, opts);
	return { status: "validated", gate: null, stage: null, score: s.score, warnings: [], breakdown: s };
}
