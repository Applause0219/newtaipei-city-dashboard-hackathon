// 預熱快取：把 demo 需要的官方 API 回應全部抓下來存好。
// 用法：node local-demo/prewarm.mjs
// 之後就算完全斷網，儀表板也跑得起來。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const CACHE = path.join(DIR, "cache");
const UP = "https://citydashboard.taipei/api/v1";

const key = (u) => u.replace(/^\/api\//, "").replace(/[^A-Za-z0-9._-]/g, "_") + ".json";
let ok = 0, fail = 0;

async function grab(apiPath) {
	const url = "/api" + apiPath;
	try {
		const r = await fetch(UP + apiPath);
		if (!r.ok) throw new Error("HTTP " + r.status);
		const d = await r.json();
		fs.mkdirSync(CACHE, { recursive: true });
		fs.writeFileSync(path.join(CACHE, key(url)), JSON.stringify(d));
		ok++;
		return d;
	} catch (e) {
		console.warn("  ✗ " + apiPath + " → " + e.message);
		fail++;
		return null;
	}
}

console.log("預熱快取中…\n");

// 1. 儀表板清單（側邊欄靠這個）
console.log("[1/4] 儀表板清單");
const dash = await grab("/dashboard/");

// 2. 貢獻者（組件資訊視窗會用到）
console.log("[2/4] 貢獻者");
await grab("/contributor/");

// 3. 每個儀表板的組件設定
console.log("[3/4] 各儀表板的組件設定");
const groups = dash?.data || {};
const all = [];
for (const city of Object.keys(groups)) {
	for (const d of groups[city] || []) {
		if (!d?.index || d.index === "youth-newtaipei") continue;
		const r = await grab("/dashboard/" + d.index);
		if (r?.data) all.push(...r.data);
	}
}
console.log("    共 " + all.length + " 個組件設定");

// 4. 每個組件的圖表資料
console.log("[4/4] 各組件的圖表資料");
const seen = new Set();
for (const c of all) {
	if (!c?.id || !["static", "current", "demo"].includes(c.time_from)) continue;
	const p = "/component/" + c.id + "/chart?city=" + (c.city || "taipei");
	if (seen.has(p)) continue;
	seen.add(p);
	await grab(p);
}

const files = fs.existsSync(CACHE) ? fs.readdirSync(CACHE).length : 0;
let bytes = 0;
if (fs.existsSync(CACHE)) for (const f of fs.readdirSync(CACHE)) bytes += fs.statSync(path.join(CACHE, f)).size;
console.log("\n完成：" + ok + " 成功 / " + fail + " 失敗");
console.log("快取檔案 " + files + " 個，共 " + (bytes / 1048576).toFixed(1) + " MB");
console.log("\n現在可以斷網測試：OFFLINE=1 npx vite --port 5173 --host 127.0.0.1");
