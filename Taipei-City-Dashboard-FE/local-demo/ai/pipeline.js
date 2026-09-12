// Insight Pipeline：把五個模組串成一條路
//
//   catalog.js  ─┐
//   age.js      ─┼→ 決定「哪些年齡組距可以用、能不能加」
//   series.js   ─┼→ 把資料庫變成純陣列（順手帶出 SQL 當證據）
//   mining.js   ─┼→ 決定性地找出候選現象（不產生任何文字）
//   insight.js  ─┼→ 先關卡後評分，組成 InsightArtifact
//   report.js   ─┘→ 把同一個 artifact 渲染成 Markdown
//
// 這支檔案本身不做任何統計，也不產生任何數值。它只負責順序與錯誤處理。
// 分成這樣不是為了好看：每一段都能單獨測試，而 mining 那段完全不碰資料庫，
// 所以在沒有 PostgreSQL 的機器上也能跑。
//
// 對應 blueprint §16 的執行流程與 §17.1 的漏斗數字。

import { loadCatalog } from "./catalog.js";
import { binsFromCatalog, selectYouthBins, canAggregate, describeScope, scopeLimitations } from "./age.js";
import { fetchDistrictYearSeries, fetchDistrictSnapshot, fetchSplitYearSeries } from "./series.js";
import { mineAll } from "./mining.js";
import { runFunnel } from "./insight.js";
import { renderReport } from "./report.js";

/**
 * 一次分析。
 *
 * @param {object}  opts
 * @param {string}  opts.table      資料表名
 * @param {string} [opts.city]      metrotaipei / taipei
 * @param {string} [opts.geography] 顯示用地區名，例如 "新北市"
 * @param {string} [opts.target]    年齡目標，預設取最寬的合法青年範圍（15-39）
 * @param {number} [opts.topN]      推薦幾個，預設 5
 * @param {string} [opts.db]
 * @returns {Promise<{funnel, artifacts, markdown, ageScope, errors}>}
 */
export async function runInsightPipeline(opts = {}) {
	const {
		table = "youth_population_district_newtaipei",
		geography = "新北市",
		target = null,
		topN = 5,
		db,
	} = opts;

	const errors = [];
	const funnel = { datasets: 0, age_ok: 0, candidates: 0, gated: 0, validated: 0, recommended: 0 };

	// ── 1. 語意脈絡 ──────────────────────────────────────────
	const catalog = await loadCatalog(db);
	const meta = catalog.tables?.[table];
	if (!meta) throw new Error(`catalog 沒有這張表：${table}`);
	funnel.datasets = Object.keys(catalog.tables || {}).length;

	// ── 2. 年齡適用性 ────────────────────────────────────────
	// 這是 gate 不是 score：年齡不合法的資料，再有趣也不能進報告。
	const bins = binsFromCatalog(meta);
	const ageScope = selectYouthBins(bins, target ? { target } : undefined);
	const columns = ageScope.bins.map((b) => b.column);

	// 只有可加總的量才能跨組距相加。人口數可以；比例不行，會需要分子分母。
	//
	// label 刻意不含年齡範圍。渲染時 report.js 會自己冠上「15-39 歲」，
	// 這裡再寫一次就會變成「15-39 歲15-39 歲人口」。年齡範圍只有一個出處。
	const metricDef = { type: "count", label: "人口數", column: columns.join("+") };
	const agg = canAggregate(ageScope.bins, metricDef);
	if (!agg.ok) throw new Error(`年齡組距不可加總：${agg.reason}`);
	funnel.age_ok = 1;

	// ── 3. 取資料 ────────────────────────────────────────────
	// 三種切面，對應不同類型的現象：
	//   各區時間序列 → 趨勢、轉折
	//   最新年度橫斷面 → 異常區
	//   性別切分     → 群體差異
	const cuts = [];
	const pull = async (name, fn, kind, tag = {}) => {
		try {
			const r = await fn();
			if (r.series.length) cuts.push({ name, kind, ...tag, ...r });
		} catch (err) {
			errors.push(`${name}: ${err.message || err}`);
		}
	};

	// geoOf / qualifyOf 決定每一則 insight 該掛在誰頭上。
	//
	// 這不是標籤問題，是正確性問題。「各區逐年」這個切面產出的每一個轉折點
	// 都只屬於**一個行政區**；如果一律標成「新北市」，報告會拿板橋的數字
	// 宣稱全市的結論。實測第一版就是這樣：兩則轉折分別是 17 萬與 3 萬的量級，
	// 明顯是不同的區，標題卻都寫新北市。
	//
	// 判準是候選涉及幾條序列：只有一條就是那一條的事；跨序列比較（群體差異、
	// 排名變化）才是市級現象。
	const oneKey = (c) => {
		const keys = c?.evidence?.series_keys;
		return Array.isArray(keys) && keys.length === 1 ? keys[0] : null;
	};

	await pull("各區逐年", () => fetchDistrictYearSeries({ table, columns, db }), "time", {
		// 單一序列 = 單一行政區；跨序列 = 區與區之間的比較，屬於全市層級
		geoOf: (c) => oneKey(c) || geography,
	});
	await pull("最新年度分區", () => fetchDistrictSnapshot({ table, columns, db, label: "人口數" }), "cross", {
		// 這個切面的 x 軸就是行政區，整條序列描述的是全市的分布
		geoOf: () => geography,
	});
	await pull("性別逐年", () => fetchSplitYearSeries({ table, columns, db, splitValues: ["男", "女"] }), "time", {
		geoOf: () => geography,
		// 性別必須寫進指標名稱。少了它，「男」那條序列的結論會被讀成男女合計——
		// 這正是 ComponentSpec 早期「同一欄位不同篩選值」表達不出來時犯過的錯。
		qualifyOf: (c) => (oneKey(c) ? `${oneKey(c)}性` : "男女比較"),
	});

	if (!cuts.length) throw new Error(`取不到任何資料：${errors.join("; ") || "未知原因"}`);

	// ── 4. 探勘 ──────────────────────────────────────────────
	// mining 是純函式，不知道資料庫存在。一個切面失敗不該拖垮其他切面。
	const found = [];
	for (const cut of cuts) {
		try {
			const cands = mineAll(cut.series, { metric: metricDef.column, metric_label: metricDef.label }, { kind: cut.kind });
			for (const c of cands) found.push({ candidate: c, cut });
		} catch (err) {
			errors.push(`探勘「${cut.name}」失敗: ${err.message || err}`);
		}
	}
	funnel.candidates = found.length;

	// ── 5. 關卡、評分、排序 ──────────────────────────────────
	// 全部交給 runFunnel。順序是刻意的：先過關卡再評分。
	// 反過來的話，一個「很驚奇」的分數可能把年齡不合法的候選推進報告——
	// 那正是本系統最該避免的事。
	//
	// 被擋下的候選不會變成 artifact（buildInsightArtifact 直接 throw），
	// 所以下游不可能因為忘了檢查 status 就把它印出來。
	const timeRange = summariseTimeRange(cuts);
	const inputs = found.map(({ candidate, cut }) => {
		const geo = cut.geoOf ? cut.geoOf(candidate) : geography;
		const qualifier = cut.qualifyOf ? cut.qualifyOf(candidate) : null;
		return {
			candidate,
			ageScope,
			geography: geo,
			timeRange,
			datasetIds: [table],
			sql: cut.sql,
			queryId: `${cut.kind}_${candidate.type}`,
			// 修飾詞（例如「女性」）併進指標名稱，讓它跟著標題與結論一起走，
			// 不會在某一段渲染裡被漏掉
			metricDef: qualifier ? { ...metricDef, label: `${qualifier}${metricDef.label}` } : metricDef,
		};
	});

	// 先要全部通過驗證的，分散之後才截斷。
	// 直接讓 runFunnel 取 topN 會得到「五則趨勢轉折、只是換五個行政區」——
	// 實測第一版就是這樣。每一則單獨看都合格，合起來卻沒有回答
	// 「值得注意的現象有哪些」，只回答了「哪五個區的轉折最劇烈」。
	const result = runFunnel(inputs, { topN: Number.MAX_SAFE_INTEGER });
	const picked = diversify(result.artifacts, topN);

	funnel.gated = result.passed_definition;
	funnel.validated = result.passed_statistical;
	funnel.recommended = picked.length;
	result.artifacts = picked;

	const summary = {
		funnel: { ...funnel, ...result },
		age_scope: ageScope.label,
		age_classification: ageScope.classification,
		age_note: describeScope(ageScope),
		limitations: scopeLimitations(ageScope),
		geography,
		time: timeRange,
	};

	return {
		funnel: { ...funnel, by_gate: result.by_gate },
		ageScope,
		artifacts: result.artifacts,
		rejected: result.rejected,
		markdown: renderReport(result.artifacts, summary),
		errors,
	};
}

/**
 * 依分數排序後，讓入選的 insight 型別分散。
 *
 * 做法是輪流取：先把每種型別最高分的取一輪，再取第二輪，依此類推。
 * 同型別內仍照分數排，所以「該型別最值得看的那一則」一定先入選。
 *
 * 為什麼需要：分數高的候選會擠在同一種型別。29 個行政區各自有轉折點，
 * 前 20 名可能全是轉折點。使用者要的是「有哪些值得注意的現象」，
 * 不是「轉折最劇烈的前五個區」——後者只回答了一個更窄的問題。
 *
 * 這一步只**重排與截斷**，不改分數、不放行任何未通過關卡的候選。
 */
function diversify(artifacts, topN) {
	const byType = new Map();
	for (const a of artifacts) {
		const t = a.insight_type || "unknown";
		if (!byType.has(t)) byType.set(t, []);
		byType.get(t).push(a);
	}
	// 型別的出場順序依該型別最高分決定，分數高的型別先輪到
	const queues = [...byType.values()].sort((x, y) => (y[0]?.validation?.score ?? 0) - (x[0]?.validation?.score ?? 0));

	const out = [];
	let round = 0;
	while (out.length < topN) {
		let added = false;
		for (const q of queues) {
			if (q.length > round) { out.push(q[round]); added = true; }
			if (out.length >= topN) break;
		}
		if (!added) break;   // 每個型別都取完了
		round++;
	}
	return out.slice(0, topN);
}

/** 時間範圍取所有切面的聯集。只有橫斷面時回傳該年度。 */
function summariseTimeRange(cuts) {
	const xs = [];
	for (const c of cuts) {
		for (const s of c.series) {
			for (const p of s.points) if (typeof p.x === "number") xs.push(p.x);
		}
		if (c.year != null) xs.push(c.year);
	}
	if (!xs.length) return "不詳";
	const lo = Math.min(...xs), hi = Math.max(...xs);
	return lo === hi ? String(lo) : `${lo}-${hi}`;
}
