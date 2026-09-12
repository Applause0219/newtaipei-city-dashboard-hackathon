// 本機 demo 用：把自訂組件注入前端，並對官方 API 做離線快取。
//
// 三件事：
//   1. 自建組件：攔截我們自己的儀表板與組件資料，讀 local-demo/*.json
//   2. 離線快取：其他官方 API 一律「先讀快取，沒有才連網」，連網成功就存起來
//   3. 純離線：設環境變數 OFFLINE=1 則完全不連網，只讀快取
//
// 所以：先在有網路時操作一遍，之後斷網也能跑。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { generate, providerName } from "./ai/provider.js";
import { buildPrompt } from "./ai/prompt.js";
import { generateFromQuestion } from "./ai/generate.js";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const CACHE = path.join(DIR, "cache");
const UPSTREAM = "https://citydashboard.taipei/api/v1";
const DASH_INDEX = "youth-newtaipei";
const OFFLINE = process.env.OFFLINE === "1";

const read = (f) => JSON.parse(fs.readFileSync(path.join(DIR, f), "utf-8"));

/** 原封不動讀出 body，用於轉發給上游 */
function rawBody(req) {
	return new Promise((resolve) => {
		let raw = "";
		req.on("data", (c) => { raw += c; if (raw.length > 1e6) req.destroy(); });
		req.on("end", () => resolve(raw));
		req.on("error", () => resolve(""));
	});
}

/** 讀 POST body。支援 JSON 與 form-urlencoded（官方 chatbot 用後者）。 */
function readBody(req) {
	return new Promise((resolve) => {
		let raw = "";
		req.on("data", (c) => { raw += c; if (raw.length > 1e6) req.destroy(); });
		req.on("end", () => {
			try {
				if ((req.headers["content-type"] || "").includes("json")) return resolve(JSON.parse(raw || "{}"));
				resolve(Object.fromEntries(new URLSearchParams(raw)));
			} catch { resolve({}); }
		});
		req.on("error", () => resolve({}));
	});
}

// 我們自己的儀表板長什麼樣
const myDashboard = () => ({
	index: DASH_INDEX,
	name: "青年儀表板",
	components: [9001],
	icon: "diversity_3",
	updated_at: new Date().toISOString(),
});

// 網址 → 檔名（把 / ? & = 換成安全字元）
function cacheKey(url) {
	return url.replace(/^\/api\//, "").replace(/[^A-Za-z0-9._-]/g, "_") + ".json";
}

function readCache(url) {
	try {
		return JSON.parse(fs.readFileSync(path.join(CACHE, cacheKey(url)), "utf-8"));
	} catch {
		return null;
	}
}

function writeCache(url, data) {
	try {
		fs.mkdirSync(CACHE, { recursive: true });
		fs.writeFileSync(path.join(CACHE, cacheKey(url)), JSON.stringify(data), "utf-8");
	} catch (e) {
		console.warn("[inject] 快取寫入失敗", e.message);
	}
}

// 把我們的儀表板插進官方清單
function addMine(payload) {
	if (!payload || typeof payload !== "object") return payload;
	payload.data = payload.data || {};
	const list = payload.data.metrotaipei || [];
	if (!list.some((d) => d && d.index === DASH_INDEX)) {
		payload.data.metrotaipei = [myDashboard(), ...list];
	}
	return payload;
}

export function injectLocalComponents() {
	return {
		name: "inject-local-components",
		configureServer(server) {
			server.middlewares.use(async (req, res, next) => {
				const url = req.url || "";
				if (!url.startsWith("/api/")) return next();

				const send = (obj, note) => {
					res.setHeader("Content-Type", "application/json; charset=utf-8");
					if (note) res.setHeader("X-Inject-Source", note);
					res.end(JSON.stringify(obj));
				};

				try {
					// ── 0a. 自動生成組件：chatbot 找不到現成組件時走這條 ──
					//
					// 模型只產出 ComponentSpec（挑表挑欄位），SQL 由程式編譯，
					// 數值由 PostgreSQL 算。詳見 ai/component-spec.js 的說明。
					if (url.startsWith("/api/component/generate") && req.method === "POST") {
						const body = await readBody(req);
						const question = (body.question || "").trim();
						if (!question) {
							res.statusCode = 400;
							return send({ status: "error", message: "缺少 question" }, "generate");
						}
						console.log("[generate] " + question);
						const r = await generateFromQuestion(question);
						console.log("[generate] → " + (r.ok ? `${r.stats?.categories ?? "?"} 項` : `${r.stage}: ${r.errors?.[0]}`));
						return send({ data: r, status: r.ok ? "success" : "error" }, "generate:" + (r.stage || "ok"));
					}

					// ── 0b. Insight Pipeline：不等使用者問，主動找出值得注意的現象 ──
					//
					// 與 0a 的差別是「誰決定要看什麼」：
					//   0a  使用者問一個問題 → 產一個組件回答它
					//   0b  使用者只給方向   → 系統自己掃描、驗證、排序，回報前幾名
					//
					// 整條路徑上 LLM 一個數字都沒碰：統計由 ai/mining.js 決定性地算，
					// 年齡是否可比較由 ai/age.js 判斷，關卡與評分由 ai/insight.js 執行。
					// 模型目前完全沒有參與——之後若要加，也只能潤飾文字，不能改數值。
					//
					// 回傳 artifacts 與 markdown 兩份，但它們來自**同一批 InsightArtifact**，
					// 所以對話框寫的數字和圖表畫的數字不可能不一致。
					if (url.startsWith("/api/insight")) {
						const q = new URL(url, "http://x").searchParams;
						const topN = Math.min(20, Math.max(1, Number(q.get("top")) || 5));
						const t0 = Date.now();
						try {
							const { runInsightPipeline } = await import("./ai/pipeline.js");
							const r = await runInsightPipeline({
								table: q.get("table") || undefined,
								geography: q.get("geo") || "新北市",
								target: q.get("age") || null,
								topN,
							});
							console.log(`[insight] ${r.funnel.candidates} 候選 → ${r.funnel.validated} 通過 → ${r.artifacts.length} 推薦 (${Date.now() - t0}ms)`);
							return send({
								data: {
									funnel: r.funnel,
									age_scope: { label: r.ageScope.label, classification: r.ageScope.classification },
									artifacts: r.artifacts,
									markdown: r.markdown,
									errors: r.errors,
								},
								status: "success",
							}, "insight:ok");
						} catch (err) {
							// 資料庫沒開是最常見的原因，訊息要講得出下一步該做什麼，
							// 不要只丟一個 stack trace 給現場的人猜。
							console.log("[insight] 失敗: " + (err.message || err));
							res.statusCode = 500;
							return send({
								status: "error",
								message: String(err.message || err),
								hint: "需要本機 PostgreSQL（dashboard 資料庫）。確認 `pg_isready` 有回應，且已套用 db/01_data_table.sql。",
							}, "insight:error");
						}
					}

					// ── 0. AI 洞察：用官方現成的 ✦ 按鈕與彈窗，前端不用改 ──
					if (url.startsWith("/api/component/ai-summary")) {
						const q = new URL(url, "http://x").searchParams;
						const idx = q.get("index");
						if (idx === "youth_population_district") {
							const cfg = read("component_9001.json");
							const chart = read("chart_9001.json");
							const prompt = buildPrompt(cfg, chart);
							const { text, model } = await generate(prompt);
							console.log("[ai] " + idx + " ← " + model);
							return send({
								data: {
									index: idx,
									city: q.get("city") || "metrotaipei",
									type: q.get("type") || "chart",
									result: text,
									model,
									updated_at: new Date().toISOString(),
								},
								status: "success",
							}, "ai:" + providerName);
						}
					}

					// ── 1. 我們自建的東西，永遠讀本地檔 ──
					if (url.startsWith("/api/component/9001/chart")) {
						return send(read("chart_9001.json"), "local");
					}
					if (url.startsWith(`/api/dashboard/${DASH_INDEX}`)) {
						return send({ data: [read("component_9001.json")], status: "success" }, "local");
					}

					// ── 2. 官方 API：先讀快取（只對 GET，POST 帶 body 不能用網址當 key）──
					const hit = req.method === "GET" ? readCache(url) : null;
					if (hit) {
						return send(url.startsWith("/api/dashboard/") && !url.slice(15) ? addMine(hit) : hit, "cache");
					}

					// ── 3. 快取沒有，且是離線模式 ──
					if (OFFLINE) {
						console.warn("[inject] 離線模式但快取沒有：" + url);
						return send({ status: "error", message: "offline, not cached: " + url }, "offline-miss");
					}

					// ── 4. 連網抓，成功就存起來 ──
					//
					// 非 GET 一定要把 method、body、content-type 一起帶過去。
					// 先前只做 GET，於是 chatbot 的 POST /vector/component 被轉成 GET，
					// 回來的東西沒有 data 欄位，官方前端在 chatStore.js:110 直接炸掉。
					const init = { method: req.method };
					if (req.method !== "GET" && req.method !== "HEAD") {
						init.body = await rawBody(req);
						const ct = req.headers["content-type"];
						if (ct) init.headers = { "content-type": ct };
					}
					const r = await fetch(UPSTREAM + url.replace(/^\/api/, ""), init);
					if (!r.ok) return next();
					const data = await r.json();
					if (req.method === "GET") writeCache(url, data);
					return send(url === "/api/dashboard/" || url === "/api/dashboard" ? addMine(data) : data, "network");
				} catch (err) {
					// 連網失敗，最後再試一次快取
					const fallback = readCache(url);
					if (fallback) return send(fallback, "cache-fallback");
					console.error("[inject] " + url + " → " + err.message);
					res.statusCode = 502;
					return res.end(JSON.stringify({ status: "error", message: String(err.message) }));
				}
			});
		},
	};
}
