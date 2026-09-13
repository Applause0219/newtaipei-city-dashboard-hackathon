// 圖表型別的覆蓋度與時間序列
//
// 這組測試防的是兩個已經發生過的退化：
//
// 1. **agent 看不到圖表清單**。ALLOWED_CHARTS 有 20 種圖表，
//    chartOptionsForPrompt() 也早就寫好了，但它只被 spec-prompt.js 用到——
//    那是舊的一次性管線。agent.js 的 system prompt 從頭到尾只出現過
//    ColumnChart 與 DistrictChart 各一次，就在那個 ComponentSpec 範例裡。
//    模型照著範例填，於是每一題畫出來的圖都一樣。
//
// 2. **time 被一條過時的規則封死**。prompt 曾寫「本專案的表都沒有真正的
//    日期欄位」——寬表時代成立，長表建好之後 youth_fact.period_start
//    就是 date 了，規則沒跟著改。代價是折線圖整個用不到，
//    而折線圖在官方 223 個組件裡被用了 34 次。
//
// 需要 PostgreSQL 的案例集中在 C 組，沒有資料庫時會自己跳過。
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { validateSpec, compileSpec, ALLOWED_CHARTS, chartOptionsForPrompt } from "./component-spec.js";
import { systemPrompt } from "./agent.js";
import { loadCatalog } from "./catalog.js";

let catalog = null;
let dbUp = false;
try {
	catalog = await loadCatalog();
	dbUp = Boolean(catalog?.columns?.youth_fact_named);
} catch { /* 沒有資料庫就只跑純函式的部分 */ }

/** 乾淨的 time spec：條件補齊，不會撞到 gender / dataset_id 的守門 */
const timeSpec = (over = {}) => ({
	index: "youth_pop_trend",
	name: "青年人口逐年變化",
	city: "metrotaipei",
	table: "youth_fact_named",
	query_type: "time",
	x: { column: "period_start" },
	aggregate: "sum",
	filters: [
		{ column: "area_level", eq: "district" },
		{ column: "dataset_id", eq: "youth_pop_single_age" },
		{ column: "gender", eq: "total" },
		{ column: "age_lower", gte: 15 },
		{ column: "age_upper", lte: 29 },
	],
	series: [
		{ label: "板橋區", column: "value", filter: { column: "area_name", eq: "板橋區" } },
		{ label: "新莊區", column: "value", filter: { column: "area_name", eq: "新莊區" } },
	],
	chart: { types: ["TimelineSeparateChart"], unit: "人" },
	short_desc: "兩區青年人口的逐年走勢",
	long_desc: "測試用",
	...over,
});

describe("A. agent 必須看得到圖表清單", () => {
	test("system prompt 含有 chartOptionsForPrompt 的內容", () => {
		const p = systemPrompt(catalog || { youthDatasets: {} });
		const opts = chartOptionsForPrompt();
		// 逐行比對，比整段字串比對耐得住排版變動
		for (const line of opts.split("\n")) {
			assert.ok(p.includes(line.trim().split(/\s{2,}/)[0]),
				`prompt 少了 query_type：${line}`);
		}
	});

	test("prompt 至少提到 8 種不同的圖表元件", () => {
		const p = systemPrompt(catalog || { youthDatasets: {} });
		const all = [...new Set(Object.values(ALLOWED_CHARTS).flat())];
		const seen = all.filter((t) => p.includes(t));
		// 退化前是 2 種（ColumnChart、DistrictChart）。門檻訂在 8
		// 是為了留排版調整的空間，同時確保不會又縮回只剩範例裡那兩種。
		assert.ok(seen.length >= 8,
			`prompt 只提到 ${seen.length} 種圖表：${seen.join("、")}`);
	});

	test("BarChart 必須出現——它是官方最常用的（223 個組件裡 63 次）", () => {
		const p = systemPrompt(catalog || { youthDatasets: {} });
		assert.ok(p.includes("BarChart"), "prompt 沒提到 BarChart");
	});

	test("time 的三種折線圖都要在清單裡", () => {
		const p = systemPrompt(catalog || { youthDatasets: {} });
		for (const t of ALLOWED_CHARTS.time) {
			assert.ok(p.includes(t), `prompt 少了 ${t}`);
		}
	});
});

describe("B. time 的守門", { skip: dbUp ? false : "需要 PostgreSQL" }, () => {
	test("條件補齊的 time spec 要通過", () => {
		const v = validateSpec(timeSpec(), catalog);
		assert.ok(v.ok, v.errors?.join(" / "));
	});

	test("x 軸不是 date 就要擋下，並說清楚該用什麼", () => {
		const v = validateSpec(timeSpec({ x: { column: "area_name" } }), catalog);
		assert.ok(!v.ok);
		const msg = v.errors.join(" ");
		assert.match(msg, /time 的 x 軸必須是 date/);
		assert.match(msg, /three_d/, "要告訴模型改用哪一種");
	});

	test("time 不可以設 latest_by——會只剩一個時間點", () => {
		const v = validateSpec(timeSpec({ latest_by: "period_start" }), catalog);
		assert.ok(!v.ok);
		assert.match(v.errors.join(" "), /latest_by/);
	});

	test("time 配長條圖要擋下並列出可用的圖", () => {
		const v = validateSpec(timeSpec({ chart: { types: ["BarChart"] } }), catalog);
		assert.ok(!v.ok);
		assert.match(v.errors.join(" "), /TimelineSeparateChart/);
	});
});

describe("C. time 編出來的 SQL 與資料形狀", { skip: dbUp ? false : "需要 PostgreSQL" }, () => {
	test("x 軸不轉 varchar——前端要的是能 new Date() 的值", () => {
		const sql = compileSpec(timeSpec(), []);
		assert.match(sql, /"period_start" AS x_axis/);
		assert.ok(!/period_start"::varchar AS x_axis/.test(sql),
			"time 的 x 軸被轉成字串了");
	});

	test("不套用 three_d 那套 xOrder 補齊（每個點自帶座標）", () => {
		const sql = compileSpec(timeSpec(), ["2020-01-01", "2021-01-01"]);
		assert.ok(!sql.includes("LEFT JOIN"),
			"time 不該有 LEFT JOIN 補齊——那是 three_d 對齊 category 用的");
		assert.ok(!sql.includes("coalesce"), "time 不該把缺漏補成 0");
	});

	test("時間軸照時序排，數列順序固定", () => {
		const sql = compileSpec(timeSpec(), []);
		assert.match(sql, /ORDER BY[\s\S]*ARRAY_POSITION[\s\S]*t\.x_axis/);
	});

	test("實際跑一次：兩個數列、點數相同、格式是前端要的時間字串", async () => {
		const { generateComponent } = await import("./generate.js");
		const r = await generateComponent(timeSpec());
		assert.ok(r.ok, r.errors?.join(" / "));
		assert.equal(r.chartable, true);
		assert.equal(r.chart.data.length, 2);

		const [a, b] = r.chart.data;
		assert.equal(a.name, "板橋區");
		assert.equal(b.name, "新莊區");
		assert.equal(a.data.length, b.data.length, "兩條線的時間點數要一樣");
		assert.ok(a.data.length >= 3, "少於 3 點不該判為可畫");

		// 後端送出的格式是 Go 的 "2006-01-02T15:04:05+08:00"，
		// TimelineSeparateChart 的 parseTime 直接對這個格式做字串替換。
		for (const p of a.data) {
			assert.match(p.x, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$/,
				`時間格式不對：${p.x}`);
			assert.equal(typeof p.y, "number");
		}

		// 時序遞增——ORDER BY 沒生效的話這裡會炸
		const ts = a.data.map((p) => new Date(p.x).getTime());
		assert.deepEqual(ts, [...ts].sort((x, y) => x - y), "時間沒有照順序");
	});

	test("stats.period 報的是實際查到的時間範圍，不是目錄上的涵蓋範圍", async () => {
		const { generateComponent } = await import("./generate.js");
		const r = await generateComponent(timeSpec());
		assert.match(r.stats.period, /^\d{4}-\d{2}-\d{2} – \d{4}-\d{2}-\d{2}$/);
		assert.equal(r.stats.series, 2);
		assert.equal(r.stats.categories, r.chart.data[0].data.length);
	});

	test("某個數列的時間點太少時要拒畫，並指名是哪一個", async () => {
		const { generateComponent } = await import("./generate.js");
		// 第二個數列篩死在單一期間，只會有 1 個點。
		// 平均起來「總點數」是夠的（9 + 1），所以只看總數的門檻會放行，
		// 畫出來是一條線旁邊一個孤點——這正是要擋的東西。
		const r = await generateComponent(timeSpec({
			series: [
				{ label: "板橋區", column: "value", filter: { column: "area_name", eq: "板橋區" } },
				{ label: "只有一期", column: "value", filter: { column: "period_start", eq: "2026-01-01" } },
			],
		}));
		assert.ok(r.ok, r.errors?.join(" / "));
		assert.equal(r.chartable, false, "一條線加一個孤點不該判為可畫");
		const why = r.reasons.join(" ");
		assert.match(why, /只有一期/, "訊息要點出是哪個數列");
		assert.ok(!/板橋區（/.test(why), "點數夠的數列不該被列進去");
		// 不畫圖不等於沒答案：數字要整理成 facts 回去
		assert.ok(Array.isArray(r.facts) && r.facts.length > 0);
	});
});

describe("D. 其餘 query_type 不受影響", () => {
	test("two_d 仍然轉 varchar（分類軸本來就是字串契約）", () => {
		const sql = compileSpec({
			table: "youth_fact_named", query_type: "two_d",
			x: { column: "area_name" }, aggregate: "avg",
			series: [{ label: "租金", column: "value" }],
		}, ["板橋區"]);
		assert.match(sql, /::varchar AS x_axis/);
	});

	test("three_d 仍然用 LEFT JOIN 補齊", () => {
		const sql = compileSpec({
			table: "youth_fact_named", query_type: "three_d",
			x: { column: "area_name" }, aggregate: "sum",
			series: [
				{ label: "男", column: "value", filter: { column: "gender", eq: "male" } },
				{ label: "女", column: "value", filter: { column: "gender", eq: "female" } },
			],
		}, ["板橋區", "新莊區"]);
		assert.ok(sql.includes("LEFT JOIN"));
		assert.ok(sql.includes("coalesce"));
	});
});

test("charttypes.test.mjs 跑完", () => {
	console.log(dbUp ? "charttypes：全部執行" : "charttypes：無資料庫，只跑 A 組");
});
