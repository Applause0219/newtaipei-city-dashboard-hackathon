// spec → 驗證 → 探測順序 → 編譯 → 執行 → 檢查對齊 → 預覽
//
// 「編出來一定先跑過再說」這一步，是 AimchartAI 架構圖 Δ4 留下那個問題的答案：
// 模型不碰數字，但我們也不無條件相信它的 spec。SQL 一定先執行、驗過對齊，
// 才會拿去註冊或顯示給人看。
import { validateSpec, compileSpec, orderProbeSQL, normalizeSpec, whereClause } from "./component-spec.js";
import { loadCatalog, query } from "./catalog.js";
import { buildSpecPrompt } from "./spec-prompt.js";
import { generateSpec } from "./provider.js";
import { findRelated } from "./official.js";

/** 值不值得畫成圖。門檻依「聚合後的資料點數」判斷，不是原始筆數。 */
const CHARTABLE = {
	minDataPoints: 4,          // 3 個以下直接給數字就好
	minCategories: 2,          // 一個分類的長條圖沒有比較的對象
	maxSeries: 8,              // component_charts.color 給的顏色會用完
};

/**
 * @param {object} spec  模型產出的 ComponentSpec
 * @returns {Promise<{ok:boolean, stage?:string, errors?:string[], sql?:string, chart?:object, stats?:object}>}
 */
export async function generateComponent(rawSpec, { db } = {}) {
	// 單一數列的 three_d 要降成 two_d，否則 DistrictChart 會讀錯格式
	const spec = normalizeSpec(rawSpec);
	const catalog = await loadCatalog(db);
	if (catalog.issues.length) {
		// 不擋，但要讓人看得到——catalog 與資料庫對不上是會慢慢腐爛的問題
		console.warn("[catalog] " + catalog.issues.join("\n[catalog] "));
	}

	const v = validateSpec(spec, catalog);
	if (!v.ok) return { ok: false, stage: "validate", errors: v.errors };

	// 數值在資料庫是 real，聚合會拖出浮點尾巴（實測 1198.151712435919）。
	// 模型不必操心這種事，我們依型別自動補上小數位。
	//
	// 寬表看欄位型別就夠了。長表不行——欄位永遠是 value，型別永遠是 count，
	// 真正的型別在「該指標的 value_type」裡（rate / index / mean / median）。
	// 少了這一段，跨領域的圖 y 軸會顯示 28000.0000000000000。
	const meta = catalog.tables?.[spec.table]?.fields || {};

	/** 這個數列實際是哪一種量：長表查指標，寬表查欄位。 */
	const seriesValueType = (sr) => {
		const ind = sr.filter?.column === "indicator_id" ? sr.filter.eq : null;
		if (ind && catalog.youthDatasets) {
			for (const d of Object.values(catalog.youthDatasets)) {
				const vt = d.indicators?.[ind]?.value_type;
				if (vt) return vt;
			}
		}
		return meta[sr.column]?.type;
	};

	// count／sum 是整數個體，取平均才會有小數；其餘本來就是連續量。
	const NON_INTEGER = new Set(["ratio", "rate", "index", "mean", "median"]);
	const types = spec.series.map(seriesValueType);
	const needRound =
		types.every((t) => NON_INTEGER.has(t)) ||          // 本來就是連續量
		(spec.aggregate === "avg" || spec.aggregate === "sum" && types.some((t) => NON_INTEGER.has(t)));
	if (needRound && !spec.transform?.divide && !Number.isInteger(spec.transform?.round)) {
		spec.transform = { ...(spec.transform || {}), round: 1 };
	}

	// 這份資料到底是哪一年的——一定要說出來。
	//
	// 兩張新北的表最新年份不同（2023 / 2024），模型挑哪張使用者看不到，
	// 於是同一個問題換個問法會得到差一萬六的兩個數字，兩個都對。
	// 現場被問「剛才不是 755 嗎」而答不出來，比數字本身更糟。
	let period = null;
	if (spec.latest_by) {
		try {
			// 必須帶上**每個數列自己的**篩選條件，不只是 spec.filters。
			//
			// 指標的篩選（indicator_id = ...）住在 series[].filter 裡，
			// 不在 spec.filters。只帶 spec.filters 算出來的是
			// 「所有區級資料的最新日期」——租金畫的是 2026-07，
			// 標籤卻寫 2026-09，而那是另一個資料集的日期。
			//
			// 排除 latest_by 本身，否則子查詢會自我參照。
			const bare = { ...spec, latest_by: undefined };
			const seen = [];
			for (const sr of spec.series) {
				const w = whereClause(bare, sr.filter ? [sr.filter] : []);
				const [r] = await query(
					`SELECT max("${spec.latest_by}") AS v FROM public."${spec.table}" ${w}`, db);
				if (r?.v != null) seen.push(String(r.v));
			}
			const uniq = [...new Set(seen)].sort();
			// 各數列期間不同時照實說出範圍，不要挑一個當代表
			period = uniq.length === 0 ? null
				: uniq.length === 1 ? uniq[0]
				: `${uniq[0]} – ${uniq[uniq.length - 1]}`;
		} catch { /* 問不到就不寫，不要因此讓生成失敗 */ }
	} else {
		const cov = catalog.tables?.[spec.table]?.coverage;
		if (cov) period = `${cov.from}–${cov.to}`;
	}

	// 探測 x 軸的固定順序（分類軸依加總降冪，時間軸依時序）
	let probe;
	try {
		probe = await query(orderProbeSQL(spec, catalog), db);
	} catch (err) {
		return { ok: false, stage: "probe", errors: [cleanErr(err)] };
	}
	const key = Object.keys(probe[0] || {})[0];
	const xOrder = [...new Set(probe.map((r) => r[key]))];
	if (xOrder.length === 0) {
		return { ok: false, stage: "probe", errors: ["查不到任何資料列，過濾條件可能把資料全部濾掉了"] };
	}

	const sql = compileSpec(spec, xOrder);

	let rows;
	try {
		rows = await query(sql, db);
	} catch (err) {
		return { ok: false, stage: "execute", errors: [cleanErr(err)], sql };
	}

	// 模擬後端的編譯（componentData.go:274-327）。
	//
	// two_d 的輸出形狀跟 three_d 不同：[{data:[{x,y}]}]，沒有 categories。
	// 這不是我們自己定的，是官方後端就這樣分。
	const isTwoD = spec.query_type === "two_d";
	if (isTwoD) {
		const pts = rows.map((r) => ({ x: r.x_axis, y: Number(r.data) }));
		const chart = { data: [{ data: pts }], status: "success" };
		const stats = { rows: pts.length, categories: pts.length, series: 1,
		                total: pts.reduce((a, p) => a + p.y, 0),
		                table: spec.table, period };
		const why2 = [];
		if (pts.length < CHARTABLE.minDataPoints) why2.push(`只有 ${pts.length} 個資料點`);
		if (pts.length < CHARTABLE.minCategories) why2.push(`只有 ${pts.length} 個分類，沒有比較的對象`);
		if (why2.length) {
			return { ok: true, chartable: false, sql, chart, stats, reasons: why2, spec,
			         warnings: spec._warnings,
			         facts: pts.map((p) => ({ label: p.x, value: p.y, unit: spec.chart?.unit || "" })) };
		}
		return { ok: true, chartable: true, sql, chart, stats, spec, warnings: spec._warnings };
	}

	// three_d：categories 依 x_axis 首次出現順序，各系列按列序 append（不對應 category）
	const categories = [];
	const series = new Map();
	for (const r of rows) {
		const x = r.x_axis, y = r.y_axis, d = Number(r.data);
		if (!categories.includes(x)) categories.push(x);
		if (!series.has(y)) series.set(y, []);
		series.get(y).push([x, d]);
	}

	// 分隔符刻意用 NUL 而不是空格：行政區名理論上可能含空格，
	// 用空格接會讓「A B」與「A  B」比對不出差異。
	//
	// 但一定要寫成 \u0000 跳脫，不能是生位元組——生位元組會讓整個檔案
	// 被判定為二進位，帶 -I 的 grep 會靜默跳過它，查不到卻不報錯。
	const misaligned = [...series.entries()]
		.filter(([, v]) => v.map(([x]) => x).join("\u0000") !== categories.join("\u0000"))
		.map(([k]) => k);
	if (misaligned.length) {
		return { ok: false, stage: "align", sql,
			errors: [`這些系列的 x 軸順序與 categories 不一致，後端會把數值對到錯的項目：${misaligned.join("、")}`] };
	}

	const chart = {
		categories,
		data: [...series.entries()].map(([name, v]) => ({ name, icon: "", data: v.map(([, d]) => d) })),
		status: "success",
	};
	const stats = {
		rows: rows.length,
		categories: categories.length,
		series: series.size,
		total: rows.reduce((a, r) => a + Number(r.data), 0),
		table: spec.table,
		period,
	};

	// 值不值得畫成圖。判準用「有沒有算出值」而不是「值是不是 0」——
	// 後者會讓真實的 0 消失，而我們的資料裡就有 5 個區四捨五入後是 0。
	const points = rows.filter((r) => r.data !== null && r.data !== undefined).length;
	const why = [];
	if (points < CHARTABLE.minDataPoints) why.push(`只有 ${points} 個資料點`);
	if (categories.length < CHARTABLE.minCategories) why.push(`只有 ${categories.length} 個分類，沒有比較的對象`);
	if (series.size > CHARTABLE.maxSeries) why.push(`${series.size} 個數列超過配色能區分的範圍`);

	if (why.length) {
		// 「不畫圖」不等於「沒答案」：把數字整理成 facts 直接回給使用者
		return {
			ok: true, chartable: false, sql, chart, stats, reasons: why, spec,
			warnings: spec._warnings,
			facts: rows.map((r) => ({
				label: series.size > 1 ? `${r.x_axis}・${r.y_axis}` : String(r.x_axis),
				value: Number(r.data),
				unit: spec.chart?.unit || "",
			})),
		};
	}

	return { ok: true, chartable: true, sql, chart, stats, spec, warnings: spec._warnings };
}

function cleanErr(err) {
	return String(err.stderr || err.message).split("\n").filter((l) => l.trim()).slice(0, 3).join(" ");
}


/**
 * 從自然語言問題一路做到可預覽的組件。
 * 這是 chatbot 會呼叫的入口。
 *
 * @param {string} question 使用者的問題
 */
export async function generateFromQuestion(question, { db } = {}) {
	const catalog = await loadCatalog(db);
	const prompt = buildSpecPrompt(catalog, question);

	const { spec, refuse, model, raw } = await generateSpec(prompt);
	if (refuse) return { ok: false, stage: "spec", model, errors: [refuse], raw };

	const result = await generateComponent(spec, { db });

	// 官方是不是已經有了？查出來一併回傳，讓使用者自己選，
	// 而不是我們默默生一個重複的（或默默不生）。
	const related = result.ok ? await findRelated(result.spec || spec, catalog) : { known: true, items: [] };

	// 注意順序：result.spec 是正規化後的版本（可能已從 three_d 降成 two_d），
	// 不能被原始 spec 蓋掉，否則前端會照錯的 query_type 去組圖表
	return { spec, ...result, model, related };
}
