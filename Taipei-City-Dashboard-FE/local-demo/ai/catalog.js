// 資料目錄：模型能用的表與欄位，以及它們的「意思」。
//
// 兩個來源，缺一不可：
//   catalog.yaml        語意（percent7 是 20-24 歲）—— 人工維護
//   information_schema  真相（這張表這個欄位到底存不存在）—— 資料庫
//
// 只有 YAML 有語意但沒有真相：多列一個不存在的欄位，就是在驗證器上開一個洞。
// 只有 information_schema 有真相但沒有語意：欄位叫 percent7，模型無從判斷。
// 所以 loadCatalog 會把兩邊對起來，對不上就在 issues 裡報出來。
//
// 走 psql 而不是 pg 套件，是為了不增加相依——比賽當天少一件會出錯的事。
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

// js-yaml 4 是 CJS，Node 26 不提供 default export，用 createRequire 取
const yaml = createRequire(import.meta.url)("js-yaml");

const run = promisify(execFile);
const DIR = path.dirname(fileURLToPath(import.meta.url));
const DB = process.env.DASHBOARD_DB || "dashboard";
const MAX = 32 * 1024 * 1024;

/**
 * 用 JSON 取回結果，避免分隔符與資料內容衝突。
 * 包裝別名刻意取 `_row_`：若與內層欄位別名撞名，PostgreSQL 會把
 * json_agg(別名) 解析成那個「欄位」而不是整列，只會拿到一欄。
 */
async function psqlJSON(sql, db) {
	const wrapped = `SELECT coalesce(json_agg(_row_), '[]'::json) FROM (${sql}) _row_`;
	const { stdout } = await run("psql", ["-d", db || DB, "-tAc", wrapped], { maxBuffer: MAX });
	return JSON.parse(stdout.trim() || "[]");
}

/** information_schema：表名 → 欄位陣列 */
async function loadSchema(db) {
	const rows = await psqlJSON(
		`SELECT table_name AS tbl, array_agg(column_name ORDER BY ordinal_position) AS cols
		 FROM information_schema.columns
		 WHERE table_schema = 'public'
		 GROUP BY table_name`, db);
	const out = {};
	for (const r of rows) out[r.tbl] = r.cols;
	return out;
}

/** catalog.yaml：解開 same_fields_as 的繼承 */
function loadYaml() {
	const doc = yaml.load(readFileSync(path.join(DIR, "catalog.yaml"), "utf8"));
	const tables = doc.tables || {};
	for (const [name, t] of Object.entries(tables)) {
		if (t.same_fields_as) {
			const base = tables[t.same_fields_as];
			if (!base) throw new Error(`catalog.yaml: ${name} 繼承了不存在的 ${t.same_fields_as}`);
			t.fields = base.fields;
			// 關聯的官方組件也一起繼承：三張表是同一份資料，關聯自然相同
			if (!t.related_official_components) {
				t.related_official_components = base.related_official_components;
			}
		}
	}
	return doc;
}

/**
 * @returns {Promise<{
 *   tables: Record<string, {label, fields: Record<string, {type,label,note,groupable}>}>,
 *   columns: Record<string, string[]>,   // 給 validateSpec 用的存在性檢查
 *   aggRules: Record<string, string[]>,
 *   issues: string[]                     // YAML 與資料庫對不上的地方
 * }>}
 */
export async function loadCatalog(db) {
	const [schema, doc] = await Promise.all([loadSchema(db), Promise.resolve(loadYaml())]);
	const issues = [];
	const tables = {};

	for (const [name, t] of Object.entries(doc.tables || {})) {
		const real = schema[name];
		if (!real) { issues.push(`catalog.yaml 列了資料庫沒有的表：${name}`); continue; }
		const declared = Object.keys(t.fields || {});
		const missing = declared.filter((c) => !real.includes(c));
		if (missing.length) issues.push(`${name}: catalog 列了不存在的欄位 ${missing.join(", ")}`);
		const undocumented = real.filter((c) => !declared.includes(c));
		if (undocumented.length) {
			// 不是錯誤，只是模型用不到——沒有語意就不該讓它挑
			issues.push(`${name}: ${undocumented.length} 個欄位未在 catalog 說明，模型不會看到`);
		}
		tables[name] = { ...t, fields: Object.fromEntries(declared.filter((c) => real.includes(c)).map((c) => [c, t.fields[c]])) };
	}

	// 每張表實際涵蓋哪幾年——去資料庫問，不寫在 YAML 裡。
	//
	// 為什麼重要：本專案的兩張新北表最新年份不同（2023 vs 2024），
	// 同一個問題挑到不同的表，latest_by 就解析到不同年份，
	// 兩個數字都對卻差一萬六，畫面上完全看不出來。
	// 把涵蓋範圍寫進 prompt，模型才有依據挑「比較新的那張」。
	await Promise.all(Object.entries(tables).map(async ([name, t]) => {
		const yearCol = Object.entries(t.fields).find(([, f]) => f.type === "year")?.[0];
		if (!yearCol) return;
		try {
			const [r] = await psqlJSON(
				`SELECT min(${'"' + yearCol + '"'}) AS lo, max(${'"' + yearCol + '"'}) AS hi FROM public.${'"' + name + '"'}`, db);
			if (r) t.coverage = { column: yearCol, from: r.lo, to: r.hi };
		} catch { /* 查不到就不寫，不要因此讓整個 catalog 失敗 */ }
	}));

	// validateSpec 只認 catalog 有說明過、且資料庫真的有的欄位
	const columns = Object.fromEntries(Object.entries(tables).map(([n, t]) => [n, Object.keys(t.fields)]));
	return { tables, columns, aggRules: doc.agg_rules || {}, issues };
}

/** 執行查詢，回傳物件陣列。純讀取用途。 */
export async function query(sql, db) {
	return psqlJSON(sql, db);
}

/** 序列化成給模型看的文字。只有這裡的東西，模型才可能挑到。 */
export function catalogForPrompt(cat) {
	const out = [];
	for (const [name, t] of Object.entries(cat.tables)) {
		out.push(`## ${name} — ${t.label}`);
		if (t.grain) out.push(`粒度：${t.grain}`);
		// 年份範圍直接寫進 prompt：問「最新」時模型才知道哪張表比較新
		if (t.coverage) out.push(`涵蓋年份：${t.coverage.from}–${t.coverage.to}`);
		if (t.note) out.push(`注意：${String(t.note).trim()}`);
		out.push("欄位：");
		for (const [col, f] of Object.entries(t.fields)) {
			const bits = [`  ${col}`, `(${f.type})`, f.label];
			if (f.groupable) bits.push("[可分組]");
			if (f.note) bits.push(`— ${String(f.note).trim()}`);
			out.push(bits.join(" "));
		}
		out.push("");
	}
	return out.join("\n");
}
