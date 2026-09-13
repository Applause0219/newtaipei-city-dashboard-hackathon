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
// generateFromQuestion import removed — /api/component/generate now proxies to the Python agent service

const DIR = path.dirname(fileURLToPath(import.meta.url));
const CACHE = path.join(DIR, "cache");
const UPSTREAM = "https://citydashboard.taipei/api/v1";
const DASH_INDEX = "youth-newtaipei";
const OFFLINE = process.env.OFFLINE === "1";

// ── Python agent service (AI analysis & component publishing) ──
// In Docker Compose the service name is "dashboard-agent"; for local dev
// override with AGENT_HOST=localhost:8090 (the default).
const IS_DOCKER_COMPOSE = process.env.DOCKER_COMPOSE === "true";
const AGENT_HOST = process.env.AGENT_HOST || (IS_DOCKER_COMPOSE ? "dashboard-agent:8090" : "localhost:8090");
const AGENT_URL = `http://${AGENT_HOST}`;

// ── Youth component dynamic serving ──
const YOUTH_COLORS = ["#5a9cf8","#4FB3C4","#56B96D","#A8C95F","#F8CF58","#E8845C","#D95B6B","#9B59B6"];
const youthComponents = new Map();   // component id → config object
const youthDashboards = new Set();   // dashboard indexes with youth components

function buildYouthConfig(id, assetId, title) {
	return {
		id, index: assetId, name: title,
		chart_config: { index: assetId, color: YOUTH_COLORS, types: ["ColumnChart"], unit: "" },
		history_config: null, map_config: [null], map_filter: null,
		time_from: "static", time_to: null,
		update_freq: 1, update_freq_unit: "year",
		source: "新北市政府", short_desc: title, long_desc: "",
		query_type: "three_d", city: "metrotaipei",
	};
}

/** Thin proxy: forward a request to the Python agent and return the parsed JSON. */
async function proxyToAgent(endpoint, method, body) {
	const url = `${AGENT_URL}${endpoint}`;
	const init = { method };
	if (body) {
		init.headers = { "content-type": "application/json" };
		init.body = JSON.stringify(body);
	}
	const r = await fetch(url, init);
	if (!r.ok) {
		const text = await r.text().catch(() => "");
		throw new Error(`Agent responded ${r.status}: ${text.slice(0, 300)}`);
	}
	return r.json();
}

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

function createApiMiddleware() {
	return async (req, res, next) => {
				const url = req.url || "";
				if (!url.startsWith("/api/")) return next();

				// In Docker Compose mode, axios sends /api/dev/... — strip the /dev/ prefix
				// so all route checks below work identically to local-demo mode.
				const normUrl = IS_DOCKER_COMPOSE
					? url.replace(/^\/api\/dev\//, "/api/")
					: url;

				const send = (obj, note) => {
					res.setHeader("Content-Type", "application/json; charset=utf-8");
					if (note) res.setHeader("X-Inject-Source", note);
					res.end(JSON.stringify(obj));
				};

				try {
					// ── 0. Chatbot vector search → proxy to agent search ──
					if (normUrl.startsWith("/api/vector/component") && req.method === "POST") {
						const body = await readBody(req);
						const query = (body.query || "").trim();
						if (query) {
							console.log("[vector→agent] " + query);
							try {
								const r = await proxyToAgent("/api/v1/agent/search", "POST", {
									query,
									limit: parseInt(body.limit) || 10,
								});
								if (r.data) {
									for (const item of r.data) {
										youthComponents.set(item.id, buildYouthConfig(item.id, item.index, item.name));
									}
								}
								console.log("[vector→agent] cached", youthComponents.size, "youth components");
								return send(r, "vector:agent");
							} catch (err) {
								console.error("[vector→agent] " + err.message);
								if (IS_DOCKER_COMPOSE) return next();
							}
						} else if (IS_DOCKER_COMPOSE) {
							return next();
						}
					}

					// ── 0-stream. Agent SSE stream → proxy to agent /stream ──
					if (normUrl.startsWith("/api/agent/stream") && req.method === "GET") {
						const q = new URL(url, "http://x").searchParams;
						const question = q.get("question") || "";
						const maxInsights = parseInt(q.get("max_insights")) || 3;
						console.log("[stream→agent] " + question + " (max_insights=" + maxInsights + ")");
						const streamUrl = `${AGENT_URL}/api/v1/agent/stream?question=${encodeURIComponent(question)}&max_insights=${maxInsights}`;
						try {
							const upstream = await fetch(streamUrl);
							res.writeHead(200, {
								"Content-Type": "text/event-stream",
								"Cache-Control": "no-cache",
								"Connection": "keep-alive",
								"X-Inject-Source": "stream:agent",
							});
							for await (const chunk of upstream.body) {
								res.write(chunk);
							}
							res.end();
							return;
						} catch (err) {
							console.error("[stream→agent] " + err.message);
							res.writeHead(502, { "Content-Type": "text/event-stream" });
							res.end(`data: ${JSON.stringify({type:"error",detail:err.message})}\n\n`);
							return;
						}
					}

					// ── 0a. 自動生成組件 → proxy to Python agent service ──
					if (normUrl.startsWith("/api/component/generate") && req.method === "POST") {
						const body = await readBody(req);
						const question = (body.question || "").trim();
						if (!question) {
							res.statusCode = 400;
							return send({ status: "error", message: "缺少 question" }, "generate");
						}
						console.log("[generate→agent] " + question);
						try {
							const r = await proxyToAgent("/api/v1/agent/analyze", "POST", { question });
							return send({ data: r, status: "success" }, "generate:agent");
						} catch (err) {
							console.error("[generate→agent] " + err.message);
							res.statusCode = 502;
							return send({
								status: "error",
								message: "Agent 服務無回應: " + err.message,
								hint: `確認 ${AGENT_URL} 是否在運行 (docker compose up dashboard-agent)`,
							}, "generate:agent-error");
						}
					}

					// ── 0b. Insight Pipeline → proxy to Python agent service ──
					//
					// 與 0a 的差別是「誰決定要看什麼」：
					//   0a  使用者問一個問題 → 產一個組件回答它
					//   0b  使用者只給方向   → 系統自己掃描、驗證、排序，回報前幾名
					if (normUrl.startsWith("/api/insight")) {
						const q = new URL(url, "http://x").searchParams;
						const table = q.get("table") || "";
						const topN = Math.min(20, Math.max(1, Number(q.get("top")) || 5));
						const t0 = Date.now();
						try {
							const r = await proxyToAgent("/api/v1/agent/analyze", "POST", {
								question: table ? `分析 ${table}` : "總覽分析",
								domains: [],
								max_insights: topN,
							});
							console.log(`[insight→agent] ${topN} insights requested (${Date.now() - t0}ms)`);
							return send({ data: r, status: "success" }, "insight:agent");
						} catch (err) {
							console.error("[insight→agent] " + (err.message || err));
							res.statusCode = 502;
							return send({
								status: "error",
								message: "Agent 服務無回應: " + String(err.message || err),
								hint: `確認 ${AGENT_URL} 是否在運行 (docker compose up dashboard-agent)`,
							}, "insight:agent-error");
						}
					}

					// ── 0. AI 洞察：用官方現成的 ✦ 按鈕與彈窗，前端不用改 ──
					if (normUrl.startsWith("/api/component/ai-summary")) {
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
					if (normUrl.startsWith("/api/component/9001/chart")) {
						return send(read("chart_9001.json"), "local");
					}
					if (normUrl.startsWith(`/api/dashboard/${DASH_INDEX}`)) {
						return send({ data: [read("component_9001.json")], status: "success" }, "local");
					}

					// ── 1b. Track youth dashboard creation ──
					if (req.method === "POST" && normUrl.startsWith("/api/dashboard")) {
						console.log("[dashboard:debug] POST", normUrl, "docker:", IS_DOCKER_COMPOSE);
					}
					if (normUrl === "/api/dashboard/" && req.method === "POST" && IS_DOCKER_COMPOSE) {
						const body = await readBody(req);
						const ids = (body.components || []).map(c => typeof c === "object" ? c.id : c);
						const hasYouth = ids.some(id => id >= 90000);
						const beUrl = "http://dashboard-be:8080/api/v1/dashboard/";
						const fwdH = { "content-type": "application/json" };
						for (const h of ["authorization", "cookie"]) if (req.headers[h]) fwdH[h] = req.headers[h];
						const r = await fetch(beUrl, { method: "POST", headers: fwdH, body: JSON.stringify(body) });
						const data = await r.json();
						if (hasYouth && data.data?.index) youthDashboards.add(data.data.index);
						console.log("[dashboard:create]", data.data?.index, "ids:", ids.length);
						return send(data, hasYouth ? "dashboard:create-youth" : "dashboard:create");
					}

					// ── 1c. Serve youth dashboard components ──
					const dashMatch = normUrl.match(/^\/api\/dashboard\/([\w-]+)/);
					if (dashMatch && req.method === "GET" && youthDashboards.has(dashMatch[1])) {
						let beComps = [];
						if (IS_DOCKER_COMPOSE) {
							try {
								const beUrl = `http://dashboard-be:8080/api/v1/dashboard/${dashMatch[1]}`;
								const fwdH = {};
								for (const h of ["authorization", "cookie"]) if (req.headers[h]) fwdH[h] = req.headers[h];
								const r = await fetch(beUrl, { headers: fwdH });
								const d = await r.json();
								beComps = d.data || [];
							} catch {}
						}
						const beIds = new Set(beComps.map(c => c.id));
						const youthConfigs = Array.from(youthComponents.values()).filter(c => !beIds.has(c.id));
						console.log("[dashboard:youth]", dashMatch[1], "be:", beComps.length, "youth:", youthConfigs.length);
						return send({ data: [...beComps, ...youthConfigs], status: "success" }, "dashboard:youth");
					}

					// ── 1d. Serve youth component chart data ──
					const chartMatch = normUrl.match(/^\/api\/component\/(\d+)\/chart/);
					if (chartMatch && req.method === "GET") {
						const cid = parseInt(chartMatch[1]);
						if (youthComponents.has(cid)) {
							const comp = youthComponents.get(cid);
							console.log("[chart:youth]", cid, comp.index);
							try {
								const r = await proxyToAgent(`/api/v1/agent/component/${comp.index}/chart`, "GET");
								return send(r, "chart:youth");
							} catch (err) {
								console.error("[chart:youth] " + err.message);
								return send({ data: [], status: "success" }, "chart:youth-empty");
							}
						}
					}

					// ── Docker Compose mode: pass non-intercepted routes to Vite proxy → Go BE ──
					if (IS_DOCKER_COMPOSE) return next();

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
					if (IS_DOCKER_COMPOSE) return next();
					// 連網失敗，最後再試一次快取
					const fallback = readCache(url);
					if (fallback) return send(fallback, "cache-fallback");
					console.error("[inject] " + url + " → " + err.message);
					res.statusCode = 502;
					return res.end(JSON.stringify({ status: "error", message: String(err.message) }));
				}
			};
}

export function injectLocalComponents() {
	const middleware = createApiMiddleware();
	return {
		name: "inject-local-components",
		configurePreviewServer(server) {
			server.middlewares.use(middleware);
		},
		configureServer(server) {
			server.middlewares.use(middleware);
		},
	};
}
