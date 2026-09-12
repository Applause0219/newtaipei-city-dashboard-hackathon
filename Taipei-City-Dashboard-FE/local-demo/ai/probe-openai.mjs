// OpenAI 模型不吃 Anthropic 的 messages 格式，要走 Bedrock 的 Converse API。
// @aws-sdk/client-bedrock-runtime 是 bedrock-sdk 的相依，已經裝好了。
import "./load-secrets.js";
import { BedrockRuntimeClient, ConverseCommand } from "@aws-sdk/client-bedrock-runtime";

const REGION = process.argv[2] || "us-west-2";
const client = new BedrockRuntimeClient({ region: REGION });

// ON_DEMAND 的直接用原 ID；INFERENCE_PROFILE 的要加 us. 前綴
const IDS = [
	"openai.gpt-oss-120b-1:0",
	"openai.gpt-oss-20b-1:0",
	"us.openai.gpt-6-astra",
	"us.openai.gpt-5.6-terra",
	"us.openai.gpt-5.6-sol",
	"us.openai.gpt-5.6-luna",
	"openai.gpt-oss-safeguard-120b",
	"openai.gpt-oss-safeguard-20b",
];

console.log(`${REGION}：實測 ${IDS.length} 個 OpenAI 模型\n`);
for (const modelId of IDS) {
	const t0 = Date.now();
	try {
		const r = await client.send(new ConverseCommand({
			modelId,
			messages: [{ role: "user", content: [{ text: '只回傳 JSON：{"ok":true}' }] }],
			inferenceConfig: { maxTokens: 64, temperature: 0 },
		}));
		const txt = (r.output?.message?.content || []).map((c) => c.text || "").join("").trim();
		console.log(`✔ ${String(Date.now()-t0).padStart(6)}ms  ${modelId}`);
		console.log(`             → ${txt.replace(/\s+/g," ").slice(0,70)}  [stop=${r.stopReason}]`);
	} catch (e) {
		console.log(`✗ ${String(Date.now()-t0).padStart(6)}ms  ${modelId}`);
		console.log(`             → ${e.name}: ${String(e.message).replace(/\s+/g," ").slice(0,110)}`);
	}
}
