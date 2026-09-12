// 從 ai/secrets.env 載入金鑰到 process.env。
//
// 檔名符合 .gitignore 的 *.env（見 .gitignore:75），不會進版控——
// 金鑰只存在硬碟，不會出現在對話紀錄，也不會被推上 GitHub。
// 這是主辦單位參賽規則明訂的要求。
//
// 抽成獨立模組是因為 provider.js 與 probe-bedrock.mjs 都要用；
// 原本寫在 provider.js 裡，probe 就讀不到。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

let done = false;

export function loadSecrets() {
	if (done) return;
	done = true;
	try {
		// 一定要用 fileURLToPath：專案路徑含中文，new URL().pathname 會做
		// 百分比編碼，fs 就找不到檔案了（這個坑之前踩過一次）
		const f = path.join(path.dirname(fileURLToPath(import.meta.url)), "secrets.env");
		if (!fs.existsSync(f)) return;
		for (const line of fs.readFileSync(f, "utf8").split("\n")) {
			// 容忍 `export KEY=value`——主辦給的憑證就是這個格式，
			// 直接複製貼上不用手改，少一個出錯機會
			const m = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
			if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
		}
		// SDK 認 AWS_REGION，主辦給的是 AWS_DEFAULT_REGION，補一個別名
		if (!process.env.AWS_REGION && process.env.AWS_DEFAULT_REGION) {
			process.env.AWS_REGION = process.env.AWS_DEFAULT_REGION;
		}
	} catch { /* 沒有就算了，stub 不需要金鑰 */ }
}

loadSecrets();
