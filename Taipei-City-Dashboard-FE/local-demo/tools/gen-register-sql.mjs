// 產生 db/02_register_component.sql：青年範圍 20-34（C 級）→ 15-39（B 級）
//
// 行政區順序直接向資料庫問，不手寫。手寫過一次就會有下一次對不上——
// 目前 chart_9001.json 與這支 SQL 的順序就已經不同（三重／中和對調）。
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { writeFileSync } from "node:fs";
const run = promisify(execFile);

const OUT = "/Users/yucc_0219/orca/projects/新北市AI智慧城市黑客松/Taipei-City-Dashboard/Taipei-City-Dashboard-FE/local-demo/db/02_register_component.sql";
const Q = String.fromCharCode(39);
const q = (s) => Q + s + Q;

const BINS = [
	["percent6", "15_19歲人口數"],
	["percent7", "20_24歲人口數"],
	["percent8", "25_29歲人口數"],
	["percent9", "30_34歲人口數"],
	["percent10", "35_39歲人口數"],
];
const SUM = BINS.map(([c]) => c).join(" + ");

const CITIES = [
	{ key: "metrotaipei", table: "youth_population_district_newtaipei", total: "新北市", name: "新北市", n: 29, src: "新北主計處",
	  links: "{https://data.ntpc.gov.tw/datasets/8308ab58-62d1-424e-8314-24b65b7ab492}", contrib: "{ntpc}",
	  use: "用於掌握青年人口在新北市各行政區的聚集情形，可作為青年職涯據點、青創基地、租金補貼資源配置的參考依據。五個組距分列呈現，可直接看出各區是以「剛畢業」或「成家階段」族群為主，兩者的政策需求不同。" },
	{ key: "taipei", table: "city_age_distribution_taipei", total: "總計", name: "臺北市", n: 12, src: "臺北主計處",
	  links: "{}", contrib: "{doit}",
	  use: "用於掌握青年人口在臺北市各行政區的聚集情形，並與新北市對照觀察雙北青年的居住分布差異。" },
];

async function districtOrder(table, total) {
	const sql = [
		`SELECT "區域別" AS d`,
		`FROM public.${JSON.stringify(table).replace(/"/g, Q)}`.replace(`public.${q(table)}`, `public."${table}"`),
		`WHERE "年份" = (SELECT max("年份") FROM public."${table}")`,
		`  AND "統計類型" = ${q("計")} AND "區域別" <> ${q(total)}`,
		`ORDER BY (${SUM}) DESC, "區域別"`,
	].join("\n");
	const { stdout } = await run("psql", ["-d", "dashboard", "-tAc",
		`SELECT coalesce(json_agg(_r_), ${q("[]")}::json) FROM (${sql}) _r_`], { maxBuffer: 32e6 });
	return JSON.parse(stdout.trim()).map((r) => r.d);
}

/**
 * 把清單折行成 SQL 陣列內容。
 *
 * 每一行結尾都必須有逗號——除了最後一行。
 * 少了它不會報錯，因為 SQL 規定「兩個字串常值之間只有空白（含換行）時自動接成一個」，
 * 所以 '汐止區' 換行 '淡水區' 會變成一個叫 '汐止區淡水區' 的元素。
 * 結果是那兩個區在陣列裡找不到，array_position 回 NULL、被 ORDER BY 排到最後，
 * 而 three_d 的數值是按列序 append 的，等於把數字接到錯的行政區上。
 * 全程沒有任何錯誤訊息。這個 bug 實際發生過一次，由 verify.mjs 抓到。
 */
function wrapList(items, perLine, indent) {
	const lines = [];
	for (let i = 0; i < items.length; i += perLine) {
		lines.push(indent + items.slice(i, i + perLine).map(q).join(","));
	}
	return lines.join(",\n");
}

function cityBlock(c, order) {
	const union = BINS.map(([col, label], i) => {
		const head = i === 0
			? `    SELECT "區域別" AS x_axis, ${q(label)} AS y_axis,\n           round(${col} / 1000.0) AS data`
			: `    SELECT "區域別", ${q(label)}, round(${col} / 1000.0)`;
		return [
			head,
			`    FROM public.${c.table}`,
			`    WHERE "統計類型" = ${q("計")} AND "區域別" <> ${q(c.total)}`,
			`      AND "年份" = (SELECT max("年份") FROM public.${c.table})`,
		].join("\n");
	}).join("\n    UNION ALL\n");

	const longDesc =
		`依《青年基本法》定義之青年年齡範圍為 18 至 35 歲。公開統計採 5 歲組距，18 與 35 都落在組距中間，` +
		`無法在不推估的前提下精確切出；本組件因此採用 15-19、20-24、25-29、30-34、35-39 五個完整組距合計之 ` +
		`15-39 歲，作為青年近似族群（Exact Youth Proxy）觀察${c.name} ${c.n} 個行政區的分布。` +
		`五個組距皆為官方原始數字之加總，未做任何內插、拆分或估算。法定青年定義仍為 18-35 歲，本組件不等同該範圍。`;

	return `INSERT INTO public.query_charts (
  index, history_config, map_config_ids, map_filter,
  time_from, time_to, update_freq, update_freq_unit,
  source, short_desc, long_desc, use_case, links, contributors,
  query_type, query_chart, city
) VALUES (
  ${q("youth_population_district")}, NULL, ${q("{}")}, ${q("{}")},
  ${q("static")}, NULL, 1, ${q("year")},
  ${q(c.src)},
  ${q(`${c.name}青年近似人口（15-39 歲）的行政區分布`)},
  ${q(longDesc)},
  ${q(c.use)},
  ${q(c.links)},
  ${q(c.contrib)},
  ${q("three_d")},
  $q$
  SELECT x_axis, y_axis, data FROM (
${union}
  ) t
  -- 官方要求 three_d 需對 x_axis 與 y_axis 排序，且各系列的 x_axis 順序必須一致
  -- （/back-end/component-data-apis）。若改用 ORDER BY data DESC，五個年齡層會各自
  -- 排出不同的行政區順序，後端編譯 three_d 時是按列序 append、不檢查數值屬於哪個
  -- 分類（componentData.go:301-325），數字會安靜地對到錯的行政區。
  --
  -- 下列陣列由資料庫查出（依 15-39 歲合計人口降冪），不是手寫的。
  -- chart_9001.json 用的是同一份順序，兩條路徑因此不會分岔。
  ORDER BY
    ARRAY_POSITION(ARRAY[
${wrapList(order, 8, "      ")}
    ]::varchar[], t.x_axis),
    ARRAY_POSITION(ARRAY[
      ${BINS.map(([, l]) => q(l)).join(",")}
    ]::varchar[], t.y_axis)
  $q$,
  ${q(c.key)}
)
-- 冪等：改了 SQL 或說明之後要能直接重跑。
-- 原本沒有這段，第二次執行會撞 query_charts_pkey，只能先手動 DELETE——
-- 而「先手動刪一下」正是比賽現場最容易漏做、漏做又不會有人發現的一步。
ON CONFLICT (index, city) DO UPDATE SET
  source = EXCLUDED.source,
  short_desc = EXCLUDED.short_desc,
  long_desc = EXCLUDED.long_desc,
  use_case = EXCLUDED.use_case,
  links = EXCLUDED.links,
  contributors = EXCLUDED.contributors,
  query_type = EXCLUDED.query_type,
  query_chart = EXCLUDED.query_chart;`;
}

const blocks = [];
for (const c of CITIES) {
	const order = await districtOrder(c.table, c.total);
	if (order.length !== c.n) throw new Error(`${c.table} 取到 ${order.length} 區，預期 ${c.n}`);
	blocks.push({ c, order, sql: cityBlock(c, order) });
	console.log(`${c.key}: ${order.length} 區，首三 ${order.slice(0, 3).join(" ")}`);
}

const file = `-- ═══ 青年人口分區：組件註冊 ═══
--
-- 對應影片「七、建立一個新組件 3.0」講的三個步驟：
--   1. components        建組件本體（id 與 index）
--   2. query_charts      每個城市一筆設定（含取數的 SQL）
--   3. component_charts  圖表樣式，跨城市共用只有一筆
--
-- id 9001 為避開官方既有編號而選；正式併入時請改為主辦方指派的編號，
-- 並同步修改下方所有出現 9001 的地方與 dashboards.components 陣列。
--
-- ── 年齡範圍：15-39，不是 18-35 ──
-- 《青年基本法》的青年是 18-35 歲，但公開統計採 5 歲組距，18 與 35 都落在
-- 組距中間。要精確切出 18-35 就必須推估組距內的單一年齡人口——那會產生
-- 「看起來很精準但實際不存在」的數字，是本專案明文禁止的事。
--
-- 因此改用五個完整組距相加：
--   15-39 = percent6 + percent7 + percent8 + percent9 + percent10
--         = 15-19  + 20-24  + 25-29  + 30-34  + 35-39
-- 每一個都是官方原始數字，零內插、零拆分。
--
-- 這是 Exact Youth Proxy（B 級）。先前版本用 20-24/25-29/30-34 三個組距，
-- 只是 Youth Segment（C 級）——合法，但涵蓋範圍比可用的窄。
-- 本檔由 scratchpad/gen-sql.mjs 產生，行政區順序向資料庫查得。

-- ── 1. 組件本體 ──
INSERT INTO public.components (id, index, name) VALUES
  (9001, ${q("youth_population_district")}, ${q("青年人口分區")})
ON CONFLICT (id) DO UPDATE SET index = EXCLUDED.index, name = EXCLUDED.name;

-- ── 2. 各城市設定 ──
-- 注意：影片明確提醒，若只建 metrotaipei 而沒有 taipei，組件在畫面上會出問題，
--       所以兩筆都要建。
${blocks.map((b) => b.sql).join("\n\n")}

-- ── 3. 圖表樣式（跨城市共用，只有一筆）──
-- 五個組距是有序資料（由年輕到年長），所以用漸層而不是對比色。
-- 保留原本三色作為錨點（藍／綠／黃），中間補兩色。
INSERT INTO public.component_charts (index, color, types, unit) VALUES
  (${q("youth_population_district")},
   ${q("{#5a9cf8,#4FB3C4,#56B96D,#A8C95F,#F8CF58}")},
   ${q("{DistrictChart,ColumnChart}")},
   ${q("仟人")})
ON CONFLICT (index) DO UPDATE
  SET color = EXCLUDED.color, types = EXCLUDED.types, unit = EXCLUDED.unit;
`;

writeFileSync(OUT, file);
console.log("\n寫入:", OUT, `(${file.split("\n").length} 行)`);
