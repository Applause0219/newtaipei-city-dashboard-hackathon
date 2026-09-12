// 長表三個根因的回歸測試。
//
// 三個都是 22 題探測跑出來的真實失敗，不是想像的情境：
//   A  gender='total' 在 21/70 個資料集會把資料濾光（那些資料集沒有 total 列）
//   B  兩個數列的行政區涵蓋不同時，UNION 出來長度不一致 → 數值對到錯的區
//   C  模型在 JSON 前面先寫一段推論，解析器只剝頭尾圍欄所以失敗
//
// 跑法：node longtable.test.mjs
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { compileSpec, validateSpec, extractJsonObject } from "./component-spec.js";
import { loadCatalog, query } from "./catalog.js";

const run = promisify(execFile);
let pass = 0, fail = 0;
const results = [];

function ok(name, cond, detail = "") {
	if (cond) { pass++; results.push(`  ✓ ${name}`); }
	else { fail++; results.push(`  ✗ ${name}${detail ? "\n      " + detail : ""}`); }
}
function group(t) { results.push(`\n${t}`); }

const cat = await loadCatalog();

// ────────────────────────────────────────────────────────────
group("C. 模型在 JSON 前後夾雜散文時仍要解析得出來");

const CASES = [
	["純 JSON", '{"a":1}', { a: 1 }],
	["頭尾圍欄", '```json\n{"a":1}\n```', { a: 1 }],
	["前面有散文", '我先分析一下：\n\n目錄裡有 X。\n\n```json\n{"a":1}\n```', { a: 1 }],
	["前後都有散文", '分析：\n```json\n{"a":1}\n```\n以上。', { a: 1 }],
	["沒有圍欄但有散文", '我的結論是 {"a":1} 這樣', { a: 1 }],
	["巢狀物件", 'x\n```json\n{"a":{"b":[1,2]}}\n```', { a: { b: [1, 2] } }],
	["字串裡有大括號", '```json\n{"a":"{not json}"}\n```', { a: "{not json}" }],
];
for (const [name, input, want] of CASES) {
	let got;
	try { got = extractJsonObject(input); } catch (e) { got = "拋例外：" + e.message; }
	ok(name, JSON.stringify(got) === JSON.stringify(want),
		`得到 ${JSON.stringify(got)}，預期 ${JSON.stringify(want)}`);
}
ok("完全沒有 JSON 時回 null", extractJsonObject("完全沒有") === null);

// ────────────────────────────────────────────────────────────
group("A. 篩選不存在的性別值要被擋下，而不是回空結果");

const baseA = {
	index: "t", name: "測試", city: "metrotaipei", table: "youth_fact_named",
	query_type: "two_d", x: { column: "area_name" }, aggregate: "sum",
	chart: { types: ["ColumnChart"] },
};
const marriage = {
	...baseA,
	filters: [
		{ column: "dataset_id", eq: "youth_marriage_age_ntpc" },
		{ column: "area_level", eq: "district" },
		{ column: "gender", eq: "total" },
	],
	series: [{ label: "結婚", column: "value", filter: { column: "indicator_id", eq: "marriage_count" } }],
};
const rA = validateSpec(marriage, cat);
ok("結婚資料沒有 total 列，篩 total 要擋下", !rA.ok,
	rA.ok ? "竟然通過了" : rA.errors.join("；"));
ok("錯誤訊息要說出實際有哪些值",
	!rA.ok && rA.errors.some((e) => /male|female/.test(e)),
	(rA.errors || []).join("；"));

const marriageOk = { ...marriage, filters: marriage.filters.slice(0, 2) };
ok("不篩 gender 就該通過", validateSpec(marriageOk, cat).ok,
	(validateSpec(marriageOk, cat).errors || []).join("；"));

// ────────────────────────────────────────────────────────────
group("B. 數列涵蓋的行政區不同時，每個數列仍要補齊所有 x");

const bSpec = {
	...baseA,
	query_type: "three_d",
	aggregate: "avg",
	filters: [{ column: "area_level", eq: "district" }],
	series: [
		{ label: "租金", column: "value", filter: { column: "indicator_id", eq: "rental_contract_rent_median" } },
		{ label: "所得", column: "value", filter: { column: "indicator_id", eq: "income_tax_mean_income" } },
	],
};
// 先取 x 的全集（29 區），再看編出來的 SQL 每個數列是不是都 29 列
const universe = (await query(
	`SELECT DISTINCT area_name FROM public.youth_fact_named
	 WHERE area_level='district' ORDER BY area_name`)).map((r) => r.area_name);
ok("行政區全集有 29 個", universe.length === 29, `實際 ${universe.length}`);

const sqlB = compileSpec(bSpec, universe);
let rowsB = [];
try { rowsB = await query(sqlB); } catch (e) { rowsB = [{ _err: e.message }]; }
ok("SQL 跑得起來", !rowsB[0]?._err, rowsB[0]?._err || "");

const bySeries = {};
for (const r of rowsB) (bySeries[r.y_axis] ||= []).push(r.x_axis);
const counts = Object.entries(bySeries).map(([k, v]) => `${k}=${v.length}`).join(" ");
ok("兩個數列的列數都等於行政區全集",
	Object.values(bySeries).every((v) => v.length === universe.length),
	`實際 ${counts}（全集 ${universe.length}）`);
ok("兩個數列的 x 順序完全一致",
	Object.values(bySeries).length === 2 &&
	Object.values(bySeries)[0].join() === Object.values(bySeries)[1].join(),
	counts);

// ────────────────────────────────────────────────────────────
console.log(results.join("\n"));
console.log(`\nlongtable.test.mjs: ${pass}/${pass + fail} 通過`);
if (fail) process.exitCode = 1;
