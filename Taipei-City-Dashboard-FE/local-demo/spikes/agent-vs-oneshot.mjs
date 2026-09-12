// 同一個問題，兩條路跑一次，看差在哪。
//
// 這支存在的唯一目的是回答「Agent 層到底在幹嘛」。
//
//   現況（one-shot）：模型讀 26KB 的資料目錄「說明書」，一次寫完答案。
//                     從頭到尾沒看過任何一筆真實資料。
//   Agent：           模型拿到三個工具，自己決定查什麼、查幾次，
//                     看到真資料之後再決定怎麼回答。
//
// 跑法（需要 secrets.env 與本機 PostgreSQL）：
//   npm i @earendil-works/pi-agent-core @earendil-works/pi-ai
//   node spikes/agent-vs-oneshot.mjs "新北市各區的青年失業率"
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { promisify } from "node:util";
import "../ai/load-secrets.js";
import { runAgentLoop } from "@earendil-works/pi-agent-core";
import { bedrockProviderModule } from "@earendil-works/pi-ai/bedrock-provider";

const run = promisify(execFile);

// 中文標籤來自 catalog-youth.yaml（由 tools/gen-youth-catalog.mjs 產生）
const yaml = createRequire(import.meta.url)("js-yaml");
const LABELS = yaml.load(
	readFileSync(new URL("../ai/catalog-youth.yaml", import.meta.url), "utf8"),
).youth_datasets;
const QUESTION = process.argv[2] || "新北市各區的青年失業率";

async function sql(q) {
	const wrapped = `SELECT coalesce(json_agg(_r_), '[]'::json) FROM (${q}) _r_`;
	const { stdout } = await run("psql", ["-d", "dashboard", "-tAc", wrapped], { maxBuffer: 32e6 });
	return JSON.parse(stdout.trim() || "[]");
}
const lit = (s) => "'" + String(s).replace(/'/g, "''") + "'";

// ── 三個工具。刻意做得很小：重點是「模型能不能自己決定查什麼」 ──
const calls = [];

const searchIndicators = {
	name: "search_indicators",
	label: "搜尋指標",
	description: "用關鍵字搜尋可用的統計指標，回傳指標代號、資料集、地理層級、期間。",
	parameters: {
		type: "object",
		properties: { keyword: { type: "string", description: "中文或英文關鍵字" } },
		required: ["keyword"],
	},
	async execute(id, p) {
		calls.push(`search_indicators("${p.keyword}")`);
		// 中文關鍵字要比對中文標籤。第一版只比對英文的 indicator_id／dataset_id，
		// 於是 agent 用「失業率」查了六次都查不到，然後下了「沒有這份資料」的
		// 錯誤結論——實際上 youth_unemployment_rate_age 一直都在。
		// 這一題示範的是：agent 的品質完全取決於工具的品質。
		const kw = p.keyword;
		const hits = new Set();
		for (const [ds, d] of Object.entries(LABELS)) {
			const dsMatch = d.label.includes(kw) || (d.topics || []).some((t) => t.includes(kw));
			for (const ind of Object.keys(d.indicators || {})) {
				if (dsMatch || ind.toLowerCase().includes(kw.toLowerCase())) hits.add(ind);
			}
		}
		const extra = hits.size ? ` OR indicator_id IN (${[...hits].map(lit).join(",")})` : "";
		const rows = await sql(`
			SELECT indicator_id, dataset_id,
			       string_agg(DISTINCT area_level, '/') AS area_levels,
			       min(period_start)::text AS from_, max(period_end)::text AS to_
			FROM public.youth_fact_named
			WHERE indicator_id ILIKE ${lit("%" + kw + "%")}
			   OR dataset_id  ILIKE ${lit("%" + kw + "%")}${extra}
			GROUP BY 1, 2
			LIMIT 25`);
		// 附上中文名，模型才判斷得出哪個是它要的
		for (const r of rows) r.label = LABELS[r.dataset_id]?.label || "";
		return { content: [{ type: "text", text: JSON.stringify(rows) }], details: { n: rows.length } };
	},
};

const inspectIndicator = {
	name: "inspect_indicator",
	label: "檢視指標",
	description: "查一個指標的細節：有哪些地理層級、性別值、年齡區間、期間、列數。",
	parameters: {
		type: "object",
		properties: { indicator_id: { type: "string" } },
		required: ["indicator_id"],
	},
	async execute(id, p) {
		calls.push(`inspect_indicator("${p.indicator_id}")`);
		const rows = await sql(`
			SELECT dataset_id,
			       string_agg(DISTINCT area_level, '/') AS area_levels,
			       string_agg(DISTINCT coalesce(gender,'(null)'), '/') AS genders,
			       string_agg(DISTINCT value_type, '/') AS value_types,
			       min(age_lower)::text AS age_lo, max(age_upper)::text AS age_hi,
			       min(period_start)::text AS from_, max(period_end)::text AS to_,
			       count(*) AS rows
			FROM public.youth_fact_named
			WHERE indicator_id = ${lit(p.indicator_id)}
			GROUP BY 1`);
		return { content: [{ type: "text", text: JSON.stringify(rows) }], details: {} };
	},
};

const queryByDistrict = {
	name: "query_by_district",
	label: "查各行政區數值",
	description: "取某個指標在新北市各行政區的最新數值（依年齡區間可選篩選）。沒有行政區層級資料時回空陣列。",
	parameters: {
		type: "object",
		properties: {
			indicator_id: { type: "string" },
			age_lower: { type: "number", description: "可選，年齡下界" },
			age_upper: { type: "number", description: "可選，年齡上界" },
		},
		required: ["indicator_id"],
	},
	async execute(id, p) {
		calls.push(`query_by_district("${p.indicator_id}"${p.age_lower ? `, ${p.age_lower}-${p.age_upper}` : ""})`);
		const age = p.age_lower != null
			? ` AND age_lower >= ${Number(p.age_lower)} AND age_upper <= ${Number(p.age_upper ?? 200)}` : "";
		const rows = await sql(`
			SELECT area_name, round(sum(value)::numeric, 1) AS value
			FROM public.youth_fact_named
			WHERE indicator_id = ${lit(p.indicator_id)} AND area_level = 'district'${age}
			  AND period_start = (SELECT max(period_start) FROM public.youth_fact_named
			                      WHERE indicator_id = ${lit(p.indicator_id)} AND area_level = 'district')
			GROUP BY 1 ORDER BY 2 DESC`);
		return { content: [{ type: "text", text: JSON.stringify(rows) }], details: { n: rows.length } };
	},
};

const model = {
	id: "us.anthropic.claude-sonnet-4-6",
	name: "Claude Sonnet 4.6 (Bedrock)",
	api: "bedrock-converse-stream",
	provider: "amazon-bedrock",
	baseUrl: "", reasoning: false, input: ["text"],
	cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
	contextWindow: 200000, maxTokens: 4096,
};

const context = {
	systemPrompt:
		"你是新北市青年儀表板的分析助理。\n" +
		"你有工具可以查真實資料，**回答前一定要先查**，不可以憑目錄描述猜。\n" +
		"如果使用者要的東西查不到，先用工具確認真的沒有，再提出你查到的替代方案。\n" +
		"用台灣繁體中文回答，簡潔，附上實際數字。",
	messages: [],
	tools: [searchIndicators, inspectIndicator, queryByDistrict],
};

const config = { model, convertToLlm: (m) => m, maxTokens: 2048, temperature: 0, region: process.env.AWS_REGION };

console.log("問題：" + QUESTION);
console.log("\n" + "─".repeat(66));
console.log("Agent 路徑（模型自己決定查什麼）");
console.log("─".repeat(66));

const t0 = Date.now();
const out = await runAgentLoop(
	[{ role: "user", content: [{ type: "text", text: QUESTION }] }],
	context, config,
	(e) => { if (e.type === "toolCall") process.stdout.write("."); },
	undefined, bedrockProviderModule.stream,
);
const ms = Date.now() - t0;

console.log("\n\n【模型實際呼叫的工具，依序】");
calls.forEach((c, i) => console.log(`  ${i + 1}. ${c}`));

const last = out[out.length - 1];
const text = (last?.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
console.log("\n【回答】\n" + text);
console.log(`\n（${calls.length} 次工具呼叫，${(ms / 1000).toFixed(1)} 秒）`);
