// 盤點這組 AWS 憑證到底能用哪些模型。
//
// 不裝 aws-cli：bedrock-sdk 已相依 @smithy/signature-v4 與
// @aws-sdk/credential-providers，自己簽 SigV4 打控制平面即可。
// 比賽當天少裝一個東西，就少一個會出錯的地方。
import "./load-secrets.js";
import { SignatureV4 } from "@smithy/signature-v4";
import { Sha256 } from "@aws-crypto/sha256-js";
import { fromNodeProviderChain } from "@aws-sdk/credential-providers";

const REGION = process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-west-2";

// ⚠ query 一定要跟 path 分開傳。
// SignatureV4 是從 request.query 欄位算正規化查詢字串的；把 ?a=b 塞在 path 裡
// 它看不到，於是簽了「沒有參數」的版本，實際送出的 URL 卻帶參數，
// 結果是 SignatureDoesNotMatch——錯誤訊息會叫你去檢查 Secret Key，方向完全錯。
async function signedGet(service, host, path, query, region) {
	const signer = new SignatureV4({
		credentials: await fromNodeProviderChain()(),
		region, service, sha256: Sha256,
	});
	const signed = await signer.sign({
		method: "GET", protocol: "https:", hostname: host, path, query,
		headers: { host, accept: "application/json" },
	});
	const qs = new URLSearchParams(query).toString();
	const res = await fetch(`https://${host}${path}${qs ? "?" + qs : ""}`, { headers: signed.headers });
	return { ok: res.ok, status: res.status, body: await res.text() };
}

async function callBedrock(path, query, region = REGION) {
	const r = await signedGet("bedrock", `bedrock.${region}.amazonaws.com`, path, query, region);
	if (!r.ok) throw new Error(`${r.status} ${r.body.slice(0, 300)}`);
	return JSON.parse(r.body);
}

// 誰的憑證
async function whoami(region = REGION) {
	const r = await signedGet("sts", `sts.${region}.amazonaws.com`, "/",
		{ Action: "GetCallerIdentity", Version: "2011-06-15" }, region);
	return r.body.match(/<Arn>(.*?)<\/Arn>/)?.[1] || `(${r.status}) ${r.body.slice(0, 200)}`;
}

for (const region of (process.env.PROBE_REGIONS || "us-west-2,us-east-1").split(",")) {
	console.log(`\n${"═".repeat(60)}\n  ${region}\n${"═".repeat(60)}`);
	try {
		console.log("身分：", await whoami(region));
	} catch (e) { console.log("身分：查不到 —", String(e.message).slice(0, 200)); }

	// 1) 基礎模型：能不能用還要看 modelLifecycle 與是否需 inference profile
	try {
		const j = await callBedrock("/foundation-models", { byProvider: "anthropic" }, region);
		const ms = (j.modelSummaries || []).filter((m) => /claude/i.test(m.modelId));
		console.log(`\n基礎模型 ${ms.length} 個：`);
		for (const m of ms) {
			const onDemand = (m.inferenceTypesSupported || []).includes("ON_DEMAND");
			console.log(`  ${onDemand ? "✔ 直接可用" : "· 需 profile"}  ${m.modelId}`
				+ `  [${(m.inferenceTypesSupported || []).join("/")}]`
				+ `  ${m.modelLifecycle?.status || ""}`);
		}
	} catch (e) { console.log("基礎模型：失敗 —", String(e.message).slice(0, 300)); }

	// 2) 跨區推論設定檔：us.anthropic.* 這類 ID 從這裡來
	try {
		const j = await callBedrock("/inference-profiles", { maxResults: "200" }, region);
		const ps = (j.inferenceProfileSummaries || []).filter((p) => /claude/i.test(p.inferenceProfileId));
		console.log(`\n推論設定檔 ${ps.length} 個：`);
		for (const p of ps) console.log(`  ${p.status === "ACTIVE" ? "✔" : "·"} ${p.inferenceProfileId}`);
	} catch (e) { console.log("推論設定檔：失敗 —", String(e.message).slice(0, 300)); }
}
console.log("\n===PROBE-DONE===");
