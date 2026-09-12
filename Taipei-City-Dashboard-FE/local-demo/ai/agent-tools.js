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
			// 沒有 district 不等於「這個主題沒有分區資料」。
			// 實測：問「所得高的區結婚是不是也多」，模型 inspect 了只有 city 的
			// resident_marital_status_count，就宣布無法分區比較——
			// 而 marriage_count 有 district，26134 列，它從頭到尾沒搜過。
			if (!/district/.test(rows[0].area_levels || "")) {
				rows[0].before_you_say_no =
					"這個指標沒有 district 層級。宣告「沒有分區資料」之前，"
					+ "必須先用中文關鍵字呼叫 search_indicators 至少一次——"
					+ "同一個主題往往有另一個指標是有分區的。";
			}
			note("inspect_indicator", { indicator_id: ind },
				`${rows[0].area_levels}｜${rows[0].rows} 列`);
			return result(rows.length === 1 ? rows[0] : rows, {});
		},
	};

	// ── 3. 共用的「要看哪批數字」解析器 ──
	//
	// query / correlate / compare 三個工具走同一套守門。
	// 分開寫的話，只要有一條路徑漏掉守門，錯的數字就會從那裡流進文字答案——
	// 這正是 2026-09-12 那次 population_count 分母灌水 8 倍的成因。
	async function resolveSelector(sel, { forDistrict = false } = {}) {
		const ind = String(sel?.indicator_id ?? "");
		if (!ind) return { error: "要指定 indicator_id" };

		const shape = (await query(`
			SELECT string_agg(DISTINCT dataset_id, ',' ORDER BY dataset_id) AS datasets,
			       string_agg(DISTINCT area_level, ',' ORDER BY area_level) AS levels,
			       string_agg(DISTINCT gender, ',' ORDER BY gender)         AS genders
			FROM public.youth_fact_named WHERE indicator_id = ${lit(ind)}`))[0];
		if (!shape?.datasets) {
			return { error: `查無指標 ${ind}`, hint: "先用 search_indicators 確認代號" };
		}
		const datasets = shape.datasets.split(",");
		const levels = shape.levels.split(",");
		const genders = (shape.genders || "").split(",").filter(Boolean);

		// 「這個指標根本沒有分區資料」要比「該挑哪個資料集」更早講。
		// 不論挑哪個資料集都補不出 district，先講 dataset_id 只會讓模型多繞一圈，
		// 繞完還是撞牆，然後宣布「沒有分區資料」——那個結論常常是錯的。
		if (forDistrict && !levels.includes("district")) {
			return {
				error: `${ind} 沒有 district 層級，做不了分區比較`,
				area_levels: levels,
				hint: "宣告「無法分區比較」之前，先用中文關鍵字再 search_indicators 一次"
					+ "——同一個主題常常有另一個指標是有分區的",
			};
		}

		const ds = sel?.dataset_id ? String(sel.dataset_id)
			: (datasets.length === 1 ? datasets[0] : null);
		if (!ds) {
			return { error: `${ind} 同時存在於 ${datasets.length} 個資料集，不指定會把不同母體加在一起`,
				datasets, hint: "挑一個 dataset_id 再查一次" };
		}
		if (!datasets.includes(ds)) return { error: `${ind} 不在資料集 ${ds} 裡`, datasets };

		const lv = forDistrict ? "district"
			: (sel?.area_level ? String(sel.area_level)
				: (levels.includes("district") ? "district" : levels[0]));
		if (!levels.includes(lv)) {
			return { error: `${ind} 沒有 ${lv} 層級` + (forDistrict ? "，做不了分區比較" : ""),
				area_levels: levels,
				hint: forDistrict ? "宣告「無法分區比較」之前，先用中文關鍵字再 search_indicators 一次——同一個主題常常有另一個指標是有分區的" : undefined };
		}

		let g = sel?.gender ? String(sel.gender) : null;
		if (g && !genders.includes(g)) {
			return { error: `${ind} 沒有 gender=${g} 這個值，硬篩會得到空結果`, genders };
		}
		if (!g && genders.includes("total")) g = "total";

		const conds = [`indicator_id = ${lit(ind)}`, `dataset_id = ${lit(ds)}`, `area_level = ${lit(lv)}`];
		if (g) conds.push(`gender = ${lit(g)}`);
		if (sel?.age_lower != null) conds.push(`age_lower >= ${Number(sel.age_lower)}`);
		if (sel?.age_upper != null) conds.push(`age_upper <= ${Number(sel.age_upper)}`);

		// 期間：沒指定就用最新一期，而且要把**實際解析出來的日期**回報出去。
		// 原本不回報，模型只好拿 inspect_indicator 的期間範圍去猜，
		// 於是把 2025 單年的 17,547 標成「2020-2025 累計」（真正累計是 84,584）。
		let period = null;
		if (sel?.period && sel.period !== "all") {
			period = String(sel.period);
			conds.push(`period_start = ${lit(period)}`);
		} else if (sel?.period !== "all") {
			const w = "WHERE " + conds.join(" AND ");
			period = (await query(
				`SELECT max(period_start)::text AS p FROM public.youth_fact_named ${w}`))[0]?.p || null;
			if (period) conds.push(`period_start = ${lit(period)}`);
		}

		const age = sel?.age_lower != null || sel?.age_upper != null
			? `${sel?.age_lower ?? ""}-${sel?.age_upper ?? ""} 歲` : "(全年齡)";
		return {
			ind, conds,
			where: "WHERE " + conds.join(" AND "),
			applied: { indicator_id: ind, dataset_id: ds, area_level: lv,
				gender: g || "(不篩，此指標無 total)", age,
				period: period || "(全部期間)" },
		};
	}

	/**
	 * 把 applied 轉成給人看的 trace 標籤。
	 * 前端的「查詢過程」只印 args 的**值**、不印鍵（ChatBox.vue 的
	 * Object.values(...).join），所以值本身必須自帶標籤，
	 * 否則畫面上會出現一個孤零零的「total」，沒人看得出那是性別。
	 */
	const traceArgs = (a, extra = {}) => ({
		指標: a.indicator_id,
		資料集: a.dataset_id,
		層級: a.area_level,
		性別: `性別=${a.gender}`,
		...(a.age && a.age !== "(全年齡)" ? { 年齡: a.age } : {}),
		...(a.period && a.period !== "(全部期間)" ? { 期間: a.period } : { 期間: "全部期間" }),
		...extra,
	});

	/** 三個工具共用的參數形狀，省得各寫一份會走樣 */
	const SELECTOR_SCHEMA = {
		type: "object",
		properties: {
			indicator_id: { type: "string" },
			dataset_id: { type: "string", description: "指標橫跨多個資料集時必填" },
			area_level: { type: "string", description: "district / city / country" },
			gender: { type: "string" },
			age_lower: { type: "number", description: "年齡下界（含）" },
			age_upper: { type: "number", description: "年齡上界（含）" },
			period: { type: "string", description: "期間開始日，例如 2021-01-01。省略＝最新一期，填 all＝全部期間" },
		},
		required: ["indicator_id"],
	};

	// ── 3a. 直接取數字 ──
	const queryIndicator = {
		name: "query_indicator",
		label: "查數值",
		description:
			"取某個指標的實際數值。by=district 看各行政區，by=period 看歷年。\n" +
			"**回傳的 applied 會寫出實際生效的條件與期間，引用數字時要照著寫，不要自己推測期間。**\n" +
			"指標橫跨多個資料集時必須指定 dataset_id。",
		parameters: {
			type: "object",
			properties: {
				...SELECTOR_SCHEMA.properties,
				by: { type: "string", description: "district 或 period" },
			},
			required: ["indicator_id", "by"],
		},
		async execute(_id, p) {
			const by = p?.by === "period" ? "period" : "district";
			// by=period 是看歷年，不該被釘在單一期間
			const sel = by === "period" ? { ...p, period: p?.period || "all" } : p;
			const r = await resolveSelector(sel, { forDistrict: by === "district" });
			if (r.error) {
				note("query_indicator", { indicator_id: String(p?.indicator_id ?? ""), by }, "被擋下");
				return result(r);
			}
			const key = by === "district" ? "area_name" : "period_start::text";
			const rows = await query(`
				SELECT ${key} AS key, round(sum(value)::numeric, 2) AS value
				FROM public.youth_fact_named ${r.where}
				GROUP BY 1 ORDER BY ${by === "district" ? "2 DESC" : "1"} LIMIT 120`);
			note("query_indicator", traceArgs(r.applied, { 分組: by === "district" ? "各行政區" : "歷年" }),
				rows.length ? `${rows.length} 筆` : "0 筆（條件可能把資料濾光）");
			return result({
				applied: { ...r.applied, by },
				rows: rows.length ? rows : undefined,
				...(rows.length ? {} : { hint: "查無資料，先用 inspect_indicator 確認年齡區間與期間" }),
			}, { n: rows.length });
		},
	};

	// ── 3b. 兩個指標的相關性：交給資料庫算，不要用眼睛看 ──
	//
	// 2026-09-12 的八題跨領域測試裡，三題相關性問題**三題都偏掉**：
	//   青年人口 vs 租金    模型說「沒有明顯正相關」  實際 r = 0.668
	//   生育率 vs 青年人口  模型說「呈現相反的關係」  實際 r = -0.200（很弱）
	//   事故 vs YouBike     模型說「不完全一致」      實際 r = 0.924（極強）
	// 它看的是自己列出來的表格，不是全部 29 個區，於是方向和強弱都估錯。
	// strength 這個標籤也由程式給，不讓模型自己決定「明顯」是多少。
	const correlateIndicators = {
		name: "correlate_indicators",
		label: "算相關性",
		description:
			"計算兩個指標在各行政區之間的相關係數（Pearson 與 Spearman），由資料庫算。\n" +
			"**只要問題是「A 高的區 B 是不是也高」這類關聯，一律用這個工具，不要自己看表格判斷。**\n" +
			"回傳會包含樣本數、強弱標籤，以及兩邊涵蓋範圍不同而被排除的行政區。",
		parameters: {
			type: "object",
			properties: { a: SELECTOR_SCHEMA, b: SELECTOR_SCHEMA },
			required: ["a", "b"],
		},
		async execute(_id, p) {
			const ra = await resolveSelector(p?.a, { forDistrict: true });
			if (ra.error) { note("correlate_indicators", { a: p?.a?.indicator_id }, "A 被擋下"); return result({ side: "a", ...ra }); }
			const rb = await resolveSelector(p?.b, { forDistrict: true });
			if (rb.error) { note("correlate_indicators", { b: p?.b?.indicator_id }, "B 被擋下"); return result({ side: "b", ...rb }); }

			const sql = `
				WITH a AS (SELECT area_name, sum(value) v FROM public.youth_fact_named ${ra.where} GROUP BY 1),
				     b AS (SELECT area_name, sum(value) v FROM public.youth_fact_named ${rb.where} GROUP BY 1),
				     j AS (SELECT a.area_name, a.v va, b.v vb FROM a JOIN b USING (area_name)),
				     rk AS (SELECT rank() OVER (ORDER BY va) ra, rank() OVER (ORDER BY vb) rb FROM j)
				SELECT (SELECT count(*) FROM j)  AS n,
				       (SELECT count(*) FROM a)  AS n_a,
				       (SELECT count(*) FROM b)  AS n_b,
				       (SELECT round(corr(va, vb)::numeric, 3) FROM j)  AS pearson,
				       (SELECT round(corr(ra, rb)::numeric, 3) FROM rk) AS spearman,
				       (SELECT string_agg(x, '、') FROM (
				          SELECT area_name x FROM a WHERE area_name NOT IN (SELECT area_name FROM b)
				          UNION SELECT area_name FROM b WHERE area_name NOT IN (SELECT area_name FROM a)
				       ) _u_) AS dropped`;
			const row = (await query(sql))[0] || {};
			const r = row.pearson === null || row.pearson === undefined ? null : Number(row.pearson);
			const abs = r === null ? null : Math.abs(r);
			// 強弱由程式判定，模型只負責把它寫進句子
			const strength = abs === null ? "算不出來"
				: abs < 0.2 ? "幾乎沒有關聯"
				: abs < 0.4 ? "微弱"
				: abs < 0.6 ? "中等"
				: abs < 0.8 ? "強" : "很強";
			const out = {
				a: ra.applied, b: rb.applied,
				n_districts: Number(row.n),
				pearson: r, spearman: row.spearman === null ? null : Number(row.spearman),
				direction: r === null ? null : (r > 0 ? "正相關" : r < 0 ? "負相關" : "無方向"),
				strength,
				...(row.dropped ? { excluded_districts: row.dropped,
					note: `這些行政區只有其中一邊有資料，已排除；相關係數是用 ${row.n} 個區算的` } : {}),
				...(Number(row.n) < 8 ? { warning: "樣本數太少，相關係數不穩定，不要下強結論" } : {}),
			};
			note("correlate_indicators",
				{ a: ra.applied.indicator_id, b: rb.applied.indicator_id },
				`r=${r}（${strength}${out.direction ? "・" + out.direction : ""}）n=${row.n}`);
			return result(out, { n: Number(row.n) });
		},
	};

	// ── 3c. 兩個指標相除：比例與排名一律 SQL 算完 ──
	//
	// 同一批測試裡，「租金中位數佔平均所得多少」那題：模型每個百分比單獨算都對，
	// 但排名錯了（中和 30.5% 排在新莊 30.7% 前面），而且漏掉四個該進前五的區——
	// 它只對自己列出來的那幾個區做了除法。這裡回傳全部 29 個區並且排序好。
	const compareIndicators = {
		name: "compare_indicators",
		label: "算比例排名",
		description:
			"把兩個指標相除，算出各行政區的比例並排序，由資料庫算。\n" +
			"**「A 佔 B 多少」「哪一區負擔最重」「比例最高」這類問題一律用這個，不要自己除。**\n" +
			"單位不同時用 numerator_scale / denominator_scale 換算（例如年所得千元換成月所得元：denominator_scale = 1000/12 ≈ 83.333）。",
		parameters: {
			type: "object",
			properties: {
				numerator: SELECTOR_SCHEMA,
				denominator: SELECTOR_SCHEMA,
				numerator_scale: { type: "number", description: "分子乘數，預設 1" },
				denominator_scale: { type: "number", description: "分母乘數，預設 1" },
				scale: { type: "number", description: "結果乘數，預設 100（＝百分比）" },
			},
			required: ["numerator", "denominator"],
		},
		async execute(_id, p) {
			const rn = await resolveSelector(p?.numerator, { forDistrict: true });
			if (rn.error) { note("compare_indicators", { 分子: p?.numerator?.indicator_id }, "分子被擋下"); return result({ side: "numerator", ...rn }); }
			const rd = await resolveSelector(p?.denominator, { forDistrict: true });
			if (rd.error) { note("compare_indicators", { 分母: p?.denominator?.indicator_id }, "分母被擋下"); return result({ side: "denominator", ...rd }); }

			const ns = Number(p?.numerator_scale ?? 1) || 1;
			const dsc = Number(p?.denominator_scale ?? 1) || 1;
			const sc = Number(p?.scale ?? 100) || 100;
			const rows = await query(`
				WITH a AS (SELECT area_name, sum(value) v FROM public.youth_fact_named ${rn.where} GROUP BY 1),
				     b AS (SELECT area_name, sum(value) v FROM public.youth_fact_named ${rd.where} GROUP BY 1)
				SELECT a.area_name AS area,
				       round(a.v::numeric, 2) AS numerator,
				       round(b.v::numeric, 2) AS denominator,
				       round(((a.v * ${ns}) / nullif(b.v * ${dsc}, 0) * ${sc})::numeric, 2) AS ratio
				FROM a JOIN b USING (area_name)
				WHERE b.v IS NOT NULL AND b.v <> 0
				ORDER BY ratio DESC NULLS LAST`);
			note("compare_indicators",
				{ 分子: rn.applied.indicator_id, 分母: rd.applied.indicator_id },
				`${rows.length} 個區，已排序`);
			return result({
				numerator: rn.applied, denominator: rd.applied,
				scales: { numerator_scale: ns, denominator_scale: dsc, result_scale: sc },
				ranked: rows,
				note: "ranked 已經由高到低排好，直接照順序引用，不要自己重排或只取你看過的幾個區",
			}, { n: rows.length });
		},
	};

	// ── 3d. 同一個指標、兩個期間的變化：差額與排名一樣交給 SQL ──
	//
	// 「過去五年流失最多的是哪幾區」這類問題，模型原本是自己相減再排名。
	// 2026-09-12 實測：29 個區的數字它全部查到了，但它列表格時是照人口大小排，
	// 只對表格裡看到的那幾個區做減法，於是絕對流失第 5 名寫成新店（-6,632），
	// 真正的第 5 名是土城（-7,311），蘆洲（-7,031）也被漏掉。
	// 這跟 compare_indicators 是同一類錯，只是衍生的維度從「兩個指標」換成「兩個期間」。
	const comparePeriods = {
		name: "compare_periods",
		label: "算期間變化",
		description:
			"同一個指標在兩個期間之間，各行政區的變化量與變化率，由資料庫算好並排序。\n" +
			"**「過去N年變化」「流失最多」「成長最快」「哪幾區在減少」這類問題一律用這個，" +
			"不要自己相減也不要自己排名。**\n" +
			"不填 from / to 就自動用最早與最新的期間。回傳依變化量由小到大（流失最多在最前面）。",
		parameters: {
			type: "object",
			properties: {
				indicator: SELECTOR_SCHEMA,
				from: { type: "string", description: "起始期間，例如 2021-01-01。省略＝最早一期" },
				to: { type: "string", description: "結束期間，例如 2026-01-01。省略＝最新一期" },
			},
			required: ["indicator"],
		},
		async execute(_id, p) {
			const base = { ...(p?.indicator || {}) };
			// 先問這個指標到底有哪些期間，from / to 才有得挑
			const probe = await resolveSelector({ ...base, period: "all" }, { forDistrict: true });
			if (probe.error) {
				note("compare_periods", { 指標: base.indicator_id }, "被擋下");
				return result(probe);
			}
			const periods = (await query(
				`SELECT DISTINCT period_start::text AS p FROM public.youth_fact_named
				 ${probe.where} ORDER BY 1`)).map((r) => r.p);
			if (periods.length < 2) {
				note("compare_periods", { 指標: base.indicator_id }, `只有 ${periods.length} 個期間`);
				return result({ error: "這個指標只有一個期間，沒有變化可以算", periods });
			}
			const from = p?.from ? String(p.from) : periods[0];
			const to = p?.to ? String(p.to) : periods[periods.length - 1];
			for (const [name, v] of [["from", from], ["to", to]]) {
				if (!periods.includes(v)) {
					note("compare_periods", { 指標: base.indicator_id }, `沒有 ${v} 這一期`);
					return result({ error: `${name}=${v} 不是這個指標有的期間`, available_periods: periods });
				}
			}

			const a = await resolveSelector({ ...base, period: from }, { forDistrict: true });
			const b = await resolveSelector({ ...base, period: to }, { forDistrict: true });
			const rows = await query(`
				WITH a AS (SELECT area_name, sum(value) v FROM public.youth_fact_named ${a.where} GROUP BY 1),
				     b AS (SELECT area_name, sum(value) v FROM public.youth_fact_named ${b.where} GROUP BY 1)
				SELECT a.area_name AS area,
				       round(a.v::numeric, 2) AS from_value,
				       round(b.v::numeric, 2) AS to_value,
				       round((b.v - a.v)::numeric, 2) AS diff,
				       round(((b.v - a.v) / nullif(a.v, 0) * 100)::numeric, 2) AS pct_change
				FROM a JOIN b USING (area_name)
				ORDER BY diff ASC`);
			const drop = rows.filter((r) => Number(r.diff) < 0).length;
			note("compare_periods",
				{ 指標: a.applied.indicator_id, 期間: `${from} → ${to}` },
				`${rows.length} 個區，${drop} 個減少`);
			return result({
				indicator: a.applied.indicator_id, from, to,
				applied: { dataset_id: a.applied.dataset_id, area_level: a.applied.area_level,
					gender: a.applied.gender, age: a.applied.age },
				available_periods: periods,
				ranked_by_diff: rows,
				note: "ranked_by_diff 已依變化量由小到大排好（負最多＝流失最多在最前面）。"
					+ "要看變化率就自己看 pct_change 欄位，但**排名一律照這份清單**，不要重排也不要只取你看過的幾個區。",
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
			correlateIndicators, compareIndicators, comparePeriods,
			buildComponent, listOfficial, listDomains],
		trace,
	};
}
