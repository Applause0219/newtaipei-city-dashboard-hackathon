// ComponentSpec → SQL 編譯器
//
// 這是「自動生成組件」的核心。設計沿用 AimchartAI 的立論：
//
//   ── 模型不碰數字，只產出宣告式規格 ──
//
// 模型輸出的是 ComponentSpec（挑哪張表、哪幾個欄位、怎麼分組），
// 由這支程式「決定性地」編譯成 SQL。模型不寫 SQL，也不產生任何數值。
//
// 這樣有三個好處：
//   1. 不可能有 SQL injection——模型的輸出只是資料，不是可執行字串
//   2. 幻覺會當場失敗——表名或欄位不存在，validateSpec 直接擋下來
//   3. 數值只在 PostgreSQL 算一次，不會出現「兩邊各算一次結果不同」
//
// 第 3 點是 AimchartAI 架構圖裡標為 Δ5 的未解問題。城市儀表板因為
// query_charts.query_chart 存的就是 SQL，天然沒有這個問題。

/** 後端各 query_type 要求的欄位別名（來源：/back-end/component-data-apis） */
const REQUIRED_COLUMNS = {
	two_d: ["x_axis", "data"],
	three_d: ["x_axis", "y_axis", "data"],
	percent: ["x_axis", "y_axis", "data"],
	time: ["x_axis", "y_axis", "data"],
	map_legend: ["name", "type", "value"],
};

/** 各 query_type 官方實際搭配過的圖表元件 */
const ALLOWED_CHARTS = {
	two_d: ["BarChart", "ColumnChart", "DistrictChart", "DonutChart", "TreemapChart", "MetroChart", "RadarChart"],
	three_d: ["ColumnChart", "DistrictChart", "BarPercentChart", "HeatmapChart", "PolarAreaChart", "RadarChart", "IndicatorChart", "TextUnitChart", "IconPercentChart"],
	percent: ["GuageChart", "BarPercentChart", "IconPercentChart", "BarChartWithGoal", "ColumnChart"],
	time: ["TimelineSeparateChart", "ColumnLineChart", "TimelineStackedChart"],
	map_legend: ["MapLegend"],
};

const CITIES = ["taipei", "metrotaipei"];

/** SQL 字串常值：單引號成對跳脫。識別字另外用 quoteIdent。 */
const lit = (s) => "'" + String(s).replace(/'/g, "''") + "'";
/** 識別字：只允許中英數與底線，其餘一律拒絕（不是跳脫，是拒絕） */
function quoteIdent(name) {
	if (!/^[一-鿿\w]+$/.test(name)) {
		throw new Error(`識別字含不允許的字元：${name}`);
	}
	return '"' + name + '"';
}

/**
 * 驗證 spec。回傳 { ok, errors[] }。
 *
 * catalog 由 catalog.js 產出，是 catalog.yaml（語意）與 information_schema
 * （真相）的交集——所以幻覺出來的表名、欄位，以及「有欄位但沒人說明過它是
 * 什麼意思」的欄位，都會在這裡被擋下。
 */
export function validateSpec(spec, catalog) {
	const e = [];
	const push = (m) => e.push(m);

	if (!spec || typeof spec !== "object") return { ok: false, errors: ["spec 不是物件"] };
	if (!spec.index || !/^[a-z][a-z0-9_]*$/.test(spec.index)) push("index 必須是小寫英數底線，且開頭為英文字母");
	if (!spec.name) push("缺 name");
	if (!CITIES.includes(spec.city)) push(`city 必須是 ${CITIES.join(" 或 ")}，收到 ${spec.city}`);

	const qt = spec.query_type;
	if (!REQUIRED_COLUMNS[qt]) push(`不支援的 query_type：${qt}`);

	const cols = catalog.columns?.[spec.table];
	const meta = catalog.tables?.[spec.table]?.fields || {};
	if (!cols) {
		push(`資料表不存在或未在 catalog 說明：${spec.table}`);
		return { ok: false, errors: e };   // 表都沒有，欄位就不用查了
	}
	const has = (c) => cols.includes(c);
	const typeOf = (c) => meta[c]?.type;

	/** filter 物件允許的鍵。多一個都不行——多的那個一定是模型以為有效但其實被忽略的。 */
	const FILTER_KEYS = new Set(["column", "eq", "ne", "gte", "lte"]);
	const checkFilter = (f, where) => {
		if (!f.column) push(`${where} 缺 column`);
		else if (!has(f.column)) push(`欄位不存在：${spec.table}.${f.column}`);
		const ops = ["eq", "ne", "gte", "lte"].filter((k) => k in f);
		if (!ops.length) push(`${where} 需要 eq / ne / gte / lte 其中之一`);
		for (const k of Object.keys(f)) {
			if (!FILTER_KEYS.has(k)) push(`${where} 有不支援的欄位 "${k}"，會被忽略`);
		}
		for (const k of ["gte", "lte"]) {
			if (k in f && !Number.isFinite(Number(f[k]))) push(`${where}.${k} 必須是數字`);
		}
	};

	// city 必須與資料表相符。
	//
	// 這關是實測補的：問「比較雙北各區的青年人口」，模型產出
	// city=metrotaipei 但 table=city_age_distribution_taipei，
	// 名稱還叫「雙北青年人口分區」——結果是一張標著雙北、只有台北 12 區的圖。
	// spec 本身完全合法，欄位都在、型別都對，下游每一關都攔不到。
	//
	// 成因是 ComponentSpec 一次只能指定一張表，跨城市的問題它表達不出來，
	// 模型只好挑一邊。與其讓它安靜地挑，不如在這裡大聲失敗。
	const tableCity = catalog.tables?.[spec.table]?.city;
	if (tableCity && spec.city !== tableCity) {
		push(`city 與資料表不符：${spec.table} 屬於 ${tableCity}，但 spec.city 是 ${spec.city}。`
			+ `一個組件只能對應一個城市；跨城市比較需要拆成兩個組件。`);
	}

	if (!spec.x?.column) push("缺 x.column");
	else if (!has(spec.x.column)) push(`欄位不存在：${spec.table}.${spec.x.column}`);

	if (!Array.isArray(spec.series) || spec.series.length === 0) push("series 至少要一項");
	else {
		spec.series.forEach((s, i) => {
			if (!s.label) push(`series[${i}] 缺 label`);
			if (!s.column) push(`series[${i}] 缺 column`);
			else if (!has(s.column)) push(`欄位不存在或未說明：${spec.table}.${s.column}`);
			if (s.filter) checkFilter(s.filter, `series[${i}].filter`);
			// 模型常把 age_lower / age_upper 直接寫在 series 上——那不是 spec 的一部分，
			// 會被靜默忽略，於是標籤寫「15-35 歲」但算的是全年齡。
			// 實測 agent 就這樣把板橋的 119,834 畫成 547,794。
			// 年齡範圍要寫成 filters: [{column:"age_lower",gte:15},{column:"age_upper",lte:35}]
			const SERIES_KEYS = new Set(["label", "column", "filter"]);
			for (const k of Object.keys(s)) {
				if (!SERIES_KEYS.has(k)) {
					push(`series[${i}] 有不支援的欄位 "${k}"，會被忽略。`
						+ (/^age_/.test(k)
							? `年齡範圍請寫成 filters: [{"column":"age_lower","gte":15},{"column":"age_upper","lte":35}]`
							: `只接受 label / column / filter。`));
				}
			}
		});

		// 標籤不可以說謊。
		//
		// 這關擋的是最難發現的一種錯：問「各區女性的青年人口」，模型產出
		// 標籤寫「女性 20-24歲」、篩選卻是 統計類型='計' 的組件。
		// 圖畫得出來、數字也對得上某個東西——只是那個東西是男女合計。
		// 使用者沒有任何線索知道自己被回答了另一個問題。
		//
		// 判斷依據是 catalog 的 values（不是硬寫「男／女」），
		// 所以之後任何有列舉值的分類欄位都自動受保護。
		for (const [fcol, fmeta] of Object.entries(meta)) {
			const vals = fmeta?.values;
			if (!Array.isArray(vals)) continue;
			const neutral = fmeta.neutral_value;
			const baseF = (spec.filters || []).find((f) => f.column === fcol);

			spec.series.forEach((sr, i) => {
				const own = sr.filter?.column === fcol && "eq" in sr.filter ? sr.filter.eq : undefined;
				const effective = own !== undefined ? own : (baseF && "eq" in baseF ? baseF.eq : undefined);

				// 標籤剛好提到一個值才判斷。提到兩個（「男女合計」）語意不明，不猜。
				const hit = vals.filter((v) => v !== neutral && String(sr.label).includes(v));
				if (hit.length !== 1) return;
				if (effective !== hit[0]) {
					push(`series[${i}] 的標籤「${sr.label}」提到「${hit[0]}」，`
						+ `但實際取的是 ${fcol}=${effective ?? "全部"}。`
						+ `要分「${hit[0]}」請在該數列加 filter：{"column":"${fcol}","eq":"${hit[0]}"}`);
				}
			});
		}

		// two_d 只吃一個數列——compileSpec 的 two_d 分支用的是 series[0]。
		//
		// 這關是實測補的，而且它擋下的是今天發現最危險的一種錯：
		// 模型給了 percent7/8/9 三個數列卻標成 two_d，編譯器只取第一個，
		// 畫出來是 29 個區、數字合理、標題寫「青年人口」——實際只有 20-24 歲。
		// 沒有任何一關會叫，因為每個欄位單獨看都合法。
		// 與其安靜地少算，不如在這裡失敗。
		if (qt === "two_d" && spec.series.length > 1) {
			push(`two_d 只能有一個數列，但收到 ${spec.series.length} 個`
				+ `（${spec.series.map((s) => s.column).join(", ")}）。多數列請用 three_d。`);
		}

		// 型別一致性：同一張圖不能混用人數與百分比，兩者的座標軸意義不同，
		// 疊在一起的圖表在數學上沒有意義（也是 catalog.yaml 標 ratio 的用意）
		const types = [...new Set(spec.series.map((s) => typeOf(s.column)).filter(Boolean))];
		if (types.length > 1) {
			push(`series 混用了不同型別的欄位（${types.join(" 與 ")}），同一張圖的數列必須是同一種量`);
		}

		// 百分比／指數不可再做除法縮放——縮放後的數字不再是任何東西的百分比
		if (spec.transform?.divide && types.includes("ratio")) {
			push("ratio 型別（百分比、指數）不可套用 transform.divide");
		}

		// 長表的三道守門。
		//
		// 長表（一個 x 對應很多列）跟寬表（一個 x 一列）在 SQL 上長得一樣，
		// 但少了任何一項都會得到「看起來正常、實際上重複計算」的數字，
		// 而且 PostgreSQL 一句話都不會說。
		const tmeta = catalog.tables?.[spec.table] || {};
		if (tmeta.long_table) {
			if (!spec.aggregate) {
				push(`${spec.table} 是長表（一個 ${spec.x?.column} 對應多列），`
					+ "必須指定 aggregate，否則同一個項目會畫出多個點。");
			}
			// 不篩地理層級 = 把全國、全市、行政區的數字疊在一起
			const filtered = new Set([
				...(spec.filters || []).map((f) => f.column),
				...spec.series.flatMap((sr) => (sr.filter ? [sr.filter.column] : [])),
			]);
			for (const need of tmeta.requires_filters || []) {
				if (!filtered.has(need)) {
					push(`${spec.table} 必須篩 ${need}`
						+ `（${meta[need]?.note || "否則會重複計算"}）`);
				}
			}
			// 篩一個該指標根本沒有的性別值 → 回空結果，而且 SQL 不報錯。
			//
			// 21/70 個資料集沒有 total 列：生育率只有 female（那本來就是
			// 婦女指標）、結婚只有 male/female、消費是 NULL。
			// 但目錄教模型「沒提到性別就篩 total」，於是它照做然後濾光。
			// 22 題探測裡有 3 題栽在這裡。
			const indFilters = [...(spec.filters || []),
			                    ...spec.series.map((sr) => sr.filter).filter(Boolean)]
				.filter((f) => f.column === "indicator_id" && "eq" in f);
			const genderFilter = [...(spec.filters || []),
			                      ...spec.series.map((sr) => sr.filter).filter(Boolean)]
				.find((f) => f.column === "gender" && "eq" in f);
			if (indFilters.length && catalog.youthDatasets) {
				for (const f of indFilters) {
					// 找出這個指標所屬的資料集（可能多個，歧義的情況上面已擋）
					let genders = null;
					for (const d of Object.values(catalog.youthDatasets)) {
						const m2 = d.indicators?.[f.eq];
						if (m2?.genders) { genders = m2.genders; break; }
					}
					if (!genders) continue;

					const hasTotal = genders.includes("total");
					const hasSplit = genders.includes("male") || genders.includes("female");

					if (genderFilter && !genders.includes(genderFilter.eq)) {
						// 篩一個不存在的值 → 空結果
						push(`指標 ${f.eq} 沒有 gender="${genderFilter.eq}" 的資料`
							+ `（實際只有 ${genders.join("、")}）。篩下去會得到空結果。`);
					} else if (!genderFilter && hasTotal && hasSplit && spec.aggregate === "sum") {
						// 同時有 total 與 male/female 時不篩就會**重複計算兩倍**。
						//
						// 實測 population_count：不篩 gender 板橋是 1,095,588，
						// 篩 total 是 547,794——正好兩倍，因為 total 那一列
						// 本來就等於 male + female，三個加起來等於算了兩遍。
						// 144 個指標裡有 58 個踩得到這個。
						push(`指標 ${f.eq} 同時有 total 與 ${genders.filter((g) => g !== "total").join("、")} 三種列，`
							+ `total 本身就等於其他的加總。不篩 gender 直接 sum 會**重複計算兩倍**。`
							+ `要全體請篩 gender="total"，要分性別請用 series[].filter 分別指定。`);
					}
				}
			}

			// indicator_id 不是全域唯一：7 個跨資料集重複，
			// 只篩 indicator_id 會把兩份資料的數字加在一起
			for (const f of [...(spec.filters || []),
			                 ...spec.series.map((sr) => sr.filter).filter(Boolean)]) {
				if (f.column === "indicator_id" && "eq" in f
				    && catalog.ambiguousIndicators?.has(f.eq)
				    && !filtered.has("dataset_id")) {
					push(`indicator_id "${f.eq}" 在多個資料集裡都有，`
						+ "只篩它會把不同資料集的數字加在一起。請同時指定 dataset_id。");
				}
			}
		}

		// 聚合方式必須符合 catalog.yaml 的 agg_rules。
		//
		// 這條規則一直寫在 catalog.yaml 裡，但從來沒有程式在用它。
		// 長表（youth_fact）一定要聚合才畫得出圖，所以現在它必須生效——
		// 「把失業率加總」在數學上沒有意義，但 SQL 不會報錯，
		// 畫出來是一個看起來正常、實際上毫無意義的數字。
		const agg = spec.aggregate;
		if (agg !== undefined) {
			if (!AGGREGATES.includes(agg)) {
				push(`aggregate 必須是 ${AGGREGATES.join(" / ")} 之一，收到 ${agg}`);
			} else {
				for (const sr of spec.series) {
					const ty = typeOf(sr.column);
					const allowed = catalog.aggRules?.[ty];
					if (allowed && !allowed.includes(agg)) {
						push(`${sr.column} 是 ${ty} 型別，不可用 ${agg}`
							+ `（允許：${allowed.join(" / ")}）。`
							+ (ty === "ratio" && agg === "sum"
								? "比率與指數加總沒有數學意義。" : ""));
					}
				}
			}
		}
	}

	(spec.filters || []).forEach((f, i) => checkFilter(f, `filters[${i}]`));

	if (spec.latest_by && !has(spec.latest_by)) push(`欄位不存在：${spec.table}.${spec.latest_by}`);

	// time 的 x 軸必須是真正的日期欄位。
	//
	// 這條以前是寫在 prompt 裡的「本專案沒有日期欄位，不要用 time」——
	// 寬表時代成立，長表建好之後就不成立了（youth_fact.period_start 是 date）。
	// 規則沒跟著改，代價是所有時間趨勢都只能畫成長條圖，折線圖整個用不到。
	//
	// 現在改成程式擋：欄位型別對就放行，不對就講清楚為什麼。
	if (qt === "time") {
		const xt = typeOf(spec.x?.column);
		if (spec.x?.column && xt !== "date") {
			push(`time 的 x 軸必須是 date 型別的欄位，${spec.x.column} 是 ${xt || "未知型別"}。`
				+ `年份整數請改用 three_d + ColumnChart。`);
		}
		// latest_by 會把整段時間收斂成一期，折線圖只剩一個點
		if (spec.latest_by) push("time 是看整段時間的變化，不可以設 latest_by（只會剩一個時間點）");
	}

	const types = spec.chart?.types;
	if (!Array.isArray(types) || types.length === 0) push("chart.types 至少要一項");
	else if (REQUIRED_COLUMNS[qt]) {
		const bad = types.filter((t) => !ALLOWED_CHARTS[qt].includes(t));
		if (bad.length) push(`${qt} 不支援這些圖表：${bad.join(", ")}（可用：${ALLOWED_CHARTS[qt].join(", ")}）`);
	}

	return { ok: e.length === 0, errors: e };
}

/** 組 WHERE 條件（不含固定順序的部分） */
export function whereClause(spec, extra = []) {
	// 先 spec.filters 再 series 自己的：後者用來表達「同欄位不同值」，
	// 例如同一個 percent7，一個數列篩「男」、另一個篩「女」。
	const conds = [];
	for (const f of [...(spec.filters || []), ...extra]) {
		const col = quoteIdent(f.column);
		if ("eq" in f) conds.push(`${col} = ${lit(f.eq)}`);
		if ("ne" in f) conds.push(`${col} <> ${lit(f.ne)}`);
		// 範圍條件。年齡範圍是這個專案的核心（藍圖 §7），沒有它就只能算全年齡。
		// 數值一律轉成 Number 再插進 SQL，不走 lit()——避免把數字包成字串。
		if ("gte" in f) conds.push(`${col} >= ${Number(f.gte)}`);
		if ("lte" in f) conds.push(`${col} <= ${Number(f.lte)}`);
	}
	for (const v of spec.x?.exclude || []) {
		conds.push(`${quoteIdent(spec.x.column)} <> ${lit(v)}`);
	}

	const parts = [...conds];

	// 「只取最新一期」的子查詢**必須帶上同一組篩選條件**。
	//
	// 寬表時代不帶也沒事：一張表就是一個資料集，全表最大值就是它的最大值。
	// 長表完全不同——youth_fact 有 70 個資料集擠在一起，各自的最新期不一樣。
	// 不帶條件算出來的是「全表最新」（2026-12-01），而租金資料最新只到
	// 2026-07-01，於是整批被濾光，得到「查不到任何資料列」。
	//
	// 這個錯不會報 SQL 錯誤，只會回空結果——實測 22 題裡一次炸掉 5 題。
	if (spec.latest_by) {
		const c = quoteIdent(spec.latest_by);
		const t = `public.${quoteIdent(spec.table)}`;
		const inner = conds.length ? ` WHERE ${conds.join(" AND ")}` : "";
		parts.push(`${c} = (SELECT max(${c}) FROM ${t}${inner})`);
	}
	return parts.length ? "WHERE " + parts.join("\n      AND ") : "";
}

/**
 * 從模型輸出裡撈出 JSON 物件，容忍前後夾雜的散文。
 *
 * 為什麼需要：原本只剝除字串**頭尾**的 ``` 圍欄，前面有一段推論就失效。
 * 目錄從 3 張表長到 151 組指標之後，挑選變難，模型開始邊想邊寫——
 * 22 題探測裡有 4 題因此被誤判成「模型不聽話」，其實它答對了只是話多。
 *
 * 用括號配對掃描而不是正則：JSON 是遞迴結構，正則配不出巢狀。
 * 掃描時要跳過字串內的括號，否則 {"a":"{"} 這種值會把配對算錯。
 *
 * @returns {object|null} 第一個完整的 JSON 物件，找不到回 null
 */
export function extractJsonObject(text) {
	const s = String(text ?? "");
	for (let start = s.indexOf("{"); start !== -1; start = s.indexOf("{", start + 1)) {
		let depth = 0, inStr = false, esc = false;
		for (let i = start; i < s.length; i++) {
			const ch = s[i];
			if (inStr) {
				if (esc) esc = false;
				else if (ch === "\\") esc = true;
				else if (ch === '"') inStr = false;
				continue;
			}
			if (ch === '"') inStr = true;
			else if (ch === "{") depth++;
			else if (ch === "}" && --depth === 0) {
				try { return JSON.parse(s.slice(start, i + 1)); }
				catch { break; }   // 這個候選不合法，換下一個 {
			}
		}
	}
	return null;
}

/** 允許的聚合函式。與 catalog.yaml 的 agg_rules 用同一組名稱。 */
const AGGREGATES = ["sum", "avg", "min", "max"];

/**
 * 數值運算：除法、小數位、以及（長表才需要的）聚合。
 *
 * 為什麼會有聚合這件事：
 *   原本只處理寬表——一列就是一個行政區，欄位本身就是度量，
 *   所以 SELECT 欄位直接出來即可。
 *   但 youth_fact 那種統一事實表是長表：一個行政區有很多列
 *   （不同 indicator、不同年齡組距、不同性別），必須 SUM 起來。
 *
 * 不給模型自由發揮：只有 AGGREGATES 這四個，而且 validateSpec 會依
 * catalog 的 agg_rules 擋掉「對 ratio 做 sum」這種數學上沒有意義的組合。
 */
function metricExpr(spec, column) {
	const col = quoteIdent(column);
	const agg = spec.aggregate;
	const base = agg ? `${agg}(${col})` : col;

	const d = spec.transform?.divide;
	if (d) return `round(${base} / ${Number(d)}.0)`;
	// ratio 欄位是 real，直接加總會出現 7238.157860000002 這種浮點尾巴。
	// 指數到小數點後一位已經遠超過解讀需要。
	const r = spec.transform?.round;
	return Number.isInteger(r) ? `round((${base})::numeric, ${r})` : base;
}

/**
 * 把 spec 編成 SQL。
 * xOrder / yOrder 是固定順序用的陣列——**這是正確性的關鍵，不是排版**。
 * 後端編譯 three_d 時是按列序 append（componentData.go:301-325），
 * 不對應 category，所以各系列的 x 順序一旦不同就會把數值對到錯的區。
 */
export function compileSpec(spec, xOrder) {
	const t = `public.${quoteIdent(spec.table)}`;
	const x = quoteIdent(spec.x.column);
	// 每個數列可以有自己的篩選條件，所以 WHERE 不能只算一次
	const whereFor = (s) => whereClause(spec, s?.filter ? [s.filter] : []);
	const arr = (items) => `ARRAY[\n      ${items.map(lit).join(", ")}\n    ]::varchar[]`;
	// 排序陣列是 varchar[]，但 x 欄位不一定是文字——「年份」就是 integer。
	// 少了這個轉型，PostgreSQL 會報 function array_position(character varying[],
	// integer) does not exist，任何以年份為 x 軸的時間序列都跑不起來。
	// 一律轉 varchar：官方 two_d／three_d 的 x_axis 契約本來就是字串。
	const asText = (e) => `${e}::varchar`;

	// 有聚合就要分組。分組鍵是 x 欄位本身（不是轉型後的），
	// PostgreSQL 才認得出 SELECT 清單裡那個 x::varchar 是它的衍生值。
	const groupBy = spec.aggregate ? `GROUP BY ${x}` : "";

	// two_d 只有一個數列，欄位是 x_axis + data，沒有 y_axis。
	//
	// 為什麼要分開處理：官方 DistrictChart 依「數列數量」切換格式解讀
	// （DistrictChart.vue:135-141）——1 個數列時它讀 item.x / item.y，
	// 多個數列時它讀純數字陣列。所以單一數列的 three_d 會讓它拿到
	// undefined，總合顯示 NaN。單一數列本來就該是 two_d。
	if (spec.query_type === "two_d") {
		const s0 = spec.series[0];
		return [
			`SELECT ${asText(x)} AS x_axis, ${metricExpr(spec, s0.column)} AS data`,
			`FROM ${t}`,
			whereFor(s0),
			groupBy,
			"-- 順序固定，避免每次查詢的排列不同",
			`ORDER BY ARRAY_POSITION(${arr(xOrder)}, ${asText(x)})`,
		].filter(Boolean).join("\n");
	}

	// time：每個點自帶自己的 x，所以**不需要** xOrder 補齊。
	//
	// 為什麼可以省掉 three_d 那套 LEFT JOIN：後端編譯 time 時是依 y_axis
	// 的名稱歸戶，再把 {x, y} 整組 append 進去（componentData.go:341-355），
	// 不像 three_d 那樣按列序對 category。座標跟著值一起走，錯不了行。
	//
	// 也因此 x 軸不轉 varchar——TimelineSeparateChart 要的是可以 new Date()
	// 的值，轉成字串再 parse 只是多繞一圈。
	if (spec.query_type === "time") {
		const tBlocks = spec.series.map((s, i) => {
			const alias = i === 0 ? " AS x_axis" : "";
			const yAlias = i === 0 ? " AS y_axis" : "";
			const dAlias = i === 0 ? " AS data" : "";
			const w = whereFor(s);
			return `    SELECT ${x}${alias}, ${lit(s.label)}${yAlias}, ${metricExpr(spec, s.column)}${dAlias}\n` +
			       `    FROM ${t}\n` + (w ? `    ${w}\n` : "") +
			       (groupBy ? `    ${groupBy}\n` : "");
		});
		const tOrder = spec.series.map((s) => s.label);
		return [
			"SELECT x_axis, y_axis, data FROM (",
			tBlocks.join("    UNION ALL\n"),
			"  ) t",
			"  -- 數列順序固定，時間軸照時序",
			"  ORDER BY",
			`    ARRAY_POSITION(${arr(tOrder)}, t.y_axis::varchar),`,
			"    t.x_axis",
		].join("\n");
	}

	// 每個數列都必須對 xOrder 裡的**每一個** x 產生一列，缺的補 0。
	//
	// 為什麼不能直接 SELECT：不同指標涵蓋的行政區不一樣。
	// 實測租金只有 27 區（坪林、平溪沒有租賃實價登錄），所得有 29 區。
	// 直接 UNION 出來兩個數列長度不同，而後端是「按列序 append、
	// 不對應 category」（componentData.go:301-325），於是從缺的那一區
	// 開始，後面所有數值全部對到錯的行政區——圖畫得出來，數字全錯。
	//
	// 用 xOrder 當左表 LEFT JOIN 回資料，長度就一定一致。
	const xUniverse = `(VALUES\n      ${xOrder.map((v) => `(${lit(v)})`).join(",\n      ")}\n    ) AS _x_(v)`;
	const blocks = spec.series.map((s, i) => {
		const alias = i === 0 ? ` AS x_axis` : "";
		const yAlias = i === 0 ? ` AS y_axis` : "";
		const dAlias = i === 0 ? ` AS data` : "";
		const w = whereFor(s);
		// 子查詢先算出「有資料的那些 x 的值」，再 LEFT JOIN 補齊
		const inner =
			`      SELECT ${asText(x)} AS k, ${metricExpr(spec, s.column)} AS m\n` +
			`      FROM ${t}\n` + (w ? `      ${w}\n` : "") +
			(groupBy ? `      ${groupBy}\n` : "");
		return `    SELECT _x_.v${alias}, ${lit(s.label)}${yAlias}, coalesce(_d_.m, 0)${dAlias}\n` +
		       `    FROM ${xUniverse}\n` +
		       `    LEFT JOIN (\n${inner}    ) _d_ ON _d_.k = _x_.v\n`;
	});

	const yOrder = spec.series.map((s) => s.label);

	return [
		"SELECT x_axis, y_axis, data FROM (",
		blocks.join("    UNION ALL\n"),
		"  ) t",
		"  -- 順序必須固定且各系列一致，否則後端會把數值對到錯的 category",
		"  ORDER BY",
		`    ARRAY_POSITION(${arr(xOrder)}, t.x_axis::varchar),`,
		`    ARRAY_POSITION(${arr(yOrder)}, t.y_axis::varchar)`,
	].join("\n");
}

/**
 * 單一數列的 three_d／percent 一律降成 two_d。
 * 語意上本來就相同（一個分類一個值），而且能避開 DistrictChart 的格式陷阱。
 */
export function normalizeSpec(spec) {
	if (!spec) return spec;
	let out = spec;
	const warn = [];

	if (out.series?.length === 1 && (out.query_type === "three_d" || out.query_type === "percent")) {
		out = { ...out, query_type: "two_d", _normalizedFrom: out.query_type };
	}

	// 圖表型別選錯就直接改掉，不要讓整個組件失敗。
	//
	// 挑哪個欄位、怎麼分組是正確性問題——錯了必須大聲失敗。
	// 但「用 BarChart 還是 ColumnChart」純粹是呈現，改掉不會讓數字變錯。
	// 實測模型會把 two_d 的 BarChart 用在 three_d 上；為了這個讓
	// demo 當場失敗不划算。改掉，但記一筆警告，不要假裝沒事。
	const allowed = ALLOWED_CHARTS[out.query_type];
	if (allowed) {
		const asked = out.chart?.types || [];
		const kept = asked.filter((t) => allowed.includes(t));
		const dropped = asked.filter((t) => !allowed.includes(t));
		if (dropped.length) {
			const types = kept.length ? kept : [allowed[0]];
			warn.push(`${out.query_type} 不支援 ${dropped.join("、")}，已改用 ${types.join("、")}`);
			out = { ...out, chart: { ...(out.chart || {}), types } };
		}
	}

	return warn.length ? { ...out, _warnings: warn } : out;
}

/** 探測 x 軸的固定順序：依所有 series 加總降冪。回傳一段 SQL 讓呼叫端執行。 */
export function orderProbeSQL(spec, catalog) {
	const t = `public.${quoteIdent(spec.table)}`;
	const x = quoteIdent(spec.x.column);

	// 為什麼是 UNION + GROUP BY，而不是把欄位加起來就好：
	// 數列可以各自帶篩選條件（男／女），那時同一個 x 會落在不同的列上，
	// 「一列之內把欄位相加」這個假設就不成立了。
	// 先展開成 (x, 值) 再 GROUP BY，不管有沒有 per-series filter 都正確。
	const blocks = spec.series.map((sr) => {
		const w = whereClause(spec, sr.filter ? [sr.filter] : []);
		return `    SELECT ${x} AS x_axis, coalesce(${quoteIdent(sr.column)}, 0) AS v\n` +
		       `    FROM ${t}\n` + (w ? `    ${w}\n` : "");
	});

	// 分類軸（行政區）照數值大小排，讀者一眼看得出高低。
	// 但時間軸不行——「2004, 2005, 2003, 2006」在趨勢圖上是雜訊。時間照時序。
	// date 與 year 都算時間軸：long table 的 period_start 是 date，
	// 只認 year 會讓時間序列的 x 軸依數值大小亂排。
	const xType = catalog?.tables?.[spec.table]?.fields?.[spec.x.column]?.type;
	const chronological = xType === "year" || xType === "date";
	const order = chronological ? "x_axis" : "sum(v) DESC, x_axis";

	return [
		"SELECT x_axis FROM (",
		blocks.join("    UNION ALL\n"),
		"  ) u",
		"  GROUP BY x_axis",
		`  ORDER BY ${order}`,
	].join("\n");
}

/** 各 query_type 的用途說明，給 prompt 用。與 ALLOWED_CHARTS 共用同一份真相。 */
const QUERY_TYPE_USE = {
	two_d:      "一個分類對一個值",
	three_d:    "一個分類對多個數列",
	percent:    "占比（分子分母）",
	time:       "時間趨勢（x 軸要 date 欄位）",
	map_legend: "地圖圖層",
};

/**
 * 把 query_type → 可用圖表 組成 prompt 的一段。
 * 程式化生成而不是手寫：改了 ALLOWED_CHARTS，prompt 自動跟上，
 * 不會出現「程式改了、prompt 還在講舊的」這種慢性腐爛。
 */
export function chartOptionsForPrompt() {
	return Object.entries(ALLOWED_CHARTS)
		.map(([qt, charts]) => `  ${qt.padEnd(11)}${(QUERY_TYPE_USE[qt] || "").padEnd(16)}${charts.join(", ")}`)
		.join("\n");
}

export { REQUIRED_COLUMNS, ALLOWED_CHARTS };
