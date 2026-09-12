// Insight Mining Engine — 從 Series 挖出「候選洞察」
//
//   ── 模型不算任何一個數字 ──
//
// 這支檔案裡沒有模型呼叫、沒有網路、沒有資料庫、沒有 Math.random、沒有
// Date.now()。輸入一樣，輸出必然一模一樣（mining.test.mjs 有一條測試直接
// 跑兩次做深度比對）。這是整個系統對評審唯一的信用來源：
// 報告裡的每個數字都能在這裡用同一份資料重算一次。
//
// 模型只會拿到這裡吐出的 InsightCandidate（純數值 + 結構化事實），
// 負責把它寫成人話。反過來不行——所以這裡刻意不產生任何一句自然語言，
// emit() 會把 description / summary 這類欄位當成違約，直接丟例外。
//
// 為什麼不照 blueprint 用 ruptures / SciPy：
// 現有 local-demo 整套是 Node ESM、零建置步驟。為了一個變點偵測多裝一個
// Python runtime，等於在比賽現場多一個會壞掉的東西。這裡的五個統計量
// （最小平方、變點掃描、MAD robust z、η²、名次差）全部是幾十行純 JS 能寫完的，
// 自己寫反而比多一個 runtime 安全。
//
// blueprint 對應：§9.1 七類洞察（MVP 前五類）、§9.2 InsightCandidate

import { INSIGHT_TYPE, validateSeries, validateCandidate } from "./schema.js";

// ─────────────────────────────────────────────────────────────
// 0. 數值工具
// ─────────────────────────────────────────────────────────────

/** 浮點零的判定門檻。比這小的量一律當 0，避免 1e-17 被當成「有變化」。 */
const EPS = 1e-12;

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

/**
 * 統一收斂到小數 6 位。
 * 不是為了好看——0.30000000000000004 這種尾巴會讓報告文字與圖表標籤
 * 對不起來，而「兩邊各算一次結果不同」正是這個架構要消滅的東西。
 */
function round6(v) {
	if (!Number.isFinite(v)) return v;          // 非有限數不在這裡吞掉，留給 emit() 大聲失敗
	if (Math.abs(v) < EPS) return 0;            // 順手消掉 -0：JSON 看不出來，Object.is 比對會炸
	if (Math.abs(v) >= 1e9) return v;           // 再乘 1e6 會超過 MAX_SAFE_INTEGER，反而失準
	return Math.round(v * 1e6) / 1e6;
}

const sum = (a) => a.reduce((s, v) => s + v, 0);
const mean = (a) => sum(a) / a.length;

/** 中位數。永遠排副本——排到呼叫端的陣列會讓同一份資料第二次跑出不同結果。 */
function median(values) {
	const a = [...values].sort((x, y) => x - y);
	const m = a.length >> 1;
	return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

/**
 * 字串排序一律用 UTF-16 碼位比，不用 localeCompare。
 * localeCompare 的中文排序結果取決於執行環境的 ICU 版本與 locale：
 * 「板橋區」和「三重區」誰在前，在我的筆電和評審的機器上可能不一樣。
 * 排序是輸出順序的一部分，順序一飄，「同樣的輸入必然同樣的輸出」就不成立了。
 */
const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

/** 相對落差，天然落在 0..1：兩者相同是 0，其中一邊是 0 就是 1。 */
function relGap(a, b) {
	const den = Math.abs(a) + Math.abs(b);
	return den > EPS ? Math.abs(a - b) / den : 0;
}

/**
 * 飽和函數 |v| / (|v| + half)：把「沒有上限的量」壓進 0..1 且單調遞增。
 * half 是「算一半」的位置。用飽和而不是 min(1, v/max) 是因為後者在超過
 * max 之後全部並列 1.0，ranker 就再也分不出「漲三成」和「漲三倍」。
 */
function saturate(v, half) {
	const a = Math.abs(v);
	return a / (a + half);
}

/**
 * 最小平方直線。sxx = 0（x 全同）回 null；y 零變異時 r2 給 null 而不是 0/0。
 * R² 在零變異的資料上是數學上未定義，不是 0 也不是 1——硬給一個數字
 * 就會讓「29 個區都填同一個值」的髒資料看起來像完美趨勢。
 */
function linreg(xs, ys) {
	const n = xs.length;
	const mx = mean(xs);
	const my = mean(ys);
	let sxx = 0, sxy = 0, syy = 0;
	for (let i = 0; i < n; i++) {
		const dx = xs[i] - mx, dy = ys[i] - my;
		sxx += dx * dx;
		sxy += dx * dy;
		syy += dy * dy;
	}
	if (sxx <= EPS) return null;
	const slope = sxy / sxx;
	const intercept = my - slope * mx;
	let sse = 0;
	for (let i = 0; i < n; i++) {
		const r = ys[i] - (intercept + slope * xs[i]);
		sse += r * r;
	}
	return { n, slope, intercept, sse, sst: syy, mean_y: my, r2: syy > EPS ? clamp01(1 - sse / syy) : null };
}

// ─────────────────────────────────────────────────────────────
// 1. 輸入正規化
// ─────────────────────────────────────────────────────────────

/** 混型別的 x 要能當 Map 的鍵又不會讓數字 2000 和字串 "2000" 撞在一起。 */
const axisKey = (x) => (typeof x === "number" ? `n:${x}` : `s:${x}`);

/**
 * 驗證 + 排序 + 去重檢查。所有偵測器的唯一入口。
 *
 * 壞資料一律丟例外，不是回 []。回 [] 代表「查過了，沒有洞察」，
 * 丟例外代表「這份資料根本不能分析」——兩件事混在一起，
 * 現場就會變成「為什麼這個區沒有結論？」而沒有人答得出來。
 * mineAll 會把例外收成 errors，所以單一條爛資料不會弄死整批。
 */
function prepare(series, fnName) {
	const v = validateSeries(series);
	if (!v.ok) throw new Error(`${fnName}：series 無效 — ${v.errors.join("；")}`);

	const pts = series.points.map((p) => ({ x: p.x, y: p.y }));
	const numericX = pts.every((p) => typeof p.x === "number" && Number.isFinite(p.x));

	// 時間序列一律依年份排序。
	// 資料可能缺年（2003 直接跳到 2005），也可能根本沒照時序給。
	// 下面每一條迴歸都假設 xs 遞增，這裡排一次，後面就不必再假設；
	// 而且因為只排副本，呼叫端的 points 順序不會被動到——
	// 就地排序會讓「同一份資料跑兩次」在第二次拿到不同的輸入，決定性當場破功。
	if (numericX) pts.sort((a, b) => a.x - b.x);

	// 同一個 x 出現兩次 = 呼叫端少篩了一個維度。
	// 最常見的是忘了篩「統計類型」，於是每年拿到「計／男／女」三列：
	// 斜率會被壓成三分之一、R² 被打爛，但畫出來的圖完全正常，沒有人看得出來。
	// 這種錯必須當場失敗，不能安靜地算下去。
	const seen = new Set();
	for (const p of pts) {
		const k = axisKey(p.x);
		if (seen.has(k)) {
			throw new Error(`${fnName}：x=${p.x} 重複出現於 series「${series.key}」。`
				+ `一個 x 只能有一列——最常見的原因是忘了篩「統計類型」，每年拿到「計／男／女」三列。`);
		}
		seen.add(k);
	}

	return { pts, numericX, xs: pts.map((p) => p.x), ys: pts.map((p) => p.y), n: pts.length };
}

/**
 * 多條 series 的共同 x 軸（交集）。
 *
 * 為什麼一定要取交集：拿 A 區 2000–2024 的平均去比 B 區只有 2020–2024 的平均，
 * 數字算得出來、圖也畫得出來，但那個「差距」有一半是年份不同造成的。
 * 沒有交集就是呼叫端把不相干的兩份資料丟在一起，直接失敗。
 */
function commonAxis(list, fnName) {
	const prepared = list.map((s) => prepare(s, fnName));
	const maps = prepared.map((p) => {
		const m = new Map();
		p.pts.forEach((pt) => m.set(axisKey(pt.x), pt.y));
		return m;
	});
	// 順序取自第一條 series（已排序），所以共同軸的順序也是決定性的
	const xs = prepared[0].pts.map((p) => p.x).filter((x) => maps.every((m) => m.has(axisKey(x))));
	if (xs.length === 0) {
		throw new Error(`${fnName}：這些 series 沒有任何共同的 x（${list.map((s) => s.key).join("、")}）——`
			+ `不同時間範圍的數列不能直接比較`);
	}
	const unionSize = new Set(prepared.flatMap((p) => p.pts.map((pt) => axisKey(pt.x)))).size;
	return { prepared, maps, xs, numericX: prepared.every((p) => p.numericX), trimmed: xs.length < unionSize };
}

// ─────────────────────────────────────────────────────────────
// 2. 輸出把關
// ─────────────────────────────────────────────────────────────

/** 這些欄位名一旦出現就是有人在 Mining Engine 裡寫結論了。 */
const FORBIDDEN_KEYS = ["description", "summary", "text", "narrative", "conclusion", "claim", "title"];

function scanBad(node, path, out) {
	if (node === undefined) { out.push(`${path}=undefined`); return; }
	if (node === null) { out.push(`${path}=null`); return; }
	if (typeof node === "number") { if (!Number.isFinite(node)) out.push(`${path}=${node}`); return; }
	if (Array.isArray(node)) { node.forEach((v, i) => scanBad(v, `${path}[${i}]`, out)); return; }
	if (typeof node === "object") { for (const k of Object.keys(node)) scanBad(node[k], `${path}.${k}`, out); }
}

/**
 * 每一個候選都要從這裡出去。三關：
 *   1. 深掃 NaN / Infinity / null / undefined——validateCandidate 只看頂層，
 *      而 facts.slope = NaN 這種錯會一路活到報告裡變成「成長了 NaN 人」
 *   2. schema.validateCandidate
 *   3. 自然語言欄位——文字是 Report Renderer 的工作，在這裡寫等於繞過驗證
 */
function emit(candidate) {
	const c = {
		...candidate,
		effect_size: clamp01(round6(candidate.effect_size)),
		statistical_strength: clamp01(round6(candidate.statistical_strength)),
	};
	const bad = [];
	scanBad(c, c.type, bad);
	if (bad.length) throw new Error(`${c.type}：輸出含非有限數值 — ${bad.join("、")}`);
	for (const k of FORBIDDEN_KEYS) {
		if (k in c || k in (c.facts || {})) {
			throw new Error(`${c.type}：不得輸出「${k}」。Mining Engine 只產生數值，敘述由 Report Renderer 寫。`);
		}
	}
	const v = validateCandidate(c);
	if (!v.ok) throw new Error(`${c.type}：不符合 InsightCandidate 契約 — ${v.errors.join("；")}`);
	return c;
}

/** metric 是欄位名（percent7），metric_label 是人看的（20-24歲人口）。 */
function labelsOf(opts, series) {
	const metric = typeof opts.metric === "string" && opts.metric ? opts.metric : "value";
	const metric_label = (typeof opts.metric_label === "string" && opts.metric_label)
		|| (series && typeof series.label === "string" && series.label)
		|| metric;
	return { metric, metric_label };
}

function evidenceOf(series_keys, xs, n, unit) {
	const ev = { series_keys, x_range: [xs[0], xs[xs.length - 1]], n };
	if (typeof unit === "string" && unit.length > 0) ev.unit = unit;
	return ev;
}

// ─────────────────────────────────────────────────────────────
// 3. 偵測器：趨勢
// ─────────────────────────────────────────────────────────────

/**
 * 最小平方趨勢。
 *
 * @param {object} series
 * @param {object} [opts] minPoints / minR2 / minRelChange / halfEffect
 * @returns {object[]} 0 或 1 個候選
 *
 * effect_size：|跨窗口相對變化| 的飽和值，half = 20%。
 * 刻意不用原始斜率——「每年 +500 人」在三重區是雜訊、在平溪區是翻倍，
 * 斜率本身不帶基期資訊，跨指標更是完全不可比（人數 vs 百分比）。
 *
 * statistical_strength：調整後 R²（Ezekiel）。用調整後而不是原始 R²，
 * 是因為只有 3 個點時原始 R² 幾乎必然很高——兩點決定一條線，第三點再爛
 * 也拉不下來。調整後 R² 會把自由度扣回去，n=3 時要非常貼合才拿得到分數。
 */
export function detectTrend(series, opts = {}) {
	const o = { minPoints: 3, minR2: 0.5, minRelChange: 0.05, halfEffect: 0.2, ...opts };
	const p = prepare(series, "detectTrend");

	// x 是行政區（字串）時沒有「趨勢」可言，回 [] 而不是硬把它當 0,1,2…
	if (!p.numericX) return [];
	// 硬下限 3：n=2 時 R² 恆為 1、調整後 R² 是 0/0。這條不吃 opts，
	// 因為把 minPoints 設成 2 不會讓兩點變成趨勢，只會讓輸出變成 NaN。
	if (p.n < Math.max(3, o.minPoints)) return [];

	const fit = linreg(p.xs, p.ys);
	if (!fit || fit.r2 === null) return [];      // y 零變異：完全水平的線沒有趨勢，也不該報「效果 0」的候選

	const span = p.xs[p.n - 1] - p.xs[0];
	if (span <= 0) return [];

	const fittedFirst = fit.intercept + fit.slope * p.xs[0];
	const fittedLast = fit.intercept + fit.slope * p.xs[p.n - 1];
	// 用配適值而不是首尾原始值算變化率：首年剛好是個異常低點時，
	// 原始首尾會把一條平緩的線講成暴漲。
	// 基期太接近 0 就改用整體平均當基準，否則 (x-0)/0 直接是 Infinity。
	const base = Math.abs(fittedFirst) > EPS ? Math.abs(fittedFirst) : Math.abs(fit.mean_y);
	if (base <= EPS) return [];
	const relChange = (fittedLast - fittedFirst) / base;

	const adjR2 = 1 - (1 - fit.r2) * (p.n - 1) / (p.n - 2);   // n >= 3 已保證分母 >= 1

	// 兩道門檻缺一不可：只看 R² 會把「25 年只漲 0.3%」的完美直線報成趨勢，
	// 只看變化率會把一條上下亂跳、首尾剛好差很多的雜訊報成趨勢。
	if (fit.r2 < o.minR2) return [];
	if (Math.abs(relChange) < o.minRelChange) return [];

	const { metric, metric_label } = labelsOf(o, series);
	return [emit({
		type: INSIGHT_TYPE.trend,
		metric,
		metric_label,
		facts: {
			slope: round6(fit.slope),                       // 每單位 x（通常是每年）
			intercept: round6(fit.intercept),
			r2: round6(fit.r2),
			adj_r2: round6(clamp01(adjR2)),
			direction: fit.slope > 0 ? "increasing" : "decreasing",
			rel_change: round6(relChange),
			pct_change: round6(relChange * 100),
			first_x: p.xs[0],
			last_x: p.xs[p.n - 1],
			first_y: round6(p.ys[0]),
			last_y: round6(p.ys[p.n - 1]),
			fitted_first: round6(fittedFirst),
			fitted_last: round6(fittedLast),
			mean_y: round6(fit.mean_y),
			span: round6(span),
		},
		effect_size: saturate(relChange, o.halfEffect),
		statistical_strength: clamp01(adjR2),
		evidence: evidenceOf([series.key], p.xs, p.n, series.unit),
	})];
}

// ─────────────────────────────────────────────────────────────
// 4. 偵測器：變點
// ─────────────────────────────────────────────────────────────

/**
 * 變點偵測：掃描所有切法，找「一條線 → 兩條線」誤差降最多的那一年。
 *
 * @param {object} series
 * @param {object} [opts] minSegment / minImprovement
 * @returns {object[]} 0 或 1 個候選
 *
 * facts.year 的定義是「最後一個仍屬舊走勢的年份」。
 *
 * effect_size：|斜率差| / (|前斜率| + |後斜率|)，天然 0..1——
 * 0 = 兩段一樣斜，0.5 = 斜率變一半，1 = 完全反轉或其中一段變水平。
 * 兩段都水平（階梯式跳動）時分母是 0/0，改用前後平均值的相對落差。
 *
 * statistical_strength：扣掉自由度的誤差改善率。
 * 兩段一定比一段貼合（單一直線本來就是兩段模型的可行解），所以未調整的
 * improvement 永遠 > 0，拿來當「統計強度」等於每條線都會報變點。
 * 這裡用 1 − (SSE₂/(n−4)) / (SSE₁/(n−2))：多花的 3 個自由度要付代價。
 */
export function detectChangePoint(series, opts = {}) {
	const o = { minSegment: 3, minImprovement: 0.3, ...opts };
	const p = prepare(series, "detectChangePoint");
	if (!p.numericX) return [];

	// 每段至少 minSegment 點：兩點必成一直線，容許 2 點的段等於送分，
	// 任何一條雜訊線都能在頭尾各切兩點做出「完美的兩段」。
	const minSeg = Math.max(2, Math.trunc(o.minSegment));
	// n >= 5 是調整式的分母 (n-4) 不為 0 的硬下限
	if (p.n < Math.max(5, minSeg * 2)) return [];

	const full = linreg(p.xs, p.ys);
	if (!full || full.sst <= EPS) return [];     // 零變異
	// 單一直線已經完美貼合 → improvement 是 0/0，而且本來就沒有轉折可言
	if (full.sse <= EPS * Math.max(1, full.sst)) return [];

	let best = null;
	for (let k = minSeg - 1; k <= p.n - minSeg - 1; k++) {
		const L = linreg(p.xs.slice(0, k + 1), p.ys.slice(0, k + 1));
		const R = linreg(p.xs.slice(k + 1), p.ys.slice(k + 1));
		if (!L || !R) continue;
		const sse = L.sse + R.sse;
		// 平手取「較晚」的切點。
		// 這不是隨便挑的：連續的轉折（先升後降的帳篷形）在 k=頂點−1 與
		// k=頂點 兩處誤差完全相同，因為頂點同時落在兩條線上。取較早的話，
		// 報出來的轉折年會固定比真正的頂點早一年——每一次都錯一年，而且看不出來。
		const tol = Math.max(EPS, full.sse * 1e-9);
		if (best === null || sse < best.sse - tol || Math.abs(sse - best.sse) <= tol) {
			best = { k, sse, L, R };
		}
	}
	if (!best) return [];

	const improvement = clamp01(1 - best.sse / full.sse);
	if (improvement < o.minImprovement) return [];   // 只降一點點誤差的「轉折」是雜訊，不是事件

	const adjusted = clamp01(1 - (best.sse / (p.n - 4)) / (full.sse / (p.n - 2)));

	const nBefore = best.k + 1;
	const nAfter = p.n - nBefore;
	const meanBefore = mean(p.ys.slice(0, nBefore));
	const meanAfter = mean(p.ys.slice(nBefore));
	const slopeDen = Math.abs(best.L.slope) + Math.abs(best.R.slope);
	const effect = slopeDen > EPS
		? Math.abs(best.R.slope - best.L.slope) / slopeDen
		: relGap(meanAfter, meanBefore);
	if (effect <= 0) return [];

	const year = p.xs[best.k];
	// 轉折年當下，兩段延伸線的落差（階梯式跳動時這才是主角）
	const gapAtYear = (best.R.intercept + best.R.slope * year) - (best.L.intercept + best.L.slope * year);

	const { metric, metric_label } = labelsOf(o, series);
	return [emit({
		type: INSIGHT_TYPE.change_point,
		metric,
		metric_label,
		facts: {
			year,
			split_index: best.k,
			slope_before: round6(best.L.slope),
			slope_after: round6(best.R.slope),
			delta_slope: round6(best.R.slope - best.L.slope),
			n_before: nBefore,
			n_after: nAfter,
			mean_before: round6(meanBefore),
			mean_after: round6(meanAfter),
			gap_at_year: round6(gapAtYear),
			sse_single: round6(full.sse),
			sse_split: round6(best.sse),
			improvement: round6(improvement),
			adj_improvement: round6(adjusted),
			// 某一段可能完全水平（升到頂後持平），那一段的 R² 是 0/0。
			// 寧可整個欄位不存在，也不能塞 null 或 NaN 進報告。
			...(best.L.r2 === null ? {} : { r2_before: round6(best.L.r2) }),
			...(best.R.r2 === null ? {} : { r2_after: round6(best.R.r2) }),
		},
		effect_size: effect,
		statistical_strength: adjusted,
		evidence: evidenceOf([series.key], p.xs, p.n, series.unit),
	})];
}

// ─────────────────────────────────────────────────────────────
// 5. 偵測器：異常值
// ─────────────────────────────────────────────────────────────

/**
 * 以中位數 + MAD 做 robust z（Iglewicz & Hoaglin 1993）。
 *
 * 為什麼不用平均值與標準差：
 * 新北市 29 區的青年人口，板橋區比最小的平溪區大兩個數量級。
 * 板橋一個區就足以把標準差撐大到讓其他 28 個區全部落在 ±2σ 內——
 * 也就是「只找得到那個本來就人盡皆知的第一名，其餘一個都抓不到」。
 * 中位數與 MAD 的崩潰點是 50%，一半的資料變成極端值它才會失守。
 *
 * @param {object} series
 * @param {object} [opts] minPoints / threshold / maxCandidates / detrend / detrendMinR2
 * @returns {object[]} 每個異常點一個候選，依 |z| 遞減
 *
 * effect_size：|偏離量| / (|偏離量| + 基準水準)，也就是「偏離相對於水準的占比」。
 * statistical_strength：|z| / (2 × 門檻)，剛好踩線 = 0.5，兩倍門檻 = 1。
 */
export function detectOutlier(series, opts = {}) {
	const o = { minPoints: 5, threshold: 3.5, maxCandidates: 3, detrend: "auto", detrendMinR2: 0.3, ...opts };
	const p = prepare(series, "detectOutlier");
	// 少於 5 點的 MAD 幾乎沒有意義（4 點時它就是中間兩個距離的一半），
	// 任何一點都能輕易「超標」，報出來全是假陽性。
	if (p.n < Math.max(3, o.minPoints)) return [];

	// 去趨勢。
	//
	// 這關是必要的，不是精緻化：一條穩定成長 25 年的人口曲線，離中位數最遠的
	// 必然是最早那年和最新那年——不去趨勢的話每一個區都會報出「2000 年異常低、
	// 2024 年異常高」兩筆完全沒有資訊量的候選，把真正的事件淹掉。
	// 去趨勢後比的是「相對於自身趨勢線的偏離」，那才是異常。
	// 分區比較（x 是行政區）沒有趨勢可言，一律不去趨勢。
	let values = p.ys;
	let fit = null;
	let detrended = false;
	if (p.numericX && o.detrend !== false) {
		fit = linreg(p.xs, p.ys);
		if (fit && fit.r2 !== null && (o.detrend === true || fit.r2 >= o.detrendMinR2)) {
			values = p.ys.map((y, i) => y - (fit.intercept + fit.slope * p.xs[i]));
			detrended = true;
		}
	}

	const med = median(values);
	const mad = median(values.map((v) => Math.abs(v - med)));

	let scale, method;
	if (mad > EPS) {
		scale = mad / 0.6745;            // 0.6745 = 常態下 MAD 與 σ 的換算常數
		method = "MAD";
	} else {
		// MAD = 0 的實況：超過一半的點數值完全相同（例如小區的某年齡層常年是同一個數）。
		// 直接除下去是 x/0 = Infinity。Iglewicz & Hoaglin 給的備案是改用平均絕對離差。
		const meanAD = mean(values.map((v) => Math.abs(v - med)));
		if (meanAD <= EPS) return [];    // 連平均絕對離差都是 0 → 全部一樣 → 沒有異常值
		scale = meanAD * 1.253314;
		method = "MeanAD";
	}

	const medValue = detrended ? median(p.ys) : med;
	const { metric, metric_label } = labelsOf(o, series);

	const hits = [];
	for (let i = 0; i < p.n; i++) {
		const z = (values[i] - med) / scale;
		if (Math.abs(z) < o.threshold) continue;
		const expected = detrended ? fit.intercept + fit.slope * p.xs[i] : medValue;
		const deviation = p.ys[i] - expected;
		const ref = Math.abs(expected);
		const effect = Math.abs(deviation) + ref > EPS ? Math.abs(deviation) / (Math.abs(deviation) + ref) : 0;
		hits.push({ i, z, expected, deviation, effect });
	}

	// 依 |z| 遞減；同分時用 x 的字串比大小固定順序，不能靠 sort 的穩定性碰運氣
	hits.sort((a, b) => Math.abs(b.z) - Math.abs(a.z) || cmpStr(String(p.xs[a.i]), String(p.xs[b.i])));

	return hits.slice(0, Math.max(1, o.maxCandidates)).map((h) => emit({
		type: INSIGHT_TYPE.outlier,
		metric,
		metric_label,
		facts: {
			x: p.xs[h.i],
			value: round6(p.ys[h.i]),
			expected: round6(h.expected),
			deviation: round6(h.deviation),
			robust_z: round6(h.z),
			threshold: round6(o.threshold),
			median_value: round6(medValue),
			mad: round6(mad),
			scale: round6(scale),
			method,
			detrended,
			...(detrended ? { residual: round6(values[h.i]), trend_slope: round6(fit.slope) } : {}),
		},
		effect_size: h.effect,
		statistical_strength: clamp01(Math.abs(h.z) / (2 * o.threshold)),
		evidence: evidenceOf([series.key], p.xs, p.n, series.unit),
	}));
}

// ─────────────────────────────────────────────────────────────
// 6. 偵測器：組間差異
// ─────────────────────────────────────────────────────────────

/**
 * 多條 series 之間，誰和誰差最多。
 *
 * @param {object[]} seriesList
 * @param {object} [opts] minRelGap
 * @returns {object[]} 0 或 1 個候選
 *
 * 水準一律取「共同 x 上的平均」，不是最後一年的值——只看最後一年，
 * 一次普查更新或一個跨年度的定義修改就能製造出不存在的「差距」。
 *
 * effect_size：(最高 − 最低) / (|最高| + |最低|)，相對差距，天然 0..1。
 * statistical_strength：η²（組間平方和 / 總平方和）。它回答的是
 * 「這個差距在整體變異裡占多少」——兩個區平均差很多但各自年年上下狂跳時，
 * η² 會很低，剛好擋掉那種「差距是雜訊撐出來的」假結論。
 */
export function detectGroupDifference(seriesList, opts = {}) {
	const o = { minRelGap: 0.05, ...opts };
	if (!Array.isArray(seriesList) || seriesList.length < 2) return [];

	const axis = commonAxis(seriesList, "detectGroupDifference");
	const groups = seriesList.map((s, gi) => {
		const vals = axis.xs.map((x) => axis.maps[gi].get(axisKey(x)));
		return { key: s.key, label: s.label || s.key, values: vals, level: mean(vals) };
	});

	// 依水準遞減；同分用 key 固定順序（否則兩個等值的區誰被報成「最高」會飄）
	groups.sort((a, b) => b.level - a.level || cmpStr(a.key, b.key));
	const hi = groups[0];
	const lo = groups[groups.length - 1];

	const gap = hi.level - lo.level;
	const den = Math.abs(hi.level) + Math.abs(lo.level);
	if (den <= EPS) return [];                  // 兩邊都是 0：沒有差距，也不能除
	const effect = clamp01(gap / den);
	if (effect < o.minRelGap) return [];        // 差距小到不值得寫一段話

	// η²
	const all = groups.flatMap((g) => g.values);
	const grand = mean(all);
	const sst = sum(all.map((v) => (v - grand) ** 2));
	const ssb = sum(groups.map((g) => g.values.length * (g.level - grand) ** 2));
	const eta = sst > EPS ? clamp01(ssb / sst) : 0;   // 總變異為 0 → 0/0，給 0 不給 NaN

	const { metric, metric_label } = labelsOf(o, seriesList[0]);
	return [emit({
		type: INSIGHT_TYPE.group_difference,
		metric,
		metric_label,
		facts: {
			high_key: hi.key,
			high_label: hi.label,
			high_value: round6(hi.level),
			low_key: lo.key,
			low_label: lo.label,
			low_value: round6(lo.level),
			gap: round6(gap),
			rel_gap: round6(effect),
			// 倍數在報告裡最好用，但分母可能是 0（某區該年齡層掛零），那時整個欄位不給
			...(Math.abs(lo.level) > EPS ? { ratio: round6(hi.level / lo.level) } : {}),
			grand_mean: round6(grand),
			eta_squared: round6(eta),
			k: groups.length,
			level_basis: "mean",
			x_basis: axis.trimmed ? "common" : "all",
		},
		effect_size: effect,
		statistical_strength: eta,
		evidence: evidenceOf(groups.map((g) => g.key), axis.xs, axis.xs.length * groups.length, seriesList[0].unit),
	})];
}

// ─────────────────────────────────────────────────────────────
// 7. 偵測器：名次變動
// ─────────────────────────────────────────────────────────────

/** 依值遞減排名次；同分用 key 決定先後，否則名次會隨輸入順序飄。 */
function ranksAt(entries) {
	const sorted = [...entries].sort((a, b) => b.y - a.y || cmpStr(a.key, b.key));
	const r = new Map();
	sorted.forEach((e, i) => r.set(e.key, i + 1));
	return r;
}

/**
 * 頭尾兩個共同 x 之間，誰的名次移動最多。
 *
 * @param {object[]} seriesList
 * @param {object} [opts] minDelta / maxCandidates / minSeparation
 * @returns {object[]}
 *
 * effect_size：|名次差| / (k − 1)，也就是「走完整張排行榜的幾分之幾」。
 * 同樣升三名，在 5 個區裡是大事、在 29 個區裡是常態，除以 k−1 才可比。
 *
 * statistical_strength：真正被超車的對手中，頭尾兩端相對差距的最小值，
 * 除以 minSeparation（預設 2%）後夾在 0..1。
 * 兩個區差 0.1% 時的互換是四捨五入造成的抖動，不是事件——
 * 沒有這一關，29 個區每年都會生出一堆「名次超車」的假新聞。
 */
export function detectRankChange(seriesList, opts = {}) {
	const o = { minDelta: 1, maxCandidates: 3, minSeparation: 0.02, ...opts };
	if (!Array.isArray(seriesList) || seriesList.length < 2) return [];

	const axis = commonAxis(seriesList, "detectRankChange");
	if (axis.xs.length < 2) return [];          // 只有一個共同時點，談不上變動

	const xFirst = axis.xs[0];
	const xLast = axis.xs[axis.xs.length - 1];
	const rows = seriesList.map((s, gi) => ({
		key: s.key,
		label: s.label || s.key,
		first: axis.maps[gi].get(axisKey(xFirst)),
		last: axis.maps[gi].get(axisKey(xLast)),
	}));

	const rFirst = ranksAt(rows.map((r) => ({ key: r.key, y: r.first })));
	const rLast = ranksAt(rows.map((r) => ({ key: r.key, y: r.last })));
	const k = rows.length;

	const movers = [];
	for (const row of rows) {
		const a = rFirst.get(row.key);
		const b = rLast.get(row.key);
		const delta = a - b;                    // 名次數字變小 = 往上爬，所以正值代表上升
		if (Math.abs(delta) < Math.max(1, o.minDelta)) continue;

		// 真的被越過的對手：頭尾兩端的大小關係反轉
		const crossed = rows.filter((other) => {
			if (other.key === row.key) return false;
			const s1 = Math.sign(row.first - other.first);
			const s2 = Math.sign(row.last - other.last);
			return s1 !== 0 && s2 !== 0 && s1 !== s2;
		});
		// 名次變了卻找不到任何真正被越過的對象 = 同分排序造成的抖動，不報
		if (crossed.length === 0) continue;

		const separation = Math.min(...crossed.map((c) => Math.min(relGap(row.first, c.first), relGap(row.last, c.last))));
		movers.push({ row, a, b, delta, crossed, separation });
	}

	movers.sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta) || cmpStr(x.row.key, y.row.key));

	const { metric, metric_label } = labelsOf(o, seriesList[0]);
	return movers.slice(0, Math.max(1, o.maxCandidates)).map((m) => emit({
		type: INSIGHT_TYPE.rank_change,
		metric,
		metric_label,
		facts: {
			key: m.row.key,
			label: m.row.label,
			rank_first: m.a,
			rank_last: m.b,
			delta_ranks: m.delta,
			direction: m.delta > 0 ? "up" : "down",
			x_first: xFirst,
			x_last: xLast,
			value_first: round6(m.row.first),
			value_last: round6(m.row.last),
			crossed_keys: m.crossed.map((c) => c.key).sort(cmpStr),
			min_separation: round6(m.separation),
			k,
		},
		effect_size: clamp01(Math.abs(m.delta) / (k - 1)),
		statistical_strength: clamp01(m.separation / o.minSeparation),
		evidence: evidenceOf([m.row.key, ...m.crossed.map((c) => c.key)], axis.xs, axis.xs.length * k, seriesList[0].unit),
	}));
}

// ─────────────────────────────────────────────────────────────
// 8. 總入口
// ─────────────────────────────────────────────────────────────

const TYPE_ORDER = Object.keys(INSIGHT_TYPE);

/**
 * 同一件事可能被兩個偵測器（或兩條 series）報兩次。
 * 簽章刻意不含 effect_size：同一件事算出來的效果大小本來就會略有不同，
 * 放進簽章等於永遠去不掉重複。
 * rank_change 用排序後的 key 集合，因為「A 超車 B」和「B 被 A 超車」
 * 是同一件事的兩種寫法，報告裡只需要一筆。
 */
function signature(c) {
	const keys = c.type === INSIGHT_TYPE.rank_change
		? [...c.evidence.series_keys].sort(cmpStr)
		: c.evidence.series_keys;
	const at = c.facts.year ?? c.facts.x ?? c.facts.x_last ?? "";
	const mag = c.type === INSIGHT_TYPE.rank_change ? Math.abs(c.facts.delta_ranks) : "";
	return [c.type, c.metric, keys.join(","), at, mag].join("|");
}

/**
 * 跑完所有適用的偵測器，回傳攤平、去重、排序過的候選陣列。
 *
 * @param {object[]} seriesList
 * @param {object} [meta] { metric, metric_label, unit }
 * @param {object} [opts] 依偵測器名稱分組的設定，例如 { trend: { minR2: 0.7 } }
 * @returns {object[]} 額外掛一個**不可列舉**的 .errors（每個偵測器的失敗紀錄）
 *
 * 為什麼 errors 掛成不可列舉屬性：呼叫端契約說好回「一個攤平的候選陣列」，
 * 改成 { candidates, errors } 會逼下游全部改寫。不可列舉代表 JSON.stringify、
 * 展開運算子、深度比對全部看不到它，它就只是一個給人查的旁註。
 *
 * 單一偵測器丟例外不會中斷整批——29 個區裡有一個區的資料是壞的，
 * 不該讓另外 28 個區的分析全部消失（現場最容易發生的 demo 事故）。
 */
export function mineAll(seriesList, meta = {}, opts = {}) {
	const list = Array.isArray(seriesList) ? seriesList : [seriesList];
	const base = {
		metric: meta.metric,
		metric_label: meta.metric_label || meta.label,
	};
	const errors = [];
	const found = [];

	const run = (detector, scope, fn) => {
		try {
			found.push(...fn());
		} catch (err) {
			errors.push({ detector, scope, message: (err && err.message) ? err.message : String(err) });
		}
	};

	for (const s of list) {
		const scope = (s && typeof s === "object" && typeof s.key === "string") ? s.key : "(未命名 series)";
		run("trend", scope, () => detectTrend(s, { ...base, ...(opts.trend || {}) }));
		run("change_point", scope, () => detectChangePoint(s, { ...base, ...(opts.change_point || {}) }));
		run("outlier", scope, () => detectOutlier(s, { ...base, ...(opts.outlier || {}) }));
	}
	if (list.length >= 2) {
		run("group_difference", "*", () => detectGroupDifference(list, { ...base, ...(opts.group_difference || {}) }));
		run("rank_change", "*", () => detectRankChange(list, { ...base, ...(opts.rank_change || {}) }));
	}

	const seen = new Map();
	for (const c of found) {
		const sig = signature(c);
		const prev = seen.get(sig);
		// 留效果較大的那一筆；完全同分時留先出現的（迴圈順序固定 → 決定性）
		if (!prev || c.effect_size > prev.effect_size) seen.set(sig, c);
	}

	// 固定排序。決定性靠的是「每一層都有 tie-break」，最後一層用 facts 的
	// JSON 當保險——只要有任何一層可能並列而沒有下一層，輸出順序就會飄。
	const out = [...seen.values()].sort((a, b) =>
		b.effect_size - a.effect_size
		|| b.statistical_strength - a.statistical_strength
		|| TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type)
		|| cmpStr(a.evidence.series_keys.join(","), b.evidence.series_keys.join(","))
		|| cmpStr(JSON.stringify(a.facts), JSON.stringify(b.facts)));

	errors.sort((a, b) => cmpStr(a.detector, b.detector) || cmpStr(a.scope, b.scope));
	Object.defineProperty(out, "errors", { value: Object.freeze(errors), enumerable: false });
	return out;
}
