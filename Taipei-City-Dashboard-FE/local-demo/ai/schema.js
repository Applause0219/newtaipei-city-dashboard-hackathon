// Insight Pipeline 的共同契約（blueprint Phase 0）
//
// 這支檔案沒有邏輯，只有「型別長什麼樣」與「怎麼驗」。
// Age Engine、Mining Engine、Validator、Report Renderer 四個模組彼此不
// 直接相依，全部只認這裡的形狀——所以它們可以分開寫、分開測、分開換掉。
//
// 為什麼不用 Pydantic / TypeScript：
// 現有 local-demo/ai 全是 Node ESM，沒有建置步驟。多引一套型別系統就多一個
// 安裝步驟與一個在比賽現場會壞掉的東西。這裡用純函式驗證，零相依。
//
// blueprint 對應：§7.3 Age Classification、§7.5 Measure Aggregation Rule、
// §9.2 InsightCandidate、§11.2 InsightArtifact、§13.3 ChartSpec

// ─────────────────────────────────────────────────────────────
// 1. 年齡
// ─────────────────────────────────────────────────────────────

/** 法定青年（《青年基本法》）。報告一律以此為對照基準，不得改寫。 */
export const POLICY_YOUTH = { lower: 18, upper: 35 };

/** 可用邊界。完整落在這個範圍內的官方組距，才可當青年近似資料。 */
export const OPERATIONAL_YOUTH = { lower: 15, upper: 40 };

/**
 * AgeBin：一個官方組距。永遠是原始的，不做任何切割。
 * upper 為 inclusive（「20-24歲」的 upper 是 24，不是 25）。
 *
 * @typedef  {object} AgeBin
 * @property {string} label   原始標籤，例如 "20-24歲人口"。必須保留——
 *                            分析完看不到原始定義是 blueprint §7.2 明文禁止的
 * @property {number} lower
 * @property {number} upper
 * @property {string} column  對應的資料庫欄位，例如 "percent7"
 */

/** blueprint §7.3 的四個等級 */
export const AGE_CLASS = {
	A: { code: "A", name: "Official Exact", desc: "完整法定青年 18-35" },
	B: { code: "B", name: "Exact Youth Proxy", desc: "青年近似，完整落在 15-40" },
	C: { code: "C", name: "Youth Segment", desc: "青年子區段" },
	D: { code: "D", name: "Out of Scope", desc: "超出可用邊界，不得作青年分析" },
};

/**
 * @typedef  {object} AgeScope
 * @property {AgeBin[]} bins            實際使用的組距，依 lower 排序且必須連續
 * @property {number}   lower
 * @property {number}   upper
 * @property {"A"|"B"|"C"|"D"} classification
 * @property {string}   label           對外顯示，例如 "15-39"
 * @property {boolean}  is_exact        是否由完整組距精確聚合而成（永遠為 true，
 *                                      因為本系統不產生非精確的 scope）
 */

// ─────────────────────────────────────────────────────────────
// 2. 指標
// ─────────────────────────────────────────────────────────────

/**
 * blueprint §7.5。這張表是避免統計錯誤的核心機制：
 * 決定一個 measure 跨組距時「能不能加」。
 */
export const AGGREGATION_RULE = {
	SUM: "SUM",                                   // 人數、件數、金額總額
	REQUIRE_DENOMINATOR: "REQUIRE_DENOMINATOR",   // 比例 / Rate：要分子分母才能合併
	REQUIRE_WEIGHT: "REQUIRE_WEIGHT",             // 平均值：要權重才能合併
	NOT_AGGREGATABLE: "NOT_AGGREGATABLE",         // 中位數、指數：不可合併
};

/**
 * type → aggregation rule。
 *
 * 同時吃兩套詞彙，因為專案裡有兩個資料來源：
 *   catalog.yaml       count / ratio                    （本機 demo 的三張表）
 *   youth_fact 契約    count / rate / mean / median /    （Airflow 那條線的 value_type）
 *                      index / ratio
 *
 * 兩邊對同一件事的判斷是一致的——失業率要分子分母、平均薪資要權數、
 * 中位數不能加總——只是用不同的字表示。在這裡合流，下游就不必知道
 * 資料是從哪條線來的。
 */
export const TYPE_TO_RULE = {
	// 可加總的量
	count: AGGREGATION_RULE.SUM,
	// 比例：直接相加會超過 100%，取平均則忽略各組人數差異
	ratio: AGGREGATION_RULE.REQUIRE_DENOMINATOR,
	rate: AGGREGATION_RULE.REQUIRE_DENOMINATOR,
	// 平均數：要各組的人數或權數才能合併
	mean: AGGREGATION_RULE.REQUIRE_WEIGHT,
	// 中位數與指數：數學上無法由各組摘要值還原
	median: AGGREGATION_RULE.NOT_AGGREGATABLE,
	index: AGGREGATION_RULE.NOT_AGGREGATABLE,
};

/**
 * youth_fact_18_35 的 coverage → 這筆數值能不能當事實用。
 *
 * 這是兩份設計文件的接縫。YOUTH_DATA_ARCHITECTURE 允許用單齡人口權重把
 * 15-19 拆出 18-19（coverage='apportioned'）；blueprint §2.1 禁止拆分官方組距。
 *
 * 兩邊都不是錯的，衝突的是「估計值能不能進報告」。這個對照表把它變成
 * 一個可設定的決策，而不是靠人記得：
 *   exact       → 真實組距，可當事實
 *   apportioned → 估計值，只能標示為估計，且不得作為主結論
 *   unavailable → 沒有權重或組距過粗，不可使用
 *
 * 預設只信任 exact。要放寬必須明確傳參數，不會有人不小心放寬。
 */
export const COVERAGE = {
	exact: { usable: true, is_estimate: false, label: "官方組距" },
	apportioned: { usable: false, is_estimate: true, label: "依單齡人口權重分攤之估計值" },
	unavailable: { usable: false, is_estimate: false, label: "無法換算" },
};

/**
 * @param {string} coverage
 * @param {{allowEstimates?: boolean}} [opts]
 * @returns {{ok:boolean, is_estimate:boolean, reason:string}}
 */
export function checkCoverage(coverage, opts = {}) {
	const c = COVERAGE[coverage];
	if (!c) return { ok: false, is_estimate: false, reason: `未知的 coverage：${coverage}` };
	if (c.usable) return { ok: true, is_estimate: false, reason: c.label };
	if (c.is_estimate && opts.allowEstimates) {
		return { ok: true, is_estimate: true, reason: `${c.label}——必須於報告中標示為估計值，不得作為主結論` };
	}
	return { ok: false, is_estimate: c.is_estimate, reason: c.label };
}

/** 可跨年齡組距相加的，只有 SUM 這一種。 */
export const isAggregatable = (rule) => rule === AGGREGATION_RULE.SUM;

/**
 * @typedef  {object} MetricDefinition
 * @property {string} column
 * @property {string} label
 * @property {"count"|"ratio"|"year"|"category"} type
 * @property {string} aggregation   AGGREGATION_RULE 之一
 * @property {string} [unit]
 * @property {string} [denominator] REQUIRE_DENOMINATOR 時，分母欄位
 */

// ─────────────────────────────────────────────────────────────
// 3. 分析輸入
// ─────────────────────────────────────────────────────────────

/**
 * Mining Engine 的唯一輸入格式。純資料，不含 SQL、不含 DB 連線。
 * 這是刻意的——分析函式必須能單獨測試。
 *
 * @typedef  {object} Series
 * @property {string} key                 穩定識別字，例如 "板橋區"
 * @property {string} label               顯示名稱
 * @property {{x:(number|string), y:number}[]} points
 *                                        時間序列時 x 是年份（number）；
 *                                        分區比較時 x 是行政區（string）
 * @property {string} [unit]
 */

// ─────────────────────────────────────────────────────────────
// 4. Insight
// ─────────────────────────────────────────────────────────────

/** blueprint §9.1 的七類。MVP 先做前五類（空間與跨資料集需要更多資料）。 */
export const INSIGHT_TYPE = {
	trend: "trend",
	change_point: "change_point",
	outlier: "outlier",
	group_difference: "group_difference",
	rank_change: "rank_change",
};

/**
 * Mining Engine 的輸出。**不是自然語言**——只有結構化事實與數值。
 * 敘述由 Report Renderer 產生，模型只負責潤飾，不負責計算。
 *
 * @typedef  {object} InsightCandidate
 * @property {string} type              INSIGHT_TYPE 之一
 * @property {string} metric            欄位名
 * @property {string} metric_label
 * @property {object} facts             該類型專屬的數值，全部由程式算出
 * @property {number} effect_size       0..1，效果大小（跨類型可比較）
 * @property {number} statistical_strength 0..1，統計強度
 * @property {object} evidence          { series_keys, x_range, n }
 */

/**
 * @typedef  {object} ValidationResult
 * @property {"validated"|"rejected"} status
 * @property {number}   score           0..100
 * @property {string[]} warnings
 * @property {string[]} [reject_reasons]
 */

/**
 * blueprint §11.2。整個系統最重要的介面契約：
 * 報告與圖表都只能從這一個物件產生，所以兩邊不可能對不上。
 *
 * @typedef  {object} InsightArtifact
 * @property {string} insight_id
 * @property {string} title
 * @property {string} claim
 * @property {string} insight_type
 * @property {object} scope
 *   @property {string} scope.policy_age       永遠是 "18-35"
 *   @property {string} scope.actual_age       實際分析的年齡範圍
 *   @property {string} scope.age_classification
 *   @property {string} scope.time
 *   @property {string} scope.geography
 * @property {object} evidence
 *   @property {string}   evidence.query_id
 *   @property {string}   evidence.sql        實際跑過的 SQL（可重現）
 *   @property {string[]} evidence.dataset_ids
 *   @property {string}   evidence.method
 *   @property {number}   evidence.effect_size
 *   @property {object}   evidence.facts
 *   @property {string}   evidence.snapshot_at ISO 時間
 * @property {ValidationResult} validation
 * @property {string[]} limitations
 * @property {object[]} chart_specs
 */

// ─────────────────────────────────────────────────────────────
// 5. 驗證器
// ─────────────────────────────────────────────────────────────

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isStr = (v) => typeof v === "string" && v.length > 0;

/** @returns {{ok:boolean, errors:string[]}} */
export function validateAgeScope(scope) {
	const e = [];
	if (!scope || typeof scope !== "object") return { ok: false, errors: ["age scope 不是物件"] };
	if (!Array.isArray(scope.bins) || scope.bins.length === 0) e.push("bins 至少要一項");
	else {
		const sorted = [...scope.bins].sort((a, b) => a.lower - b.lower);
		sorted.forEach((b, i) => {
			if (!isStr(b.label)) e.push(`bins[${i}] 缺 label（原始標籤必須保留）`);
			if (!isNum(b.lower) || !isNum(b.upper)) e.push(`bins[${i}] lower/upper 必須是數字`);
			if (b.lower > b.upper) e.push(`bins[${i}] lower 大於 upper`);
			// 組距之間不得有洞，也不得重疊——有洞代表加總的不是一個連續範圍
			if (i > 0 && sorted[i - 1].upper + 1 !== b.lower) {
				e.push(`bins 不連續：${sorted[i - 1].label} 之後直接跳到 ${b.label}`);
			}
		});
		if (isNum(scope.lower) && sorted[0] && scope.lower !== sorted[0].lower) {
			e.push(`scope.lower(${scope.lower}) 與第一個組距(${sorted[0].lower}) 不符`);
		}
		const last = sorted[sorted.length - 1];
		if (isNum(scope.upper) && last && scope.upper !== last.upper) {
			e.push(`scope.upper(${scope.upper}) 與最後一個組距(${last.upper}) 不符`);
		}
	}
	if (!AGE_CLASS[scope.classification]) e.push(`classification 必須是 A/B/C/D，收到 ${scope.classification}`);
	return { ok: e.length === 0, errors: e };
}

/** @returns {{ok:boolean, errors:string[]}} */
export function validateSeries(s) {
	const e = [];
	if (!s || typeof s !== "object") return { ok: false, errors: ["series 不是物件"] };
	if (!isStr(s.key)) e.push("缺 key");
	if (!Array.isArray(s.points) || s.points.length === 0) e.push("points 至少要一項");
	else {
		s.points.forEach((p, i) => {
			if (p == null || typeof p !== "object") { e.push(`points[${i}] 不是物件`); return; }
			if (p.x === undefined || p.x === null) e.push(`points[${i}] 缺 x`);
			if (!isNum(p.y)) e.push(`points[${i}].y 不是有限數字（收到 ${p.y}）`);
		});
	}
	return { ok: e.length === 0, errors: e };
}

/** @returns {{ok:boolean, errors:string[]}} */
export function validateCandidate(c) {
	const e = [];
	if (!c || typeof c !== "object") return { ok: false, errors: ["candidate 不是物件"] };
	if (!INSIGHT_TYPE[c.type]) e.push(`未知的 insight type：${c.type}`);
	if (!isStr(c.metric)) e.push("缺 metric");
	if (!isNum(c.effect_size) || c.effect_size < 0 || c.effect_size > 1) {
		e.push(`effect_size 必須是 0..1，收到 ${c.effect_size}`);
	}
	if (!isNum(c.statistical_strength) || c.statistical_strength < 0 || c.statistical_strength > 1) {
		e.push(`statistical_strength 必須是 0..1，收到 ${c.statistical_strength}`);
	}
	if (!c.facts || typeof c.facts !== "object") e.push("缺 facts");
	if (!c.evidence || typeof c.evidence !== "object") e.push("缺 evidence");
	return { ok: e.length === 0, errors: e };
}

/** @returns {{ok:boolean, errors:string[]}} */
export function validateArtifact(a) {
	const e = [];
	if (!a || typeof a !== "object") return { ok: false, errors: ["artifact 不是物件"] };
	["insight_id", "title", "claim", "insight_type"].forEach((k) => {
		if (!isStr(a[k])) e.push(`缺 ${k}`);
	});

	const s = a.scope || {};
	if (s.policy_age !== "18-35") e.push(`scope.policy_age 必須是 "18-35"（法定定義不可改寫），收到 ${s.policy_age}`);
	["actual_age", "age_classification", "time", "geography"].forEach((k) => {
		if (!isStr(s[k])) e.push(`缺 scope.${k}`);
	});

	const ev = a.evidence || {};
	// 沒有 SQL 就無法重現，無法重現的 insight 不准進報告（blueprint §10.1）
	if (!isStr(ev.sql)) e.push("缺 evidence.sql——無法重現的結論不得進入報告");
	if (!isStr(ev.method)) e.push("缺 evidence.method");
	if (!isStr(ev.snapshot_at)) e.push("缺 evidence.snapshot_at");

	if (!a.validation || !["validated", "rejected"].includes(a.validation.status)) {
		e.push("validation.status 必須是 validated 或 rejected");
	}
	// 非 A 級年齡一定要交代限制，否則報告會讓人以為涵蓋完整青年
	if (!Array.isArray(a.limitations)) e.push("limitations 必須是陣列");
	else if (s.age_classification && s.age_classification !== "A" && a.limitations.length === 0) {
		e.push(`age_classification 為 ${s.age_classification} 但沒有任何 limitations——`
			+ "非完整 18-35 的分析必須標示限制");
	}
	return { ok: e.length === 0, errors: e };
}

/** 把多個驗證結果併成一個，方便在 pipeline 裡串。 */
export function combine(...results) {
	const errors = results.flatMap((r) => r.errors || []);
	return { ok: errors.length === 0, errors };
}
