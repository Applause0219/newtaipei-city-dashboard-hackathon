// query_indicator 的守門測試。
//
// 由來：2026-09-12 使用者追問「為什麼青年的Ａ1事故佔比這麼高」，
// agent 回答「青年佔新北市人口約 11.7%」——真值 15.0%。
//
// 根因不在模型，在工具：build_component 有 8 道守門，query_indicator 一道都沒有。
// population_count 橫跨 2 個資料集、2 個地理層級、3 種性別值，
// 全部不篩一起加，分母變成 32,376,008（新北市實際約 400 萬）。
// 圖表那條路擋得下來的錯，文字這條路照樣講得出口。
//
// 跑法：node agent-tools.test.mjs
import { buildTools } from "./agent-tools.js";
import { loadCatalog, query } from "./catalog.js";

let pass = 0, fail = 0;
const out = [];
const ok = (name, cond, detail = "") => {
	if (cond) { pass++; out.push(`  ✓ ${name}`); }
	else { fail++; out.push(`  ✗ ${name}${detail ? "\n      " + detail : ""}`); }
};
const group = (t) => out.push(`\n${t}`);

const cat = await loadCatalog();
const { tools, trace } = buildTools(cat);
const T = Object.fromEntries(tools.map((t) => [t.name, t]));
const call = async (name, args) => JSON.parse((await T[name].execute("t", args)).content[0].text);

// ────────────────────────────────────────────────────────────
group("A. 跨資料集的指標，不指定 dataset_id 要被擋下");

const r1 = await call("query_indicator", { indicator_id: "population_count", by: "period" });
ok("population_count 不給 dataset_id 要回錯誤", !!r1.error, JSON.stringify(r1).slice(0, 160));
ok("錯誤要列出可選的資料集", Array.isArray(r1.datasets) && r1.datasets.length === 2,
	JSON.stringify(r1.datasets));
ok("不可以回傳任何數字", r1.rows === undefined);

const r1b = await call("query_indicator", {
	indicator_id: "population_count", by: "period", dataset_id: "不存在的資料集",
});
ok("給錯 dataset_id 要被擋下", !!r1b.error, JSON.stringify(r1b).slice(0, 160));

// 單一資料集的指標不該被誤擋
const r1c = await call("query_indicator", { indicator_id: "traffic_fatal_party_count", by: "period" });
ok("只有一個資料集時不用指定，要正常回數字", !r1c.error && r1c.rows?.length === 5,
	JSON.stringify(r1c).slice(0, 200));

// ────────────────────────────────────────────────────────────
group("B. by=period 不可以把 city 跟 district 的數字疊在一起");

const r2 = await call("query_indicator", {
	indicator_id: "population_count", by: "period", dataset_id: "youth_pop_single_age",
});
ok("預設要篩到 district 層級", r2.applied?.area_level === "district", JSON.stringify(r2.applied));
ok("預設要篩 gender=total", r2.applied?.gender === "total", JSON.stringify(r2.applied));

const y2025 = r2.rows?.find((r) => r.key === "2025-01-01");
ok("2025 新北總人口要是 404 萬量級，不是 3235 萬",
	y2025 && Number(y2025.value) > 3.9e6 && Number(y2025.value) < 4.2e6,
	`實際 ${y2025?.value}`);

// ────────────────────────────────────────────────────────────
group("C. 青年人口佔比：修好之後要算得出 15.0%");

const r3 = await call("query_indicator", {
	indicator_id: "population_count", by: "period",
	dataset_id: "youth_pop_single_age", age_lower: 15, age_upper: 29,
});
const n2025 = r3.rows?.find((r) => r.key === "2025-01-01");
const pct = n2025 && y2025 ? (100 * Number(n2025.value) / Number(y2025.value)) : NaN;
ok("15-29 歲佔比落在 15.0% 附近，不是 11.7%", Math.abs(pct - 15.0) < 0.2,
	`實際 ${Number.isFinite(pct) ? pct.toFixed(1) + "%" : "算不出來"}`);

// 直接對資料庫驗一次，確定測試自己沒搞錯
const truth = (await query(`
	SELECT round(100*sum(value) FILTER (WHERE age_lower>=15 AND age_upper<=29)
	       / nullif(sum(value),0), 1) AS pct
	FROM public.youth_fact_named
	WHERE indicator_id='population_count' AND dataset_id='youth_pop_single_age'
	  AND area_level='district' AND gender='total' AND period_start='2025-01-01'`))[0];
ok("工具算的比例要跟直接下 SQL 一致", Math.abs(pct - Number(truth.pct)) < 0.11,
	`工具 ${pct?.toFixed(1)}%、SQL ${truth.pct}%`);

// ────────────────────────────────────────────────────────────
group("D. 不存在的性別值要擋下，而不是回空結果");

const r4 = await call("query_indicator", {
	indicator_id: "marriage_count", by: "district", gender: "total",
});
ok("結婚資料沒有 total，篩 total 要被擋下", !!r4.error, JSON.stringify(r4).slice(0, 200));
ok("錯誤要說出實際有哪些性別值", Array.isArray(r4.genders) && r4.genders.length > 0,
	JSON.stringify(r4.genders));

const r4b = await call("query_indicator", { indicator_id: "marriage_count", by: "district" });
ok("沒有 total 的指標，不篩 gender 要正常回數字", !r4b.error && r4b.rows?.length > 0,
	JSON.stringify(r4b).slice(0, 200));

// ────────────────────────────────────────────────────────────
group("E. trace 要記下實際生效的條件（使用者唯一能核對的地方）");

trace.length = 0;
await call("query_indicator", {
	indicator_id: "traffic_fatal_party_count", by: "period", age_lower: 15, age_upper: 29,
});
const last = trace[trace.length - 1];
const shown = Object.values(last?.args || {}).join("｜");
ok("trace 要看得到年齡條件", /15-29/.test(shown), shown);
ok("trace 要看得到性別條件", /性別=/.test(shown), shown);
ok("trace 要看得到地理層級", /district/.test(shown), shown);

// ────────────────────────────────────────────────────────────
console.log(out.join("\n"));
console.log(`\nagent-tools.test.mjs: ${pass}/${pass + fail} 通過`);
if (fail) process.exitCode = 1;
