// 掃過所有推論設定檔，找出「真的叫得動」的那些。
//
// 為什麼不能只看 ListInferenceProfiles：那支 API 回的是「這個 region 有什麼」，
// 不是「這個帳號被授權用什麼」。實測 us.anthropic.claude-opus-5 列得出來，
// 呼叫卻回 403 not available for this account。當天只信實測結果。
import "./load-secrets.js";
import { AnthropicBedrock } from "@anthropic-ai/bedrock-sdk";
import { SignatureV4 } from "@smithy/signature-v4";
import { Sha256 } from "@aws-crypto/sha256-js";
import { fromNodeProviderChain } from "@aws-sdk/credential-providers";

const REGION = process.argv[2] || process.env.AWS_REGION || "us-west-2";

// 先列出這個 region 有哪些 profile
const host = `bedrock.${REGION}.amazonaws.com`;
const signer = new SignatureV4({ credentials: await fromNodeProviderChain()(), region: REGION, service: "bedrock", sha256: Sha256 });
const q = { maxResults: "200" };
const signed = await signer.sign({ method: "GET", protocol: "https:", hostname: host, path: "/inference-profiles", query: q, headers: { host, accept: "application/json" } });
const listed = JSON.parse(await (await fetch(`https://${host}/inference-profiles?${new URLSearchParams(q)}`, { headers: signed.headers })).text());
const ids = (listed.inferenceProfileSummaries || [])
	.map((p) => p.inferenceProfileId)
	.filter((id) => /claude/i.test(id))
	.sort();

const client = new AnthropicBedrock({ awsRegion: REGION });
const ok = [], denied = [], other = [];
console.log(`${REGION}：實測 ${ids.length} 個 profile\n`);
for (const model of ids) {
	const t0 = Date.now();
	try {
		const r = await client.messages.create({
			model, max_tokens: 32, temperature: 0,
			messages: [{ role: "user", content: "回答一個字：好" }],
		});
		const ms = Date.now() - t0;
		ok.push({ model, ms, out: r.usage.output_tokens });
		console.log(`✔ ${String(ms).padStart(6)}ms  ${model}`);
	} catch (e) {
		const msg = String(e.message).replace(/\s+/g, " ");
		if (e.status === 403) { denied.push(model); console.log(`✗   403     ${model}`); }
		else { other.push({ model, msg: msg.slice(0, 90) }); console.log(`✗   ${e.status}     ${model}\n            ${msg.slice(0, 110)}`); }
	}
}
console.log(`\n${"═".repeat(58)}`);
console.log(`可用 ${ok.length} · 無權限 ${denied.length} · 其他錯誤 ${other.length}`);
if (ok.length) {
	console.log("\n可用模型（依延遲排序）：");
	for (const r of ok.sort((a, b) => a.ms - b.ms)) console.log(`  ${String(r.ms).padStart(6)}ms  ${r.model}`);
}
