// 路徑 A（靜態 JSON）與路徑 B/C（資料庫）必須給出同一組數字。
// 兩條路分岔過一次——舊版 chart_9001.json 與 02_register_component.sql
// 的行政區順序不同（三重／中和對調）——所以這支驗證從現在起每次都要跑。
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFileSync } from "node:fs";
const run = promisify(execFile);
const Q = String.fromCharCode(39);

const DIR = "/Users/yucc_0219/orca/projects/新北市AI智慧城市黑客松/Taipei-City-Dashboard/Taipei-City-Dashboard-FE/local-demo";

async function psql(db, sql) {
	const { stdout } = await run("psql", ["-d", db, "-tAc", sql], { maxBuffer: 32e6 });
	return stdout;
}

// 取出後端實際會執行的那段 SQL
const chartSQL = (await psql("dashboardmanager",
	`SELECT query_chart FROM public.query_charts
	 WHERE index = ${Q}youth_population_district${Q} AND city = ${Q}metrotaipei${Q}`)).trim();

const rowsRaw = await psql("dashboard",
	`SELECT coalesce(json_agg(_r_), ${Q}[]${Q}::json) FROM (${chartSQL}) _r_`);
const rows = JSON.parse(rowsRaw.trim());

const json = JSON.parse(readFileSync(`${DIR}/chart_9001.json`, "utf8"));

let fail = 0;
const check = (name, ok, detail = "") => {
	console.log(`${ok ? "✓" : "✗"} ${name}${detail ? "  " + detail : ""}`);
	if (!ok) fail++;
};

// 後端編譯 three_d 是按列序 append，所以「順序一致」本身就是正確性條件
const sqlCats = [...new Set(rows.map((r) => r.x_axis))];
check("行政區順序一致", JSON.stringify(sqlCats) === JSON.stringify(json.categories),
	sqlCats.slice(0, 3).join(",") + " vs " + json.categories.slice(0, 3).join(","));

const sqlSeries = [...new Set(rows.map((r) => r.y_axis))];
const jsonSeries = json.data.map((d) => d.name);
check("數列順序一致", JSON.stringify(sqlSeries) === JSON.stringify(jsonSeries),
	sqlSeries.join(",") );

// 逐格比對，不是只比總和——總和相同但錯位的情況，比總和是抓不到的
let cellBad = 0, cells = 0;
for (const [si, sName] of jsonSeries.entries()) {
	for (const [ci, cName] of json.categories.entries()) {
		const row = rows.find((r) => r.y_axis === sName && r.x_axis === cName);
		const a = row ? Number(row.data) : null;
		const b = json.data[si].data[ci];
		cells++;
		if (a !== b) { cellBad++; if (cellBad <= 3) console.log(`    ${cName}/${sName}: SQL=${a} JSON=${b}`); }
	}
}
check(`逐格數值一致（${cells} 格）`, cellBad === 0, cellBad ? `${cellBad} 格不符` : "");

// 年齡範圍必須是五個完整組距，而且連續
const expect = ["15_19歲人口數", "20_24歲人口數", "25_29歲人口數", "30_34歲人口數", "35_39歲人口數"];
check("五個組距且連續（15-39）", JSON.stringify(jsonSeries) === JSON.stringify(expect));

// 設定檔不得把 15-39 說成 18-35
const cfg = JSON.parse(readFileSync(`${DIR}/component_9001.json`, "utf8"));
const text = cfg.long_desc + cfg.short_desc;
check("說明有標示 15-39", text.includes("15-39"));
check("說明仍載明法定 18-35", text.includes("18-35") || text.includes("18 至 35"));
check("配色數與數列數相符", cfg.chart_config.color.length === jsonSeries.length,
	`${cfg.chart_config.color.length} 色 / ${jsonSeries.length} 數列`);

const total = json.data.reduce((a, s) => a + s.data.reduce((x, y) => x + y, 0), 0);
console.log(`\n15-39 歲合計 ${total} 仟人，${json.categories.length} 區，${jsonSeries.length} 個組距`);
console.log(fail === 0 ? `\n路徑一致性: ${8 - fail}/8 通過` : `\n路徑一致性: 失敗 ${fail} 項`);
process.exit(fail ? 1 : 0);
