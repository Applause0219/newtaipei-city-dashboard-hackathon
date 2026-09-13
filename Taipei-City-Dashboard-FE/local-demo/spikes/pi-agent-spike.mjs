// Spike：用 pi-agent-core 跑一個「只有領域工具」的 agent，接真的 Bedrock。
//
// 要驗證的是三件事：
//   1. 能不能不用 coding-agent 那套檔案／bash 工具，只註冊自己的工具
//   2. 能不能接上我們已經在用的 Bedrock（us.anthropic.claude-sonnet-4-6）
//   3. 模型會不會真的呼叫工具、拿結果、給答案
import fs from "node:fs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { runAgentLoop } from "@earendil-works/pi-agent-core";
import { bedrockProviderModule } from "@earendil-works/pi-ai/bedrock-provider";

const run = promisify(execFile);

// ── 載入金鑰（沿用專案既有的 secrets.env）──
const SECRETS = "/Users/yucc_0219/orca/projects/新北市AI智慧城市黑客松/Taipei-City-Dashboard/Taipei-City-Dashboard-FE/local-demo/ai/secrets.env";
for (const line of fs.readFileSync(SECRETS, "utf8").split("\n")) {
	const m = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
	if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
}
if (!process.env.AWS_REGION) process.env.AWS_REGION = process.env.AWS_DEFAULT_REGION || "us-west-2";

// ── 一個真的領域工具：查新北各區青年人口 ──
//
// 刻意不是假資料——直接打我們正在用的 PostgreSQL，
// 這樣才知道整條路（模型 → 工具 → 資料庫 → 回模型）通不通。
const queryYouth = {
	name: "query_youth_population",
	label: "查詢青年人口",
	description:
		"查詢新北市各行政區的青年人口（20-34 歲，單位：人）。回傳最新一年的資料，依人口數由多到少排序。",
	parameters: {
		type: "object",
		properties: {
			limit: { type: "number", description: "回傳前幾名行政區，預設 5" },
		},
		required: [],
	},
	async execute(toolCallId, params) {
		const limit = Math.min(Math.max(Number(params?.limit) || 5, 1), 29);
		const sql = `
			SELECT "區域別" AS district,
			       ("percent7" + "percent8" + "percent9")::bigint AS youth
			FROM public.city_age_distribution_newtaipei
			WHERE "統計類型" = '計'
			  AND "區域別" <> '新北市'
			  AND "年份" = (SELECT max("年份") FROM public.city_age_distribution_newtaipei)
			ORDER BY youth DESC
			LIMIT ${limit}`;
		const wrapped = `SELECT coalesce(json_agg(_r_), '[]'::json) FROM (${sql}) _r_`;
		const { stdout } = await run("psql", ["-d", "dashboard", "-tAc", wrapped]);
		const rows = JSON.parse(stdout.trim() || "[]");
		console.log(`   [tool] query_youth_population(limit=${limit}) → ${rows.length} 列`);
		return {
			content: [{ type: "text", text: JSON.stringify(rows) }],
			details: { rows: rows.length, sql },
		};
	},
};

// ── 手工建 Model：npm 版的內建註冊表是空的 ──
const model = {
	id: "us.anthropic.claude-sonnet-4-6",
	name: "Claude Sonnet 4.6 (Bedrock)",
	api: "bedrock-converse-stream",
	provider: "amazon-bedrock",
	baseUrl: "",
	reasoning: false,
	input: ["text"],
	cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
	contextWindow: 200000,
	maxTokens: 4096,
};

const context = {
	systemPrompt:
		"你是新北市青年儀表板的分析助理。回答關於青年人口的問題時，" +
		"一定要先呼叫工具取得真實數字，不可以憑印象回答。用台灣繁體中文作答。",
	messages: [],
	tools: [queryYouth],
};

const config = {
	model,
	convertToLlm: (messages) => messages,
	maxTokens: 1024,
	temperature: 0,
	region: process.env.AWS_REGION,
};

const events = [];
const emit = (e) => {
	events.push(e.type);
	if (e.type === "toolCall") console.log(`   [event] 模型要呼叫: ${e.toolCall?.name ?? "?"}`);
};

const prompts = [{ role: "user", content: [{ type: "text", text: "新北市哪三個區的青年人口最多？請給實際數字。" }] }];

console.log("── 開始 ──");
const t0 = Date.now();
try {
	const out = await runAgentLoop(prompts, context, config, emit, undefined, bedrockProviderModule.stream);
	console.log(`── 完成（${Date.now() - t0}ms）──`);
	console.log("事件序列:", [...new Set(events)].join(" → "));
	const last = out[out.length - 1];
	const text = (last?.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
	console.log("\n最終回答:\n" + text);
} catch (e) {
	console.log(`── 失敗（${Date.now() - t0}ms）──`);
	console.log(e?.name + ": " + String(e?.message).slice(0, 400));
	if (e?.stack) console.log(e.stack.split("\n").slice(1, 5).join("\n"));
}
