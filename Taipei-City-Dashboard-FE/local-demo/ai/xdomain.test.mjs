// 跨領域分析的回歸測試。
//
// 由來：2026-09-12 跑了八題跨領域問題，八題只有兩題完全正確。
// 所有錯誤都在同一個地方——需要「再算一步」的比較性數字，
// 當時是模型自己從表格估的。四個根因各自對應下面一組。
//
// 跑法：node xdomain.test.mjs
import { buildTools } from "./agent-tools.js";
import { loadCatalog, query } from "./catalog.js";
import { validateSpec } from "./component-spec.js";

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
const call = async (n, a) => JSON.parse((await T[n].execute("t", a)).content[0].text);

const POP = { indicator_id: "population_count", dataset_id: "youth_pop_single_age",
	age_lower: 15, age_upper: 35 };
const RENT = { indicator_id: "rental_contract_rent_median" };
const INCOME = { indicator_id: "income_tax_mean_income" };
const FERT = { indicator_id: "fertility_rate_total" };
const BIKE = { indicator_id: "youbike_total_slots" };
const INJ = { indicator_id: "traffic_injury_party_count" };

// ────────────────────────────────────────────────────────────
group("A. 相關性由資料庫算（原本三題三題估錯）");

const c1 = await call("correlate_indicators", { a: POP, b: RENT });
ok("青年人口 vs 租金：r 要是 0.668 附近，不是「沒有明顯正相關」",
	Math.abs(c1.pearson - 0.668) < 0.01, JSON.stringify(c1).slice(0, 220));
ok("方向要標成正相關", c1.direction === "正相關", c1.direction);
ok("強弱標籤由程式給，不是模型自己講", c1.strength === "強", c1.strength);
ok("涵蓋範圍不同的區要被列出來（租金只有 23 區）",
	c1.n_districts === 23 && !!c1.excluded_districts,
	`n=${c1.n_districts} excluded=${c1.excluded_districts}`);

const c2 = await call("correlate_indicators", { a: FERT, b: POP });
ok("生育率 vs 青年人口：r=-0.200，要標成微弱不是「相反的關係」",
	Math.abs(c2.pearson + 0.200) < 0.01 && c2.strength === "微弱",
	`r=${c2.pearson} strength=${c2.strength}`);

const c3 = await call("correlate_indicators", { a: INJ, b: BIKE });
ok("事故 vs YouBike：r=0.924，要標成很強不是「不完全一致」",
	Math.abs(c3.pearson - 0.924) < 0.01 && c3.strength === "很強",
	`r=${c3.pearson} strength=${c3.strength}`);
ok("同時要回 Spearman", typeof c3.spearman === "number", String(c3.spearman));

// ────────────────────────────────────────────────────────────
group("B. 比例與排名由資料庫算（原本漏 4 個區、排名顛倒）");

// 租金（元/月）÷ 月均所得（年均千元 ÷ 12 → 元）
const cmp = await call("compare_indicators", {
	numerator: RENT, denominator: INCOME, denominator_scale: 1000 / 12,
});
ok("要回傳全部有交集的行政區，不是模型看過的那幾個",
	cmp.ranked?.length === 23, `實際 ${cmp.ranked?.length}`);
ok("第一名是土城區 37.0%",
	cmp.ranked?.[0]?.area === "土城區" && Math.abs(cmp.ranked[0].ratio - 37.0) < 0.1,
	JSON.stringify(cmp.ranked?.[0]));
ok("五股區要出現在前五（原本整個漏掉）",
	cmp.ranked?.slice(0, 5).some((r) => r.area === "五股區"),
	cmp.ranked?.slice(0, 5).map((r) => `${r.area} ${r.ratio}`).join("、"));
ok("排序必須嚴格遞減（原本 30.5 排在 30.7 前面）",
	cmp.ranked?.every((r, i, a) => i === 0 || Number(a[i - 1].ratio) >= Number(r.ratio)),
	cmp.ranked?.slice(0, 6).map((r) => r.ratio).join(" ≥ "));

// 跟直接下 SQL 比對，確定工具沒算錯
const truth = (await query(`
	WITH m AS (SELECT area_name, sum(value) v FROM public.youth_fact_named
	           WHERE indicator_id='rental_contract_rent_median' AND area_level='district'
	             AND period_start=(SELECT max(period_start) FROM public.youth_fact_named
	                               WHERE indicator_id='rental_contract_rent_median' AND area_level='district')
	           GROUP BY 1),
	     i AS (SELECT area_name, sum(value) v FROM public.youth_fact_named
	           WHERE indicator_id='income_tax_mean_income' AND area_level='district'
	             AND period_start=(SELECT max(period_start) FROM public.youth_fact_named
	                               WHERE indicator_id='income_tax_mean_income' AND area_level='district')
	           GROUP BY 1)
	SELECT m.area_name, round(100*m.v/(i.v*1000/12.0), 2) pct
	FROM m JOIN i USING (area_name) ORDER BY pct DESC LIMIT 1`))[0];
ok("工具算的比例要跟直接下 SQL 完全一致",
	cmp.ranked?.[0]?.area === truth.area_name &&
	Math.abs(Number(cmp.ranked[0].ratio) - Number(truth.pct)) < 0.01,
	`工具 ${cmp.ranked?.[0]?.area} ${cmp.ranked?.[0]?.ratio} / SQL ${truth.area_name} ${truth.pct}`);

// ────────────────────────────────────────────────────────────
group("C. 期間要講清楚（原本把 2025 單年說成 2020-2025 累計）");

const qd = await call("query_indicator", { ...INJ, by: "district" });
ok("by=district 要回報實際解析出來的期間",
	qd.applied?.period === "2025-01-01", JSON.stringify(qd.applied));
const banqiao = qd.rows?.find((r) => r.key === "板橋區");
ok("板橋是 2025 單年的 17547，不是累計的 84584",
	Math.abs(Number(banqiao?.value) - 17547) < 1, String(banqiao?.value));

const qp = await call("query_indicator", { ...POP, by: "period" });
ok("by=period 不可以被釘在單一期間", qp.rows?.length > 5, `${qp.rows?.length} 期`);

// 指定期間：原本做不到，所以模型只能去讀 build_component 的 5 列 preview
const q2021 = await call("query_indicator", { ...POP, age_upper: 39, by: "district", period: "2021-01-01" });
ok("可以指定期間查某一年的分區資料", q2021.rows?.length === 29, `${q2021.rows?.length} 區`);
ok("2021 年板橋 15-39 歲是 171397",
	Math.abs(Number(q2021.rows?.find((r) => r.key === "板橋區")?.value) - 171397) < 1,
	String(q2021.rows?.find((r) => r.key === "板橋區")?.value));
ok("指定期間要回報在 applied 裡", q2021.applied?.period === "2021-01-01", q2021.applied?.period);

// ────────────────────────────────────────────────────────────
group("D. 沒有 district 時要提示先搜尋，不要直接宣告沒資料");

const insp = await call("inspect_indicator", { indicator_id: "resident_marital_status_count" });
const one = Array.isArray(insp) ? insp[0] : insp;
ok("只有 city 層級的指標要附上「先去搜尋」的提示",
	!!one?.before_you_say_no, JSON.stringify(one).slice(0, 200));

const inspOk = await call("inspect_indicator", { indicator_id: "marriage_count" });
const oneOk = Array.isArray(inspOk) ? inspOk[0] : inspOk;
ok("有 district 的指標不該出現那個提示", !oneOk?.before_you_say_no);
ok("marriage_count 確實有 district（原本被誤判成沒有）",
	/district/.test(oneOk?.area_levels || ""), oneOk?.area_levels);

// 做不了分區相關性時，錯誤訊息要指出下一步
const bad = await call("correlate_indicators", {
	a: INCOME, b: { indicator_id: "resident_marital_status_count" },
});
ok("拿只有 city 的指標做分區相關性要被擋下", !!bad.error, JSON.stringify(bad).slice(0, 200));
ok("並且要提示先用中文關鍵字再搜一次", /search_indicators/.test(bad.hint || ""), bad.hint);

// ────────────────────────────────────────────────────────────
group("F. 期間變化由資料庫算（原本自己相減，絕對流失第 5 名排錯）");

const per = await call("compare_periods", {
	indicator: { ...POP, age_upper: 29 }, from: "2021-01-01", to: "2026-01-01",
});
ok("要回傳全部 29 個行政區", per.ranked_by_diff?.length === 29, `實際 ${per.ranked_by_diff?.length}`);
ok("第一名是新莊區 -11061（不是人口最多的板橋）",
	per.ranked_by_diff?.[0]?.area === "新莊區" &&
	Math.abs(Number(per.ranked_by_diff[0].diff) + 11061) < 1,
	JSON.stringify(per.ranked_by_diff?.[0]));
ok("第五名是土城區，不是模型原本寫的新店區",
	per.ranked_by_diff?.[4]?.area === "土城區",
	per.ranked_by_diff?.slice(0, 5).map((r) => `${r.area} ${r.diff}`).join("、"));
ok("排序必須是變化量遞增（流失最多在最前面）",
	per.ranked_by_diff?.every((r, i, a) => i === 0 || Number(a[i - 1].diff) <= Number(r.diff)),
	per.ranked_by_diff?.slice(0, 6).map((r) => r.diff).join(" ≤ "));
ok("同時要給變化率",
	Math.abs(Number(per.ranked_by_diff?.[0]?.pct_change) + 14.68) < 0.1,
	String(per.ranked_by_diff?.[0]?.pct_change));

const perAuto = await call("compare_periods", { indicator: { ...POP, age_upper: 29 } });
ok("不給 from / to 時自動取最早與最新",
	perAuto.from === perAuto.available_periods?.[0] &&
	perAuto.to === perAuto.available_periods?.[perAuto.available_periods.length - 1],
	`${perAuto.from} → ${perAuto.to}`);

const perBad = await call("compare_periods", {
	indicator: { ...POP, age_upper: 29 }, from: "1999-01-01", to: "2026-01-01",
});
ok("不存在的期間要擋下並列出可選期間",
	!!perBad.error && Array.isArray(perBad.available_periods),
	JSON.stringify(perBad).slice(0, 160));

// 兩個期間的比較圖：ComponentSpec 本來就表達得出來，模型原本不知道
const twoPeriod = {
	index: "t", name: "測試", city: "metrotaipei", table: "youth_fact_named",
	query_type: "three_d", x: { column: "area_name" }, aggregate: "sum",
	chart: { types: ["ColumnChart"] },
	filters: [
		{ column: "area_level", eq: "district" },
		{ column: "dataset_id", eq: "youth_pop_single_age" },
		{ column: "gender", eq: "total" },
		{ column: "age_lower", gte: 15 }, { column: "age_upper", lte: 29 },
	],
	series: [
		{ label: "2021", column: "value", filter: { column: "period_start", eq: "2021-01-01" } },
		{ label: "2026", column: "value", filter: { column: "period_start", eq: "2026-01-01" } },
	],
};
const vr = validateSpec(twoPeriod, cat);
ok("兩個期間的 three_d spec 要能通過驗證", vr.ok, (vr.errors || []).join("；"));

// ────────────────────────────────────────────────────────────
group("E. 守門不能因為新增工具而漏掉");

const amb = await call("correlate_indicators", {
	a: { indicator_id: "population_count" }, b: RENT,
});
ok("跨資料集的指標在 correlate 也要擋", !!amb.error, JSON.stringify(amb).slice(0, 180));
const amb2 = await call("compare_indicators", {
	numerator: { indicator_id: "population_count" }, denominator: INCOME,
});
ok("跨資料集的指標在 compare 也要擋", !!amb2.error, JSON.stringify(amb2).slice(0, 180));

// ────────────────────────────────────────────────────────────
console.log(out.join("\n"));
console.log(`\nxdomain.test.mjs: ${pass}/${pass + fail} 通過`);
if (fail) process.exitCode = 1;
