// 重生 chart_9001.json：把青年範圍從 20-34（C 級）改成 15-39（B 級 Exact Youth Proxy）
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { writeFileSync } from "node:fs";
const run = promisify(execFile);

const DIR = "/Users/yucc_0219/orca/projects/新北市AI智慧城市黑客松/Taipei-City-Dashboard/Taipei-City-Dashboard-FE/local-demo";

const BINS = [
	["percent6", "15_19歲人口數"],
	["percent7", "20_24歲人口數"],
	["percent8", "25_29歲人口數"],
	["percent9", "30_34歲人口數"],
	["percent10", "35_39歲人口數"],
];
const sumExpr = BINS.map(([c]) => c).join(" + ");
const Q = String.fromCharCode(39); // 單引號，避開 shell 巢狀引號

const sql = [
	`SELECT "區域別" AS d, ${BINS.map(([c], i) => `round(${c}/1000.0) AS v${i}`).join(", ")}`,
	`FROM public.youth_population_district_newtaipei`,
	`WHERE "年份" = (SELECT max("年份") FROM public.youth_population_district_newtaipei)`,
	`  AND "統計類型" = ${Q}計${Q} AND "區域別" <> ${Q}新北市${Q}`,
	`ORDER BY (${sumExpr}) DESC, "區域別"`,
].join("\n");

const { stdout } = await run("psql", ["-d", "dashboard", "-tAc",
	`SELECT coalesce(json_agg(_r_), ${Q}[]${Q}::json) FROM (${sql}) _r_`], { maxBuffer: 32e6 });
const rows = JSON.parse(stdout.trim());

const categories = rows.map((r) => r.d);
const data = BINS.map(([, name], i) => ({
	name,
	icon: "",
	data: rows.map((r) => Number(r["v" + i])),
}));

writeFileSync(`${DIR}/chart_9001.json`, JSON.stringify({ categories, data, status: "success" }, null, 2) + "\n");

const total = data.reduce((a, s) => a + s.data.reduce((x, y) => x + y, 0), 0);
console.log("區數:", categories.length, "| 數列:", data.length, "| 合計:", total, "仟人");
console.log("前五區:", categories.slice(0, 5).join(" "));
console.log("板橋區各層:", data.map((s) => s.data[0]).join(" / "));
console.log();
console.log("ARRAY_POSITION 順序陣列：");
console.log(categories.map((c) => `${Q}${c}${Q}`).join(","));
