// Age Compatibility Engine（blueprint §7.2 零推估、§7.3 年齡分級、
// §7.5 指標聚合規則、§7.6 範圍相容性）
//
// 這支檔案只做一件事：**在「官方組距」與「法定青年 18-35」之間，
// 誠實地說出兩者的關係，並且拒絕任何需要捏造數字才能成立的分析。**
//
// 為什麼非有不可：
// 《青年基本法》的青年是 18-35 歲，但主計處的人口資料是五歲一組
// （15-19、20-24…）。18 與 35 都落在組距**中間**，官方資料裡根本不存在
// 「18-35 歲人口」這個數字。於是最自然、也最常見的錯誤是：
//
//     18-35 ≈ 2/5 × (15-19) + (20-24) + (25-29) + (30-34) + 1/5 × (35-39)
//
// 這條式子的每一步都「看起來合理」，算出來的數字也會落在正確的量級，
// 圖畫得出來、報告寫得出來、在台上講得順。但那個 2/5 假設了 15-19 歲的
// 五個年齡人數相同——這件事沒有任何官方資料支持，而且在少子化的新北市
// 明顯不成立（15 歲與 19 歲的出生世代差了五年）。
// 那個數字沒有來源，被追問時答不出「這是哪裡來的」。
//
// 所以本引擎的鐵則是：**只加完整組距，而且只加連續的完整組距。**
// 做不到的時候，正確答案是「拒絕」，不是「估一個」。
//
// 與其他模組的關係：本檔只依賴 schema.js 的型別與驗證器，不碰 SQL、
// 不碰資料庫、不碰 LLM——所以它可以單獨測試（見 age.test.mjs）。

import {
	POLICY_YOUTH,
	OPERATIONAL_YOUTH,
	AGE_CLASS,
	AGGREGATION_RULE,
	TYPE_TO_RULE,
	isAggregatable,
	validateAgeScope,
} from "./schema.js";

/**
 * 年齡範圍錯誤。
 *
 * 為什麼用 throw 而不是回傳 null：這一類錯誤全部是「呼叫端要求了一個
 * 數學上不存在的東西」（例如從 15-19 切出 18-19）。回傳 null 或 NaN，
 * 下游很容易 `?? 0` 或 `|| fallback` 就吞掉了，最後畫面上出現一個
 * 沒有來源的數字——正是本引擎存在的目的要擋的事。當場爆掉才安全。
 */
export class AgeScopeError extends Error {
	constructor(message, detail = {}) {
		super(message);
		this.name = "AgeScopeError";
		this.detail = detail;
	}
}

const POLICY_LABEL = `${POLICY_YOUTH.lower}-${POLICY_YOUTH.upper}`;
const BOUND_LABEL = `${OPERATIONAL_YOUTH.lower}-${OPERATIONAL_YOUTH.upper}`;

/**
 * 「青年近似（B 級）」的起點上限。
 *
 * 為什麼是 20 而不是 18：18 落在 15-19 組距的中間，官方資料裡能拿到的
 * 起點只有兩種——15（整個組距都收進來）或 20（從下一個組距開始）。
 * 所以一個真正「涵蓋青年」的官方範圍，起點必然 ≤ 20。
 * 起點 25 以後（例如 25-39）已經丟掉青年前段七年，只能算子區段。
 */
const PROXY_MAX_START = 20;

// ─────────────────────────────────────────────────────────────
// 1. 標籤解析
// ─────────────────────────────────────────────────────────────

// 這個專案的標籤同時存在 ASCII hyphen 與 en-dash（U+2013）。
// 兩者長得幾乎一樣，肉眼校對不出來，只用 /-/ 去比會安靜地少抓一半組距——
// 少抓的後果不是報錯，而是 selectYouthBins 選到比較窄的範圍，
// 報告照樣產得出來，只是涵蓋的人少了幾萬。所以在解析前先一律正規化。
const DASH_LIKE = /[‐-―−－～〜~]/g;
const FULLWIDTH_DIGIT = /[０-９]/g;

/** 人類年齡的合理上界。用來擋掉「年份被當成年齡」的誤判，見 parseAgeLabel。 */
const MAX_HUMAN_AGE = 150;

function normalizeLabel(label) {
	return String(label)
		.replace(DASH_LIKE, "-")
		.replace(FULLWIDTH_DIGIT, (d) => String.fromCharCode(d.charCodeAt(0) - 0xfee0))
		.replace(/\s+/g, "");
}

/**
 * 從欄位標籤解析出年齡組距。
 *
 * @param   {string} label 例如 "20-24歲人口"、"25–29歲"、"15-19歲人口數"、"85歲以上"
 * @returns {{lower:number, upper:number}|null} 不是年齡組距時回 null（不是 throw——
 *          catalog 裡本來就有「總人口」「老化指數」這種欄位，跳過是正常流程）
 *
 * upper 為 inclusive：「20-24歲」的 upper 是 24 不是 25（與 schema.js 的
 * AgeBin 定義一致）。這件事必須整個系統一致，否則加總會差一歲的人口。
 */
export function parseAgeLabel(label) {
	if (label == null) return null;
	const s = normalizeLabel(label);

	// 為什麼每個 pattern 都硬性要求「歲」字：
	// 沒有它，「2000-2024年」會被解析成 lower=2000 / upper=2024 的「組距」。
	// 本專案的年份欄位涵蓋 2000-2024，這個誤判是真的會發生的，
	// 而且結果是一個永遠落在可用邊界外的鬼組距，靜靜污染 bins 清單。
	// MAX_HUMAN_AGE 是第二道保險，兩道都要。

	// a) 封閉組距："20-24歲人口"、"25–29歲"、"15-19歲人口數"
	const closed = s.match(/(\d{1,3})-(\d{1,3})歲/);
	if (closed) {
		const lower = Number(closed[1]);
		const upper = Number(closed[2]);
		if (lower > upper) return null;              // "24-20歲" 這種顛倒的標籤不猜，直接不認
		if (upper > MAX_HUMAN_AGE) return null;      // 年份誤判的第二道保險
		return { lower, upper };
	}

	// b) 開放組距："85歲以上"、"100歲以上人口"、"65歲及以上"
	//
	// upper 給 Infinity 而不是猜一個 120：猜上界等於捏造資料，而且
	// Infinity 有一個好處——它必然 > OPERATIONAL_YOUTH.upper，所以
	// 開放組距在任何路徑上都不可能被誤判成青年可用資料。
	const open = s.match(/(\d{1,3})歲(?:及|或)?以上/);
	if (open) {
		const lower = Number(open[1]);
		if (lower > MAX_HUMAN_AGE) return null;
		return { lower, upper: Infinity };
	}

	// c) 下開放組距："未滿15歲"、"14歲以下"。本專案目前沒有，但主計處其他
	//    資料集會出現；認得它比之後被一個 null 靜靜跳過安全。
	const below = s.match(/未滿(\d{1,3})歲/);
	if (below) {
		const n = Number(below[1]);
		if (n > MAX_HUMAN_AGE || n <= 0) return null;
		return { lower: 0, upper: n - 1 };
	}
	const orLess = s.match(/(\d{1,3})歲(?:及|或)?以下/);
	if (orLess) {
		const n = Number(orLess[1]);
		if (n > MAX_HUMAN_AGE) return null;
		return { lower: 0, upper: n };
	}

	return null;
}

/** 開放組距（沒有上界）無法構成可驗證的 scope，也必然在可用邊界之外。 */
const isOpenEnded = (bin) => !Number.isFinite(bin.upper);

/** 組距是否「完整」落在可用邊界 15-40 內。部分重疊不算——那正是 D 級。 */
export function isWithinOperational(bin, boundary = OPERATIONAL_YOUTH) {
	return Number.isFinite(bin.lower)
		&& Number.isFinite(bin.upper)
		&& bin.lower >= boundary.lower
		&& bin.upper <= boundary.upper;
}

// ─────────────────────────────────────────────────────────────
// 2. 從 catalog 建組距清單
// ─────────────────────────────────────────────────────────────

const overlaps = (a, b) => a.lower <= b.upper && b.lower <= a.upper;

/**
 * 把一份可能互相重疊的組距清單，收斂成一組互不重疊的細組距。
 *
 * 為什麼非做不可：catalog.yaml 的同一張表裡，percent3..percent23 是五歲
 * 一組的細組距，但 percent24（0-14歲）、percent26（15-64歲）、
 * percent28（65歲以上）是**同一批人的另一種切法**，而且 YAML 自己就註明了
 * 「percent26 等於 percent6..percent15 加總」。
 *
 * 若不處理，bins 裡會同時有 15-19 與 15-64 兩個 lower=15 的項目，
 * 後果有二：
 *   1. validateAgeScope 會因為「15-19 之後直接跳到 15-64」判定不連續——
 *      這還算好，至少會叫。
 *   2. 更糟的是 canAggregate 若放行，percent6 + percent26 會把 15-19 歲
 *      的人算兩次，總人口憑空多出十幾萬。
 *
 * 策略：同一個起點優先留「窄的」（細組距資訊量較高，且能組出粗組距，
 * 反之不行）；範圍完全相同時優先留 count 型欄位（可加總的那個）。
 *
 * @returns {{kept: AgeBin[], dropped: {bin: AgeBin, reason: string}[]}}
 */
export function partitionBins(bins) {
	const ranked = [...bins].sort((a, b) =>
		a.lower - b.lower
		|| (a.upper - a.lower) - (b.upper - b.lower)   // 窄的優先
		|| (a.type === "count" ? -1 : 0) - (b.type === "count" ? -1 : 0)
		|| String(a.column).localeCompare(String(b.column)));  // 最後靠欄位名定序，確保結果穩定

	const kept = [];
	const dropped = [];
	for (const bin of ranked) {
		const clash = kept.find((k) => overlaps(k, bin));
		if (clash) {
			dropped.push({
				bin,
				reason: `與已採用的「${clash.label}」(${rangeLabel(clash)}) 重疊，`
					+ `屬於同一批人的另一種切法；同時採用會重複計算`,
			});
			continue;
		}
		kept.push(bin);
	}
	return { kept, dropped };
}

/**
 * 從 catalog 的單一資料表描述建出組距清單。
 *
 * @param {object} tableMeta catalog.tables[name]，需要 .fields
 * @param {object} [opts]
 * @param {string|null} [opts.type]           只收這個 catalog type 的欄位（預設不篩）
 * @param {boolean}     [opts.keepComposites] true 時保留重疊的粗組距（除錯用，
 *                                            正常流程不要開——會重複計算）
 * @returns {AgeBin[]} 依 lower 排序、互不重疊
 */
export function binsFromCatalog(tableMeta, opts = {}) {
	const { type = null, keepComposites = false } = opts;
	const fields = tableMeta?.fields;
	if (!fields || typeof fields !== "object") {
		throw new AgeScopeError("binsFromCatalog 需要 catalog 的 tableMeta.fields", { tableMeta });
	}

	const bins = [];
	for (const [column, meta] of Object.entries(fields)) {
		if (type && meta?.type !== type) continue;
		// 標籤解析不出年齡就跳過。catalog 裡的「總人口」「老化指數」「行政區」
		// 本來就不是組距，這不是錯誤。
		const parsed = parseAgeLabel(meta?.label);
		if (!parsed) continue;
		bins.push({
			// label 保留原始字串，一個字都不改寫。
			// blueprint §7.2 明文要求：分析完之後必須看得到當初用的原始定義，
			// 否則沒有人能回頭檢查「15-39」到底是從哪幾個欄位加起來的。
			label: meta.label,
			lower: parsed.lower,
			upper: parsed.upper,
			column,
			type: meta?.type,
		});
	}

	bins.sort((a, b) => a.lower - b.lower || a.upper - b.upper);
	if (keepComposites) return bins;
	return partitionBins(bins).kept;
}

// ─────────────────────────────────────────────────────────────
// 3. 分級
// ─────────────────────────────────────────────────────────────

const rangeLabel = (r) => (Number.isFinite(r.upper) ? `${r.lower}-${r.upper}` : `${r.lower}+`);

/** 排序 + 檢查連續性。回傳排序後的複本，不改動輸入。 */
function sortedContiguous(bins) {
	const sorted = [...bins].sort((a, b) => a.lower - b.lower || a.upper - b.upper);
	for (let i = 1; i < sorted.length; i++) {
		const prev = sorted[i - 1];
		const cur = sorted[i];
		if (prev.upper + 1 === cur.lower) continue;
		const gap = prev.upper + 1 <= cur.lower - 1
			? `中間缺少 ${prev.upper + 1}-${cur.lower - 1} 歲`
			: `兩個組距重疊，會重複計算`;
		return {
			sorted,
			gap: `組距不連續：「${prev.label}」(${rangeLabel(prev)}) 之後直接跳到 `
				+ `「${cur.label}」(${rangeLabel(cur)})，${gap}`,
		};
	}
	return { sorted, gap: null };
}

/**
 * 判定一組組距的年齡等級（blueprint §7.3）。
 *
 * @param   {AgeBin[]} bins 必須是完整、連續的官方組距
 * @returns {AgeScope}
 * @throws  {AgeScopeError} 空清單、開放組距、或不連續時
 *
 * 為什麼不連續要 throw 而不是回傳 D：
 * D 的意思是「範圍超出可用邊界」，是一個**關於範圍**的判斷。
 * 但 20-24 + 30-34（跳過 25-29）根本沒有「一個範圍」可言——
 * 若硬給它一個 label「20-34」，那就是對外宣稱涵蓋了 25-29 歲的人，
 * 而那五歲的資料根本沒被加進去。這是說謊，不是分級。
 */
export function classifyAgeScope(bins) {
	if (!Array.isArray(bins) || bins.length === 0) {
		throw new AgeScopeError("classifyAgeScope 需要至少一個組距", { bins });
	}

	const open = bins.find(isOpenEnded);
	if (open) {
		throw new AgeScopeError(
			`「${open.label}」是開放組距（沒有上界），無法構成可驗證的 AgeScope。`
			+ `它必然超出可用邊界 ${BOUND_LABEL} 歲，不得用於青年分析。`,
			{ bin: open });
	}

	const { sorted, gap } = sortedContiguous(bins);
	if (gap) throw new AgeScopeError(gap, { bins: sorted });

	const lower = sorted[0].lower;
	const upper = sorted[sorted.length - 1].upper;

	let classification;
	if (lower === POLICY_YOUTH.lower && upper === POLICY_YOUTH.upper) {
		// A：官方資料剛好就是 18-35。本專案的五歲組距做不到，
		// 這條路徑是留給未來可能出現的單歲別資料的。
		classification = AGE_CLASS.A.code;
	} else if (lower < OPERATIONAL_YOUTH.lower || upper > OPERATIONAL_YOUTH.upper) {
		// D：只要有**任何一部分**落在 15-40 之外就出局。
		//
		// 為什麼不「把超出的部分切掉」：切 15-44 → 15-39 需要把 40-44 整組丟掉，
		// 那是合法的（丟掉完整組距不是切割）；但真正會發生的情況是
		// 資料只給了「15-44歲人口」一個欄位，此時要得到 15-39 只能按比例扣掉
		// 40-44 的估計值——那就是捏造。這裡一律判 D，要不要改用別的欄位
		// 由 selectYouthBins 從完整組距清單重新挑，而不是在這裡動手術。
		classification = AGE_CLASS.D.code;
	} else if (lower <= PROXY_MAX_START && upper >= POLICY_YOUTH.upper) {
		// B：完整落在 15-40 內，而且「從頭涵蓋到法定上界」。
		// 起點 ≤ 20 表示沒有漏掉青年前段（18-19 最多只差在 15-19 這一組的
		// 取捨上）；upper ≥ 35 表示涵蓋到法定青年的上界。
		// 15-39、20-39 都符合；20-34（上界少了 35）與 25-39（起點太晚）不符合。
		classification = AGE_CLASS.B.code;
	} else {
		// C：完整落在 15-40 內，但只是其中一段。可以用，但結論只適用該段。
		classification = AGE_CLASS.C.code;
	}

	const scope = {
		bins: sorted,
		lower,
		upper,
		classification,
		label: `${lower}-${upper}`,
		// is_exact 永遠 true：本引擎不存在「非精確聚合」的產出路徑，
		// 不精確的請求在上游就已經 throw 了。這個欄位留著是為了讓
		// 下游 artifact 能直接宣告「這個數字是完整組距加總來的」。
		is_exact: true,
		policy_age: POLICY_LABEL,
		usable: classification !== AGE_CLASS.D.code,
		class_name: AGE_CLASS[classification].name,
	};

	if (!scope.usable) {
		// 把「超出的是哪一段」算出來寫進理由。只說「超出邊界」不夠——
		// 使用者下一句一定會問「那把超出的部分扣掉不就好了」，
		// 所以同一句話裡就要把「不可用比例推估裁切」講掉。
		const over = [];
		if (lower < OPERATIONAL_YOUTH.lower) over.push(`${lower}-${OPERATIONAL_YOUTH.lower - 1}`);
		if (upper > OPERATIONAL_YOUTH.upper) over.push(`${OPERATIONAL_YOUTH.upper + 1}-${upper}`);
		scope.rejection = `${scope.label} 歲超出可用邊界 ${BOUND_LABEL} 歲`
			+ `（超出的部分：${over.join(" 與 ")} 歲），不得作為青年分析；`
			+ `也不可用比例推估把它裁成 ${BOUND_LABEL} 內的範圍——`
			+ `若資料另有完整的細組距，請改從細組距重新挑選。`;
	}

	// 自我檢查：本引擎產出的 scope 一定要通過共同契約。
	// 契約驗證放在這裡而不是下游，是因為一旦壞掉的 scope 流出去，
	// 錯誤會在報告生成階段才浮現，那時已經很難回推是哪一步出的問題。
	const v = validateAgeScope(scope);
	if (!v.ok) throw new AgeScopeError(`產出的 AgeScope 不符契約：${v.errors.join("；")}`, { scope, errors: v.errors });

	return scope;
}

// ─────────────────────────────────────────────────────────────
// 4. 挑選青年組距
// ─────────────────────────────────────────────────────────────

/** 允許 "20-34" 或 {lower,upper} 兩種寫法，減少呼叫端手寫物件出錯的機會。 */
function normalizeTarget(target) {
	if (target == null) return null;
	if (typeof target === "string") {
		const parsed = parseAgeLabel(target.includes("歲") ? target : `${target}歲`);
		if (!parsed) throw new AgeScopeError(`看不懂的目標範圍：${target}（請寫成 "20-34" 或 {lower:20, upper:34}）`);
		return parsed;
	}
	if (typeof target.lower === "number" && typeof target.upper === "number") return target;
	throw new AgeScopeError("target 必須是 \"20-34\" 這樣的字串或 {lower, upper}", { target });
}

/**
 * 從組距清單挑出可用的青年涵蓋範圍。
 *
 * 預設：回傳**完整落在可用邊界 15-40 內、最長的連續完整組距序列**。
 * 對本專案的實際資料（五歲一組）＝ 15-19 + 20-24 + 25-29 + 30-34 + 35-39
 * ＝ 15-39，B 級。40-44 因為 upper=44 > 40 被排除——不是被裁掉，是整組不取。
 *
 * @param {AgeBin[]} bins
 * @param {object}   [opts]
 * @param {string|{lower:number,upper:number}} [opts.target] 指定較窄的目標範圍
 * @param {{lower:number,upper:number}}        [opts.boundary] 覆寫可用邊界（測試用）
 * @returns {AgeScope}
 * @throws  {AgeScopeError} 沒有任何可用組距，或 target 無法用完整組距組出來
 */
export function selectYouthBins(bins, opts = {}) {
	const boundary = opts.boundary || OPERATIONAL_YOUTH;
	const target = normalizeTarget(opts.target);

	if (!Array.isArray(bins) || bins.length === 0) {
		throw new AgeScopeError("selectYouthBins 需要至少一個組距", { bins });
	}

	const all = [...bins].sort((a, b) => a.lower - b.lower || a.upper - b.upper);

	if (target) {
		// 目標本身就超出可用邊界的話，先擋——否則下面即使湊得出完整組距，
		// 也只是精確地算出一個不得用於青年分析的數字。
		if (target.lower < boundary.lower || target.upper > boundary.upper) {
			throw new AgeScopeError(
				`目標範圍 ${rangeLabel(target)} 超出可用邊界 ${boundary.lower}-${boundary.upper} 歲，`
				+ `不得作為青年分析。`, { target });
		}

		const run = runMatching(all, target);
		if (run) return classifyAgeScope(run);

		// 湊不出來——把「為什麼湊不出來」講清楚，因為這正是最容易被
		// 誤以為「再想想辦法就有」的地方。實際上沒有辦法，只有捏造。
		const startBin = all.find((b) => b.lower < target.lower && target.lower <= b.upper);
		const endBin = all.find((b) => b.lower <= target.upper && target.upper < b.upper);
		const why = [];
		if (startBin) {
			why.push(`起點 ${target.lower} 歲落在官方組距「${startBin.label}」(${rangeLabel(startBin)}) 的中間，不是組距起點`);
		}
		if (endBin) {
			why.push(`終點 ${target.upper} 歲落在官方組距「${endBin.label}」(${rangeLabel(endBin)}) 的中間，不是組距終點`);
		}
		if (!why.length) why.push(`清單裡沒有能連續拼出 ${rangeLabel(target)} 的完整組距`);

		throw new AgeScopeError(
			`無法取得 ${rangeLabel(target)} 歲：${why.join("；")}。`
			+ `要得到這個範圍必須把官方組距按比例拆開（例如假設組距內各歲人數相同），`
			+ `那個比例沒有任何官方資料支持，屬於推估而非事實——本引擎禁止產生這種數字。`
			+ `請改用完整組距能表達的範圍，並在報告中說明實際使用的是哪個範圍。`,
			{ target, startBin, endBin });
	}

	// 預設路徑：先濾掉任何有一部分在邊界外的組距（含開放組距），
	// 再找最長的連續段。濾掉而不是裁切——裁切就是推估。
	const eligible = all.filter((b) => isWithinOperational(b, boundary));
	if (eligible.length === 0) {
		throw new AgeScopeError(
			`沒有任何組距完整落在可用邊界 ${boundary.lower}-${boundary.upper} 歲內，`
			+ `這份資料不能做青年分析。`, { bins: all });
	}

	let best = null;
	let run = [eligible[0]];
	const consider = () => {
		const span = run[run.length - 1].upper - run[0].lower;
		const bestSpan = best ? best[best.length - 1].upper - best[0].lower : -1;
		// 先比涵蓋寬度，再比組距數量（同寬時細的比較有資訊量），
		// 最後比起點（早的優先），確保同樣輸入永遠得到同樣輸出。
		if (span > bestSpan
			|| (span === bestSpan && best && run.length > best.length)
			|| (span === bestSpan && best && run.length === best.length && run[0].lower < best[0].lower)) {
			best = run;
		}
	};
	for (let i = 1; i < eligible.length; i++) {
		if (eligible[i - 1].upper + 1 === eligible[i].lower) {
			run.push(eligible[i]);
		} else {
			consider();
			run = [eligible[i]];
		}
	}
	consider();

	return classifyAgeScope(best);
}

/** 找出「加起來剛好等於 range」的連續完整組距序列；找不到回 null。 */
function runMatching(sortedBins, range) {
	const startIdx = sortedBins.findIndex((b) => b.lower === range.lower);
	if (startIdx < 0) return null;
	const run = [];
	for (let i = startIdx; i < sortedBins.length; i++) {
		const bin = sortedBins[i];
		if (run.length && run[run.length - 1].upper + 1 !== bin.lower) return null;  // 中間有洞
		if (bin.upper > range.upper) return null;                                    // 會超出目標 → 需要切割 → 不行
		run.push(bin);
		if (bin.upper === range.upper) return run;
	}
	return null;
}

// ─────────────────────────────────────────────────────────────
// 5. 指標聚合規則（blueprint §7.5）
// ─────────────────────────────────────────────────────────────

/**
 * 這組組距能不能就這個指標做跨組距合併。
 *
 * @param {AgeBin[]} bins
 * @param {{column?:string, label?:string, type?:string, aggregation?:string, denominator?:string}} metric
 *        直接吃 catalog 的欄位描述（有 type 就夠），也接受已經算好 aggregation 的
 *        MetricDefinition——兩種來源在 pipeline 裡都會出現。
 * @returns {{ok:boolean, rule:string, reason:string}}
 *
 * 這支函式擋的是整個系統第二危險的錯（第一是切割組距）：
 * 把 percent6..percent10 的**失業率**加起來、或取平均，然後說「青年失業率」。
 * 結果會是一個介於各組距之間的數字，量級正確、圖畫得出來、
 * 沒有任何驗證器會叫——但它不等於任何一群人的失業率。
 */
export function canAggregate(bins, metric) {
	const label = metric?.label || metric?.column || "此指標";
	const rule = metric?.aggregation
		|| TYPE_TO_RULE[metric?.type]
		|| AGGREGATION_RULE.NOT_AGGREGATABLE;

	if (!Array.isArray(bins) || bins.length === 0) {
		return { ok: false, rule, reason: "沒有給任何組距，無從判斷能不能合併。" };
	}

	const open = bins.find(isOpenEnded);
	if (open) {
		return {
			ok: false,
			rule,
			reason: `「${open.label}」是開放組距（沒有上界），`
				+ `加進來之後這筆數字對應的年齡範圍說不清楚，不得參與跨組距合併。`,
		};
	}

	// 只有一個組距時根本沒有「跨組距」可言，任何 rule 都不必攔。
	// 但理由要寫清楚，免得呼叫端把這個 ok 誤讀成「這個比例欄位可以加總」。
	if (bins.length === 1) {
		return {
			ok: true,
			rule,
			reason: `只有一個組距（${rangeLabel(bins[0])}），不涉及跨組距合併，直接取用原始值即可。`,
		};
	}

	// 連續性優先檢查：即使是可加總的人口數，跳過中間組距的加總也不對應
	// 任何真實範圍——例如 20-24 + 30-34 加起來，沒有任何一個年齡區間
	// 等於這個數字，但它會被標成「20-34」印進報告。
	// 用排序後的複本算涵蓋範圍：呼叫端傳進來的順序不保證，
	// 直接取 bins[0] / bins[last] 會在亂序輸入時報出錯誤的範圍。
	const { sorted, gap } = sortedContiguous(bins);
	if (gap) {
		return {
			ok: false,
			rule,
			reason: `${gap}。加總的結果不對應任何真實年齡範圍，`
				+ `不得以連續區間的名義呈現；請補齊中間的組距，或改用實際連續的較窄範圍。`,
		};
	}

	if (isAggregatable(rule)) {
		return {
			ok: true,
			rule,
			reason: `「${label}」是可加總的量（${rule}），`
				+ `且 ${sorted.length} 個組距連續涵蓋 ${sorted[0].lower}-${sorted[sorted.length - 1].upper} 歲，`
				+ `直接加總即為該範圍的總量。`,
		};
	}

	// 不能加的情況，理由必須指名「缺什麼才能做」，而不是只說不行——
	// 否則呼叫端（含 LLM）會換個寫法再試一次，或乾脆自己算一個平均。
	const REASONS = {
		[AGGREGATION_RULE.REQUIRE_DENOMINATOR]:
			`「${label}」是比例（ratio），跨組距合併需要分子與分母兩個欄位。`
			+ `直接把各組距的比例相加會得到一個大於 100% 的無意義數字；`
			+ `取平均則等於假設每個組距的人口數相同——新北市各五歲組距人口相差數萬，`
			+ `這個假設不成立，小組距會被放大。`
			+ `正確做法：取得分子（例如失業人數）與分母（例如勞動力人口）兩個欄位，`
			+ `各自跨組距加總之後再相除。`
			+ (metric?.denominator ? `（已知分母欄位：${metric.denominator}）` : `（目前的 catalog 沒有提供對應的分母欄位）`),
		[AGGREGATION_RULE.REQUIRE_WEIGHT]:
			`「${label}」是平均值，跨組距合併需要各組距的權重（通常是人口數）做加權平均。`
			+ `未加權的平均會讓人數少的組距與人數多的組距等權，結論會偏向小組距。`,
		[AGGREGATION_RULE.NOT_AGGREGATABLE]:
			`「${label}」是中位數／指數這類不可合併的量：`
			+ `各組距的中位數無論怎麼加權都還原不出合併後的中位數，`
			+ `必須回到原始個體資料重算。本資料集沒有個體資料，因此這個指標只能逐組距呈現。`,
	};

	return { ok: false, rule, reason: REASONS[rule] || `「${label}」的聚合規則是 ${rule}，不可跨組距合併。` };
}

// ─────────────────────────────────────────────────────────────
// 6. 範圍相容性（blueprint §7.6）
// ─────────────────────────────────────────────────────────────

/** 呼叫端給 AgeScope 或 AgeBin[] 都接受——pipeline 兩種都會出現。 */
function asScope(x, which) {
	if (Array.isArray(x)) return classifyAgeScope(x);
	if (x && Array.isArray(x.bins) && typeof x.classification === "string") return x;
	throw new AgeScopeError(`compareAgeScopes 的第 ${which} 個參數必須是 AgeScope 或 AgeBin[]`, { value: x });
}

/** 一個 scope 裡，「完整組距」允許的切點。只有落在這些點上的範圍才不需要切割組距。 */
const cutPoints = (scope) => ({
	starts: new Set(scope.bins.map((b) => b.lower)),
	ends: new Set(scope.bins.map((b) => b.upper)),
});

/**
 * 兩個年齡範圍能不能放在一起比較。
 *
 * @returns {{status:"exact"|"proxy"|"incompatible", reason:string, warnings:string[]}}
 *
 * 三種結果的意思：
 *   exact        ── 同一個年齡範圍。組距切法不同沒關係（例如一邊是
 *                    20-24+25-29+30-34、另一邊是單一的 20-34），只要
 *                    兩邊都由完整組距構成、涵蓋範圍相同，比較就是成立的。
 *   proxy        ── 範圍不同但都在 15-40 內，且存在一段「兩邊都能用完整
 *                    組距表達」的共同範圍。可以並陳，但只能當探索性對照，
 *                    結論強度必須下修，不得寫成同一族群的比較。
 *   incompatible ── 有一邊超出 15-40，或兩邊要對齊就得切割組距（含完全
 *                    沒有重疊的情況）。這種比較做不出來，只能拒絕。
 *
 * 刻意不做的事：當 a 的範圍剛好是 b 的一段連續完整組距時（例如
 * 25-34 之於 20-39），本函式**不會**自動把 b 收斂成 a 然後宣告 exact。
 * 因為那會默默改掉 b 這一側實際測量的族群——使用者要的是 20-39 的數字，
 * 拿到的卻是 25-34 的。正確做法是回報 proxy，並在 warnings 裡把
 * 「可以收斂成哪個範圍」講出來，讓呼叫端明確決定。
 */
export function compareAgeScopes(a, b) {
	const A = asScope(a, 1);
	const B = asScope(b, 2);
	const warnings = [];

	// 1. 出界的一律不能比。這條放最前面，因為 D 級的數字連單獨呈現都不行，
	//    更不用說拿來對照。
	const outs = [];
	if (A.classification === AGE_CLASS.D.code) outs.push(`第一個範圍 ${A.label}`);
	if (B.classification === AGE_CLASS.D.code) outs.push(`第二個範圍 ${B.label}`);
	if (outs.length) {
		return {
			status: "incompatible",
			reason: `${outs.join("、")} 超出可用邊界 ${BOUND_LABEL} 歲（D 級），`
				+ `不得作為青年分析，也不得與其他範圍對照。`,
			warnings,
		};
	}

	// 2. 範圍相同 → exact。
	if (A.lower === B.lower && A.upper === B.upper) {
		if (A.bins.length !== B.bins.length) {
			warnings.push(`兩邊的組距切法不同（${A.bins.length} 組 vs ${B.bins.length} 組），`
				+ `但都由完整組距構成且涵蓋範圍相同，可精確聚合成同一範圍。`);
		}
		if (A.classification !== AGE_CLASS.A.code) {
			warnings.push(`兩邊都是 ${A.label} 歲，不等於法定青年 ${POLICY_LABEL} 歲；`
				+ `報告仍須標示實際範圍，不得逕稱「青年」。`);
		}
		return {
			status: "exact",
			reason: `兩者涵蓋的年齡範圍相同（${A.label} 歲），可直接比較。`,
			warnings,
		};
	}

	// 3. 找「兩邊都能用完整組距表達」的共同範圍。找不到就是要切割組距。
	const lo = Math.max(A.lower, B.lower);
	const hi = Math.min(A.upper, B.upper);
	if (lo > hi) {
		return {
			status: "incompatible",
			reason: `${A.label} 與 ${B.label} 完全沒有重疊，是兩群不同的人，`
				+ `不能互為青年近似，也不能並列為同一指標的對照。`,
			warnings,
		};
	}

	const ca = cutPoints(A);
	const cb = cutPoints(B);
	const starts = [...ca.starts].filter((v) => cb.starts.has(v) && v >= lo && v <= hi).sort((x, y) => x - y);
	const ends = [...ca.ends].filter((v) => cb.ends.has(v) && v >= lo && v <= hi).sort((x, y) => x - y);
	const common = starts.length && ends.length && starts[0] <= ends[ends.length - 1]
		? { lower: starts[0], upper: ends[ends.length - 1] }
		: null;

	if (!common) {
		return {
			status: "incompatible",
			reason: `${A.label} 與 ${B.label} 雖然重疊於 ${lo}-${hi} 歲，`
				+ `但兩邊的組距邊界對不上——要對齊就必須把官方組距拆開，`
				+ `而按比例拆組距屬於推估而非事實，本引擎禁止。`,
			warnings,
		};
	}

	// 4. proxy：可以並陳，但一定要降低宣稱強度。
	warnings.push(`年齡範圍不同（${A.label} vs ${B.label}），只能作探索性對照；`
		+ `結論強度必須下修，不得寫成同一族群的比較。`);
	warnings.push(`若要精確比較，請把兩邊都收斂到共同的完整組距範圍 ${common.lower}-${common.upper} 歲`
		+ `（兩邊都能用完整組距表達，不需要切割）。`);
	warnings.push(`兩者皆非法定青年 ${POLICY_LABEL} 歲，報告必須標示各自的實際範圍。`);

	return {
		status: "proxy",
		reason: `${A.label} 與 ${B.label} 都完整落在可用邊界 ${BOUND_LABEL} 歲內，`
			+ `但涵蓋範圍不同，只能作為青年近似的探索性對照。`,
		warnings,
	};
}

// ─────────────────────────────────────────────────────────────
// 7. 對外敘述
// ─────────────────────────────────────────────────────────────

/**
 * 產生報告裡必須出現的那一句話。
 *
 * 這句話是整份報告最重要的一行：它把「我們實際算的是什麼」與
 * 「法律說的青年是什麼」同時擺出來。少了它，讀者會理所當然地
 * 把 15-39 讀成青年——而那是我們永遠不能讓它發生的誤解。
 * 所以這裡的每一種分級都必然同時提到實際範圍與法定範圍，
 * 而且任何一條路徑都不會寫出「本分析涵蓋 18-35 歲」這種宣稱，
 * 除非 classification 真的是 A。
 *
 * @param {AgeScope|AgeBin[]} scope
 * @returns {string}
 */
export function describeScope(scope) {
	const s = asScope(scope, 1);

	if (s.classification === AGE_CLASS.A.code) {
		return `本項分析使用官方資料提供之 ${s.label} 歲統計，`
			+ `與《青年基本法》法定青年 ${POLICY_LABEL} 歲定義完全一致。`;
	}
	if (s.classification === AGE_CLASS.B.code) {
		return `本項分析使用官方資料提供之 ${s.label} 歲統計作為青年近似族群觀察；`
			+ `法定青年定義仍為 ${POLICY_LABEL} 歲。`;
	}
	if (s.classification === AGE_CLASS.C.code) {
		return `本項分析使用官方資料提供之 ${s.label} 歲統計，`
			+ `為法定青年 ${POLICY_LABEL} 歲之子區段，結論僅適用於此區段，不得推論至全體青年；`
			+ `法定青年定義仍為 ${POLICY_LABEL} 歲。`;
	}
	return `本項分析之年齡範圍 ${s.label} 歲超出可用邊界 ${BOUND_LABEL} 歲，`
		+ `不得作為青年分析或青年近似；法定青年定義為 ${POLICY_LABEL} 歲。`;
}

/**
 * 給報告 limitations 陣列用的條列版本。
 * 與 describeScope 共用同一份判斷，避免「敘述說 B、限制欄寫 C」這種
 * 兩處各寫一次就會慢慢對不上的腐爛。
 */
export function scopeLimitations(scope) {
	const s = asScope(scope, 1);
	const out = [describeScope(s)];
	if (s.classification !== AGE_CLASS.A.code) {
		out.push(`實際使用欄位：${s.bins.map((b) => `${b.label}(${b.column})`).join("、")}，`
			+ `均為完整官方組距加總，未做任何組距內的比例拆分。`);
	}
	if (s.classification === AGE_CLASS.B.code) {
		const extraLow = s.lower < POLICY_YOUTH.lower ? `${s.lower}-${POLICY_YOUTH.lower - 1}` : null;
		const extraHigh = s.upper > POLICY_YOUTH.upper ? `${POLICY_YOUTH.upper + 1}-${s.upper}` : null;
		const extras = [extraLow, extraHigh].filter(Boolean);
		if (extras.length) {
			out.push(`此範圍較法定青年多涵蓋 ${extras.join(" 與 ")} 歲，`
				+ `因官方統計以五歲為一組，${POLICY_YOUTH.lower} 與 ${POLICY_YOUTH.upper} 歲落在組距中間，無法精確切出。`);
		}
	}
	return out;
}
