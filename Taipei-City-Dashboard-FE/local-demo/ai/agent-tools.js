// Agent 的工具箱。
//
// 設計原則只有一條：**工具決定不了數字，只決定要算什麼。**
//
//   模型      決定呼叫哪個工具、用什麼參數
//   工具      跑既有的決定性程式碼（validateSpec → compileSpec → SQL）
//   資料庫    算出數字
//
// 所以「LLM 不碰數字」這件事在 agent 路徑上依然成立，而且多了一層好處：
// 那 8 道守門變成 agent 的**回饋**。它寫錯 spec 會收到錯誤訊息然後自己修，
// 不像 one-shot 那樣整個請求失敗。
//
// 對應藍圖 §6.2 的 tool 清單，先做真正用得到的六個。
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { generateComponent } from "./generate.js";
import { loadCatalog, query } from "./catalog.js";
import { loadOfficial } from "./official.js";

const run = promisify(execFile);
const lit = (s) => "'" + String(s).replace(/'/g, "''") + "'";

/** 工具回傳的統一形狀（pi-agent-core 的 AgentToolResult） */
const result = (obj, details = {}) => ({
	content: [{ type: "text", text: JSON.stringify(obj) }],
	details,
});

/**
 * 建立工具集。
 * @param {object} catalog   loadCatalog() 的結果
 * @param {function} onCall  每次呼叫時通知（給前端畫漏斗用）
 */
export function buildTools(catalog, onCall = () => {}) {
	const trace = [];
	const note = (name, args, summary) => {
		const rec = { name, args, summary, at: Date.now() };
		trace.push(rec);
		try { onCall(rec); } catch { /* 通知失敗不能影響工具本身 */ }
	};

	// ── 1. 找資料 ──
	const searchIndicators = {
		name: "search_indicators",
		label: "搜尋指標",
		description:
			"用關鍵字搜尋可用的統計指標。中文英文都可以——中文會比對資料集的中文名稱，" +
			"英文會比對指標代號。回傳指標代號、所屬資料集、中文名、地理層級、期間。" +
			"這是探索資料的第一步，回答任何問題前都應該先搜尋。",
		parameters: {
			type: "object",
			properties: {
				keyword: { type: "string", description: "關鍵字，例如「租金」「失業」「income」" },
			},
			required: ["keyword"],
		},
		async execute(_id, p) {
			const kw = String(p?.keyword ?? "").trim();
			if (!kw) return result({ error: "keyword 不能是空的" });

			// 中文關鍵字要比對中文標籤——指標代號全是英文，
			// 只比對代號會讓「失業率」查不到 youth_unemployment_rate_age。
			const hits = new Set();
			for (const d of Object.values(catalog.youthDatasets || {})) {
				const dsMatch = (d.label || "").includes(kw)
					|| (d.topics || []).some((t) => t.includes(kw));
				for (const ind of Object.keys(d.indicators || {})) {
					if (dsMatch || ind.toLowerCase().includes(kw.toLowerCase())) hits.add(ind);
				}
			}
			const extra = hits.size ? ` OR indicator_id IN (${[...hits].map(lit).join(",")})` : "";
			const rows = await query(`
				SELECT indicator_id, dataset_id,
				       string_agg(DISTINCT area_level, '/' ORDER BY area_level) AS area_levels,
				       min(period_start)::text AS period_from,
				       max(period_end)::text   AS period_to
				FROM public.youth_fact_named
				WHERE indicator_id ILIKE ${lit("%" + kw + "%")}
				   OR dataset_id  ILIKE ${lit("%" + kw + "%")}${extra}
				GROUP BY 1, 2 ORDER BY 1 LIMIT 30`);
			for (const r of rows) {
				r.label = catalog.youthDatasets?.[r.dataset_id]?.label || "";
			}
			note("search_indicators", { keyword: kw }, `找到 ${rows.length} 個指標`);
			return result(rows.length ? rows : { found: 0, hint: "換個關鍵字，或用 list_domains 看有哪些主題" },
				{ n: rows.length });
		},
	};

	// ── 2. 看清楚這個指標能不能用 ──
	const inspectIndicator = {
		name: "inspect_indicator",
		label: "檢視指標",
		description:
			"查一個指標的細節：有哪些地理層級、性別值、年齡區間、時間範圍、列數。" +
			"**要做分區圖之前一定要先查**——很多指標只有全國層級，畫不出分區圖。",
		parameters: {
			type: "object",
			properties: { indicator_id: { type: "string" } },
			required: ["indicator_id"],
		},
		async execute(_id, p) {
			const ind = String(p?.indicator_id ?? "");
			const rows = await query(`
				SELECT dataset_id,
				       string_agg(DISTINCT area_level, '/' ORDER BY area_level)   AS area_levels,
				       string_agg(DISTINCT coalesce(gender,'(null)'), '/')        AS genders,
				       string_agg(DISTINCT value_type, '/' ORDER BY value_type)   AS value_types,
				       string_agg(DISTINCT unit, '/')                             AS units,
				       min(age_lower)::text AS age_lower_min,
				       max(age_upper)::text AS age_upper_max,
				       min(period_start)::text AS period_from,
				       max(period_end)::text   AS period_to,
				       count(*) AS rows
				FROM public.youth_fact_named
				WHERE indicator_id = ${lit(ind)}
				GROUP BY 1`);
			if (!rows.length) {
				note("inspect_indicator", { indicator_id: ind }, "查無此指標");
				return result({ error: `查無指標 ${ind}`, hint: "先用 search_indicators 確認代號" });
			}
			for (const r of rows) r.label = catalog.youthDatasets?.[r.dataset_id]?.label || "";
			// 跨資料集重複的指標必須同時指定 dataset_id，否則數字會相加
			if (catalog.ambiguousIndicators?.has(ind)) {
				rows[0].warning = "這個 indicator_id 在多個資料集裡都有，"
					+ "建立組件時必須同時篩 dataset_id，否則會把不同資料集的數字加在一起。";
			}
			note("inspect_indicator", { indicator_id: ind },
				`${rows[0].area_levels}｜${rows[0].rows} 列`);
			return result(rows.length === 1 ? rows[0] : rows, {});
		},
	};

	// ── 3. 直接取數字（不建組件，只是看看） ──
	//
	// 這個工具跟 build_component 讀同一張長表，但原本一道守門都沒有——
	// 於是 agent 寫在**文字裡**的數字，可以違反圖表數字必須遵守的每一條規則。
	//
	// 2026-09-12 實測：population_count 同時存在於 2 個資料集、2 個地理層級、
	// 3 種性別值。全部不篩就一起加，分母算出 32,376,008（新北市實際約 400 萬），
	// 青年人口佔比報成 11.7%，真值 15.0%。
	// validateSpec 擋得下來的錯，文字照樣講得出口——所以守門要放在這裡，不是只放在圖表那條路。
	const queryIndicator = {
		name: "query_indicator",
		label: "查數值",
		description:
			"取某個指標的實際數值。用來在建組件前確認資料長什麼樣，或回答不需要圖表的問題。\n" +
			"by 決定分組方式：district（各行政區）或 period（歷年）。\n" +
			"指標橫跨多個資料集時**必須**指定 dataset_id，否則會被擋下來。\n" +
			"不指定 area_level 預設用 district；該指標有 total 性別時不指定 gender 會自動用 total。" +
			"實際生效的條件都會寫在回傳結果的 applied 欄位裡。",
		parameters: {
			type: "object",
			properties: {
				indicator_id: { type: "string" },
				by: { type: "string", description: "district 或 period" },
				dataset_id: { type: "string", description: "指標橫跨多個資料集時必填" },
				area_level: { type: "string", description: "district / city / country，預設 district" },
				age_lower: { type: "number", description: "可選，年齡下界（含）" },
				age_upper: { type: "number", description: "可選，年齡上界（含）" },
				gender: { type: "string", description: "可選，先用 inspect_indicator 確認有哪些值" },
			},
			required: ["indicator_id", "by"],
		},
		async execute(_id, p) {
			const ind = String(p?.indicator_id ?? "");
			const by = p?.by === "period" ? "period" : "district";
			const bad = (summary, obj) => { note("query_indicator", { indicator_id: ind, by }, summary); return result(obj); };

			// 先問清楚這個指標長什麼樣，再決定要不要擋
			const shape = (await query(`
				SELECT string_agg(DISTINCT dataset_id, ',' ORDER BY dataset_id) AS datasets,
				       string_agg(DISTINCT area_level, ',' ORDER BY area_level) AS levels,
				       string_agg(DISTINCT gender, ',' ORDER BY gender)         AS genders
				FROM public.youth_fact_named WHERE indicator_id = ${lit(ind)}`))[0];
			if (!shape?.datasets) {
				return bad("查無此指標",
					{ error: `查無指標 ${ind}`, hint: "先用 search_indicators 確認代號" });
			}
			const datasets = shape.datasets.split(",");
			const levels = shape.levels.split(",");
			const genders = (shape.genders || "").split(",").filter(Boolean);

			// 跨資料集的指標一旦不指定，就是把不同母體加在一起。這種錯沒有補救辦法，只能擋。
			const ds = p?.dataset_id ? String(p.dataset_id)
				: (datasets.length === 1 ? datasets[0] : null);
			if (!ds) {
				return bad(`要指定 dataset_id（有 ${datasets.length} 個）`, {
					error: `${ind} 同時存在於 ${datasets.length} 個資料集，不指定會把不同母體加在一起`,
					datasets, hint: "挑一個 dataset_id 再查一次",
				});
			}
			if (!datasets.includes(ds)) {
				return bad("dataset_id 不存在", { error: `${ind} 不在資料集 ${ds} 裡`, datasets });
			}

			// 地理層級不篩，會把全國／全市／行政區的數字疊起來
			const lv = by === "district" ? "district"
				: (p?.area_level ? String(p.area_level)
					: (levels.includes("district") ? "district" : levels[0]));
			if (!levels.includes(lv)) {
				return bad(`沒有 ${lv} 層級`, {
					error: `${ind} 沒有 ${lv} 層級` + (by === "district" ? "，畫不出分區圖" : ""),
					area_levels: levels,
				});
			}

			// 有 total 又不篩 gender＝total 跟 male/female 一起加，剛好兩倍
			let g = p?.gender ? String(p.gender) : null;
			if (g && !genders.includes(g)) {
				return bad(`沒有 gender=${g}`, {
					error: `${ind} 沒有 gender=${g} 這個值，硬篩會得到空結果`, genders,
				});
			}
			if (!g && genders.includes("total")) g = "total";

			const conds = [`indicator_id = ${lit(ind)}`, `dataset_id = ${lit(ds)}`, `area_level = ${lit(lv)}`];
			if (g) conds.push(`gender = ${lit(g)}`);
			if (p?.age_lower != null) conds.push(`age_lower >= ${Number(p.age_lower)}`);
			if (p?.age_upper != null) conds.push(`age_upper <= ${Number(p.age_upper)}`);
			const where = "WHERE " + conds.join(" AND ");

			// 分區看最新一期；歷年看全部
			const latest = by === "district"
				? ` AND period_start = (SELECT max(period_start) FROM public.youth_fact_named ${where})`
				: "";
			const key = by === "district" ? "area_name" : "period_start::text";
			const rows = await query(`
				SELECT ${key} AS key, round(sum(value)::numeric, 2) AS value
				FROM public.youth_fact_named ${where}${latest}
				GROUP BY 1 ORDER BY ${by === "district" ? "2 DESC" : "1"} LIMIT 120`);

			const age = p?.age_lower != null || p?.age_upper != null
				? `${p?.age_lower ?? ""}-${p?.age_upper ?? ""} 歲` : "";
			note("query_indicator", {
				indicator_id: ind, by, dataset_id: ds, area_level: lv,
				...(age ? { age } : {}), ...(g ? { gender: `gender=${g}` } : { gender: "未篩性別" }),
			}, rows.length ? `${rows.length} 筆` : "0 筆（條件可能把資料濾光）");

			// applied 是給模型看的：它引用數字時要知道這批數字的口徑是什麼
			return result({
				applied: { dataset_id: ds, area_level: lv, gender: g || "(不篩，此指標無 total)", age: age || "(全年齡)" },
				rows: rows.length ? rows : undefined,
				...(rows.length ? {} : { hint: "查無資料，先用 inspect_indicator 確認年齡區間與期間" }),
			}, { n: rows.length });
		},
	};

	// ── 4. 建組件：走的是既有那條決定性管線 ──
	const buildComponent = {
		name: "build_component",
		label: "建立組件",
		description:
			"用 ComponentSpec 建一個儀表板組件。spec 會經過驗證、編譯成 SQL、實際執行、檢查對齊。" +
			"**驗證失敗時會回傳具體原因，你可以修正後再試一次。**\n" +
			"youth_fact_named 是長表：必須填 aggregate、必須篩 area_level，" +
			"要看哪個指標是用 series[].filter 指定 indicator_id（欄位固定是 value）。",
		parameters: {
			type: "object",
			properties: {
				spec: { type: "object", description: "ComponentSpec 物件" },
			},
			required: ["spec"],
		},
		async execute(_id, p) {
			const spec = p?.spec;
			if (!spec || typeof spec !== "object") {
				return result({ ok: false, error: "spec 必須是物件" });
			}
			// 同理：組件的篩選條件也要攤開，否則看不出這張圖算的是哪一群人
			const cond = (spec.filters || [])
				.map((f) => `${f.column}${"eq" in f ? "=" + f.eq
					: "ne" in f ? "≠" + f.ne
					: "gte" in f ? "≥" + f.gte
					: "lte" in f ? "≤" + f.lte : ""}`)
				.join(" ");
			const r = await generateComponent(spec);
			if (!r.ok) {
				note("build_component", { name: spec.name, cond }, `失敗（${r.stage}）`);
				// 錯誤要完整回去，agent 才修得動
				return result({ ok: false, stage: r.stage, errors: r.errors });
			}
			note("build_component", { name: spec.name, cond },
				r.chartable === false ? "資料太少不畫圖"
					: `${r.stats.categories}×${r.stats.series}`);
			return result({
				ok: true,
				chartable: r.chartable !== false,
				name: spec.name,
				stats: r.stats,
				reasons: r.reasons,
				warnings: r.warnings,
				// 數值不回給模型——它不需要，回了反而誘導它自己算
				preview: (r.chart?.categories || r.chart?.data?.[0]?.data || []).slice(0, 5),
			}, { component: r });
		},
	};

	// ── 5. 官方已經有了嗎 ──
	const listOfficial = {
		name: "list_official_components",
		label: "查官方組件",
		description:
			"用關鍵字查官方儀表板已經有哪些組件。建新組件前應該先查，" +
			"避免做出重複的東西，也可以告訴使用者「官方已經有了」。",
		parameters: {
			type: "object",
			properties: { keyword: { type: "string" } },
			required: ["keyword"],
		},
		async execute(_id, p) {
			const kw = String(p?.keyword ?? "");
			const all = await loadOfficial();
			if (all === null) {
				note("list_official_components", { keyword: kw }, "連不上官方 API");
				return result({ known: false, hint: "查不到官方清單（離線），不代表官方沒有。" });
			}
			const hit = all.filter((c) =>
				(c.name || "").includes(kw) || (c.index || "").includes(kw.toLowerCase()));
			note("list_official_components", { keyword: kw }, `${hit.length} 個`);
			return result(hit.slice(0, 12).map((c) => ({
				name: c.name, index: c.index, city: c.city, query_type: c.query_type,
			})), { n: hit.length });
		},
	};

	// ── 6. 有哪些主題可以問 ──
	const listDomains = {
		name: "list_domains",
		label: "列出主題",
		description: "列出資料涵蓋的主題與各自的指標數。不知道從哪開始時用這個。",
		parameters: { type: "object", properties: {}, required: [] },
		async execute() {
			const byTopic = {};
			for (const d of Object.values(catalog.youthDatasets || {})) {
				const n = Object.keys(d.indicators || {}).length;
				for (const t of d.topics?.length ? d.topics : ["(未分類)"]) {
					byTopic[t] = (byTopic[t] || 0) + n;
				}
			}
			const list = Object.entries(byTopic)
				.sort((a, b) => b[1] - a[1]).slice(0, 30)
				.map(([topic, n]) => ({ topic, indicators: n }));
			note("list_domains", {}, `${list.length} 個主題`);
			return result(list, { n: list.length });
		},
	};

	return {
		tools: [searchIndicators, inspectIndicator, queryIndicator,
			buildComponent, listOfficial, listDomains],
		trace,
	};
}
