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
import { readFileSync, existsSync } from "node:fs";
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

/**
 * 表名 → 欄位陣列。
 *
 * 走 pg_attribute 而不是 information_schema.columns：
 * 後者**看不到 materialized view**，而 youth_fact 正是 matview。
 * 用 information_schema 會讓它被誤判成「資料表不存在」，
 * 而錯誤訊息會叫你去檢查表名——方向完全錯。
 *
 * relkind: r=一般表, v=view, m=matview, p=分割表, f=外部表
 */
async function loadSchema(db) {
	const rows = await psqlJSON(
		`SELECT c.relname AS tbl,
		        array_agg(a.attname ORDER BY a.attnum) AS cols
		 FROM pg_class c
		 JOIN pg_namespace n ON n.oid = c.relnamespace
		 JOIN pg_attribute a ON a.attrelid = c.oid
		 WHERE n.nspname = 'public'
		   AND c.relkind IN ('r','v','m','p','f')
		   AND a.attnum > 0 AND NOT a.attisdropped
		 GROUP BY c.relname`, db);
	const out = {};
	for (const r of rows) out[r.tbl] = r.cols;
	return out;
}

/**
 * catalog-youth.yaml：youth_fact 的 151 組（資料集 × 指標）。
 * 由 tools/gen-youth-catalog.mjs 從資料庫產生，不手寫——
 * 手寫會寫錯指標名，也會在新增 DAG 後腐爛。
 */
function loadYouth() {
	const f = path.join(DIR, "catalog-youth.yaml");
	if (!existsSync(f)) return null;
	return yaml.load(readFileSync(f, "utf8"));
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

	// youth_fact 的指標目錄。掛在回傳值上而不是塞進 tables，
	// 因為它不是「欄位」而是「一個欄位的可能取值」——形狀不同。
	const youth = loadYouth();

	// validateSpec 只認 catalog 有說明過、且資料庫真的有的欄位
	const columns = Object.fromEntries(Object.entries(tables).map(([n, t]) => [n, Object.keys(t.fields)]));
	return {
		tables,
		columns,
		aggRules: doc.agg_rules || {},
		youthDatasets: youth?.youth_datasets || null,
		ambiguousIndicators: new Set(youth?.ambiguous_indicators || []),
		issues,
	};
}

/** 執行查詢，回傳物件陣列。純讀取用途。 */
export async function query(sql, db) {
	return psqlJSON(sql, db);
}

/**
 * youth_fact 的指標清單，序列化成給模型看的文字。
 *
 * 用精簡的一行一指標，不是完整 YAML：151 組如果照 YAML 全貼是 1,500 行，
 * 把 prompt 撐大四倍卻沒有多給什麼——模型要的是「有哪些指標、是什麼、
 * 能不能加總」，不是每一組的列數。
 *
 * 歧義指標標上 ⚠，模型才知道要同時指定 dataset_id。
 */
export function youthIndicatorsForPrompt(cat) {
	if (!cat.youthDatasets) return "";
	const out = ["## youth_fact 可用的指標", ""];
	out.push("格式：indicator_id  [value_type]  單位  地理層級  期間  gender=可用的性別值");
	out.push("⚠ 標記代表該 indicator_id 跨資料集重複，必須同時指定 dataset_id。");
	out.push("");
	for (const [ds, d] of Object.entries(cat.youthDatasets)) {
		const topics = d.topics?.length ? `（${d.topics.join("、")}）` : "";
		out.push(`### ${ds} — ${d.label}${topics}`);
		for (const [ind, i] of Object.entries(d.indicators || {})) {
			const warn = cat.ambiguousIndicators?.has(ind) ? " ⚠" : "";
			const bits = [
				`  ${ind}${warn}`,
				`[${i.value_type}]`,
				i.unit ? `${i.unit}` : "",
				`${i.area_levels.join("/")}`,
				`${i.period}`,
				// 性別值一定要印出來：預設篩 total 會在 21/70 個資料集炸掉
				i.genders ? `gender=${i.genders.join(",")}` : "",
			].filter(Boolean);
			out.push(bits.join("  "));
		}
		out.push("");
	}
	return out.join("\n");
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
