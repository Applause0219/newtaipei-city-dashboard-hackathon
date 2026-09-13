// 從資料庫 + job_config.json 產生 youth_fact 的資料目錄。
//
// 為什麼用產生的而不是手寫：
//   151 組（資料集 × 指標）、70 個資料集。手寫會有兩個問題——寫錯
//   （指標不存在，模型挑了就失敗），以及腐爛（新增 DAG 後目錄沒跟上）。
//
// 兩個來源缺一不可，跟 catalog.js 同樣的原則：
//   資料庫        真相：這個指標到底存不存在、是什麼 value_type、涵蓋哪些年
//   job_config    語意：這份資料的中文名、領域分類
//
// ⚠ 為什麼依「資料集」巢狀而不是把 indicator_id 當唯一鍵：
//   indicator_id **不是全域唯一的**。實測 144 個 id 對應 151 組，
//   有 7 個跨資料集重複——包括 population_count 這種最可能被問到的。
//   只用 indicator_id 篩選會把兩個資料集的數字加在一起，
//   SQL 不會報錯，畫出來是一個看起來正常、實際上是兩份資料相加的數字。
//
// 讀 youth_fact_named（帶行政區名的 view）而不是 youth_fact：
// 兩者列數相同，但目錄要與模型實際會查的表一致。
//
// 輸出 ai/catalog-youth.yaml。
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const run = promisify(execFile);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const AI = path.join(HERE, "..", "ai");
const PROJ = path.join(
	HERE, "..", "..", "..",
	"Taipei-City-Dashboard-DE", "dags", "proj_new_taipei_city_dashboard",
);
const DB = process.env.DASHBOARD_DB || "dashboard";

async function q(sql) {
	const wrapped = `SELECT coalesce(json_agg(_r_), '[]'::json) FROM (${sql}) _r_`;
	const { stdout } = await run("psql", ["-d", DB, "-tAc", wrapped],
		{ maxBuffer: 64 * 1024 * 1024 });
	return JSON.parse(stdout.trim() || "[]");
}

// ── 1) 資料庫：每組（資料集 × 指標）實際長什麼樣 ──
const rows = await q(`
	SELECT dataset_id,
	       indicator_id,
	       string_agg(DISTINCT value_type, '/' ORDER BY value_type) AS value_types,
	       string_agg(DISTINCT unit, '/' ORDER BY unit)             AS units,
	       string_agg(DISTINCT area_level, '/' ORDER BY area_level) AS area_levels,
	       min(period_start)::text AS period_from,
	       max(period_end)::text   AS period_to,
	       count(*)                AS rows,
	       bool_or(age_lower IS NOT NULL)                    AS has_age,
	       bool_or(gender IS NOT NULL AND gender <> 'total') AS has_gender,
	       -- 實際有哪些性別值。21/70 個資料集根本沒有 total 列
	       -- （生育率只有 female、結婚只有 male/female、消費是 NULL），
	       -- 模型照習慣篩 total 就會把資料濾光，而 SQL 不會報錯。
	       string_agg(DISTINCT coalesce(gender, '(null)'), '/' ORDER BY coalesce(gender, '(null)')) AS genders
	FROM public.youth_fact_named
	GROUP BY 1, 2
	ORDER BY 1, 2`);

// 跨資料集重複的 indicator_id——模型只填 indicator_id 時必須同時指定 dataset_id
const dup = await q(`
	SELECT indicator_id, count(DISTINCT dataset_id) AS n
	FROM public.youth_fact_named
	GROUP BY 1 HAVING count(DISTINCT dataset_id) > 1
	ORDER BY 1`);

// ── 2) job_config：中文語意 ──
const DIMENSION_TAGS = new Set([
	"youth", "新北市", "New-Taipei-City", "年齡", "性別", "行政區",
	"單齡", "年齡組", "交叉表", "PDF", "API", "SDMX-JSON", "census",
	"age", "OAS", "全國",
]);

const meta = {};
for (const d of fs.readdirSync(PROJ)) {
	const f = path.join(PROJ, d, "job_config.json");
	if (!fs.existsSync(f)) continue;
	try {
		const j = JSON.parse(fs.readFileSync(f, "utf8"));
		const table = j.dag_infos?.ready_data_default_table || d;
		meta[table] = {
			name: j.data_infos?.name_cn || d,
			// tags 混了維度、來源機關與主題，只留主題
			topics: (j.dag_infos?.tags || []).filter((t) => !DIMENSION_TAGS.has(t)),
			dept: j.data_infos?.source_dept || "",
		};
	} catch { /* 壞掉的 config 下面會報出來 */ }
}

// ── 3) 併起來 ──
const esc = (s) => String(s ?? "").replace(/"/g, '\\"');
const issues = [];
const out = [];

out.push("# 由 tools/gen-youth-catalog.mjs 產生，不要手改。");
out.push("# 來源：資料庫 youth_fact（真相）＋ DE 的 job_config.json（語意）");
out.push(`# 產生時間：${new Date().toISOString().slice(0, 16).replace("T", " ")}`);
out.push("");

if (dup.length) {
	out.push("# 這些 indicator_id 跨資料集重複。只篩 indicator_id 會把多份資料");
	out.push("# 的數字加在一起，所以必須同時指定 dataset_id。validateSpec 會擋。");
	out.push("ambiguous_indicators:");
	for (const d of dup) out.push(`  - ${d.indicator_id}   # ${d.n} 個資料集`);
	out.push("");
}

// 依資料集分組
const byDataset = new Map();
for (const r of rows) {
	if (!byDataset.has(r.dataset_id)) byDataset.set(r.dataset_id, []);
	byDataset.get(r.dataset_id).push(r);
}

out.push("youth_datasets:");
for (const [ds, list] of byDataset) {
	const m = meta[ds];
	if (!m) issues.push(`${ds} 在 job_config 找不到中文名`);
	out.push(`  ${ds}:`);
	out.push(`    label: "${esc(m?.name || ds)}"`);
	if (m?.topics?.length) out.push(`    topics: [${m.topics.map((t) => `"${esc(t)}"`).join(", ")}]`);
	if (m?.dept) out.push(`    dept: "${esc(m.dept)}"`);
	out.push("    indicators:");
	for (const r of list) {
		out.push(`      ${r.indicator_id}:`);
		out.push(`        value_type: ${r.value_types}`);
		if (r.units) out.push(`        unit: "${esc(r.units)}"`);
		out.push(`        area_levels: [${String(r.area_levels).split("/").map((a) => `"${a}"`).join(", ")}]`);
		out.push(`        period: "${r.period_from} – ${r.period_to}"`);
		out.push(`        rows: ${r.rows}`);
		if (r.has_age) out.push("        has_age_bands: true");
		out.push(`        genders: [${String(r.genders).split("/").map((g) => `"${g}"`).join(", ")}]`);
	}
}

fs.writeFileSync(path.join(AI, "catalog-youth.yaml"), out.join("\n") + "\n");

console.log(`✔ catalog-youth.yaml：${byDataset.size} 個資料集、${rows.length} 組指標`);
if (dup.length) {
	console.log(`\n⚠ ${dup.length} 個 indicator_id 跨資料集重複（已標進 ambiguous_indicators）：`);
	for (const d of dup) console.log(`  ${d.indicator_id}  ×${d.n}`);
}
if (issues.length) {
	console.log(`\n⚠ ${issues.length} 個語意缺漏：`);
	for (const i of issues.slice(0, 10)) console.log("  " + i);
}

const byTopic = {};
for (const r of rows) {
	for (const t of meta[r.dataset_id]?.topics || ["(未分類)"]) {
		byTopic[t] = (byTopic[t] || 0) + 1;
	}
}
console.log("\n主題分布（指標數，前 20）：");
for (const [k, v] of Object.entries(byTopic).sort((a, b) => b[1] - a[1]).slice(0, 20)) {
	console.log(`  ${String(v).padStart(3)}  ${k}`);
}
