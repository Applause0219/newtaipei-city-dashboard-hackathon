// 這個帳號除了 Anthropic 以外還有哪些供應商的模型。
import "./load-secrets.js";
import { SignatureV4 } from "@smithy/signature-v4";
import { Sha256 } from "@aws-crypto/sha256-js";
import { fromNodeProviderChain } from "@aws-sdk/credential-providers";

async function get(path, query, region) {
	const host = `bedrock.${region}.amazonaws.com`;
	const signer = new SignatureV4({ credentials: await fromNodeProviderChain()(), region, service: "bedrock", sha256: Sha256 });
	const signed = await signer.sign({ method: "GET", protocol: "https:", hostname: host, path, query, headers: { host, accept: "application/json" } });
	const qs = new URLSearchParams(query || {}).toString();
	const r = await fetch(`https://${host}${path}${qs ? "?" + qs : ""}`, { headers: signed.headers });
	const b = await r.text();
	if (!r.ok) throw new Error(`${r.status} ${b.slice(0, 200)}`);
	return JSON.parse(b);
}

for (const region of ["us-west-2", "us-east-1"]) {
	console.log(`\n${"═".repeat(56)}\n  ${region}\n${"═".repeat(56)}`);
	const j = await get("/foundation-models", null, region);
	const byProv = {};
	for (const m of j.modelSummaries || []) {
		(byProv[m.providerName] ||= []).push(m);
	}
	for (const [prov, ms] of Object.entries(byProv).sort()) {
		console.log(`\n【${prov}】${ms.length} 個`);
		for (const m of ms) {
			const t = (m.inferenceTypesSupported || []).join("/");
			console.log(`   ${m.modelId}  [${t}]  ${m.modelLifecycle?.status || ""}`);
		}
	}
}
