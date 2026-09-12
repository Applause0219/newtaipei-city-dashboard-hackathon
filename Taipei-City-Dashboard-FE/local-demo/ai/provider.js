// 模型供應者。三條路徑，同一個介面，下游一行都不用改。
//
//   stub     關鍵字規劃器，離線可用，用來把流程跑通
//   gemini   開發期驗證 prompt 好不好用（比賽當天不可用）
//   bedrock  比賽當天唯一合規的路徑
//
// 切換方式：
//   AI_PROVIDER=bedrock npx vite --port 5173 --host 127.0.0.1
//
// bedrock 還需要 BEDROCK_MODEL。可用的模型 ID 由
//   node local-demo/ai/probe-bedrock.mjs
// 實際查出來，不要用猜的——猜錯時的錯誤訊息會指向錯誤的方向。

// 從 ai/secrets.env 讀金鑰（該檔名符合 .gitignore 的 *.env，不會進版控）。
// 這樣金鑰只存在你的硬碟，不會出現在對話紀錄、也不會被推上 GitHub。
// 9/12 放 AWS 憑證同樣用這個檔案。
import "./load-secrets.js";

const PROVIDER = process.env.AI_PROVIDER || "stub";

/**
 * 產生分析文字。
 * @param {string} prompt  完整的提問（已含資料）
 * @returns {Promise<{text: string, model: string}>}
 */
export async function generate(prompt) {
	if (PROVIDER === "bedrock") return generateBedrock(prompt);
	return generateStub(prompt);
}

// ── 假的：離線可用，用來把整條流程先跑通 ──
async function generateStub(prompt) {
	const { default: stub } = await import("./stub-answers.js");
	// 從 prompt 裡找出是問哪個組件
	const m = prompt.match(/組件代號：(\S+)/);
	const key = m ? m[1] : "default";
	await new Promise((r) => setTimeout(r, 700)); // 模擬模型思考時間
	return { text: stub[key] || stub.default, model: "stub (尚未接 Bedrock)" };
}

// ── 真的：Bedrock（比賽當天唯一合規的路徑）──
//
// 用 AnthropicBedrock 而不是 AnthropicBedrockMantle：
// Mantle 走的是 Claude Platform on AWS，需要 Marketplace 訂閱、端點也不同；
// 主辦發的臨時帳號是一般的 Amazon Bedrock。用錯會得到 404 或 AccessDenied。
//
// 憑證不在程式碼裡：AnthropicBedrock 預設走 AWS 憑證鏈，會讀
// AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN，
// 由 load-secrets.js 從 gitignore 過的 secrets.env 填進 process.env。
let _bedrock = null;
async function bedrockClient() {
	if (_bedrock) return _bedrock;
	const { AnthropicBedrock } = await import("@anthropic-ai/bedrock-sdk");
	// 主辦限定 us-west-2 / us-east-1，預設用前者（憑證頁面的預設值）
	_bedrock = new AnthropicBedrock({
		awsRegion: process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-west-2",
	});
	return _bedrock;
}

async function generateBedrock(prompt, { maxTokens = 8192 } = {}) {
	const client = await bedrockClient();
	// 沒有預設值：模型 ID 要由 probe-bedrock.mjs 實際查到的來填。
	// 猜一個寫死在這裡，失敗時錯誤訊息會指向模型不存在，
	// 而真正的問題是「沒人設定過」——當天沒時間釐清這種誤導。
	const model = process.env.BEDROCK_MODEL;
	if (!model) {
		throw new Error("未設定 BEDROCK_MODEL。先跑 `node local-demo/ai/probe-bedrock.mjs` 看有哪些可用，再寫進 secrets.env");
	}

	// 黑客松是共用帳號，限流幾乎一定會遇到。退避重試。
	let res, lastErr;
	for (let attempt = 0; attempt < 4; attempt++) {
		if (attempt) await new Promise((r) => setTimeout(r, 1000 * 2 ** (attempt - 1)));
		try {
			res = await client.messages.create({
				model,
				max_tokens: maxTokens,
				temperature: 0,          // 挑欄位這件事不需要創意
				messages: [{ role: "user", content: prompt }],
			});
			break;
		} catch (e) {
			lastErr = e;
			const s = e?.status;
			// 429/503 是暫時的；400/403 重試幾次都一樣，直接拋並講清楚原因
			if (s !== 429 && s !== 503) {
				if (s === 403 || /AccessDenied/i.test(e?.message || "")) {
					throw new Error(`Bedrock 拒絕存取 ${model}：這個帳號可能沒開通此模型，或 region 不對（目前 ${client.awsRegion}）。原文：${e.message}`);
				}
				if (s === 400 && /ValidationException|on-demand/i.test(e?.message || "")) {
					throw new Error(`Bedrock 不接受模型 ID ${model}：多數新模型必須用跨區推論設定檔（us.anthropic.* 開頭）。原文：${e.message}`);
				}
				throw e;
			}
			console.warn(`[bedrock] ${s}，第 ${attempt + 1} 次重試`);
		}
	}
	if (!res) throw lastErr;

	const text = res.content
		.filter((b) => b.type === "text")
		.map((b) => b.text)
		.join("\n")
		.trim();

	// 把 stop_reason 對應成 generateSpec 認得的 finishReason。
	// 少了這個對應，被截斷的輸出會被報成「模型沒有回傳可解析的 JSON」——
	// 跟 Gemini 那條路徑當初踩過的坑一模一樣，成因與解法卻完全不同。
	const finishReason = res.stop_reason === "max_tokens" ? "MAX_TOKENS"
		: res.stop_reason === "end_turn" ? "STOP"
			: res.stop_reason;

	return { text, model: `bedrock/${model}`, finishReason, usage: res.usage };
}

// ═══ ComponentSpec 產生 ═══
//
// 與 generate() 分開，因為要求不同：這裡必須拿到可解析的 JSON，
// 拿不到就是失敗，不能像文字摘要那樣「差不多就好」。

/**
 * @returns {Promise<{spec?:object, refuse?:string, model:string, raw:string}>}
 */
export async function generateSpec(prompt) {
	const { text, model, finishReason } =
		PROVIDER === "bedrock" ? await generateBedrock(prompt) :
			PROVIDER === "gemini"  ? await generateGemini(prompt) :
				await stubSpec(prompt);

	// 模型有時仍會包在 ```json 裡，容忍但不鼓勵
	const cleaned = text.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim();
	try {
		const obj = JSON.parse(cleaned);
		if (obj.refuse) return { refuse: obj.refuse, model, raw: text };
		return { spec: obj, model, raw: text };
	} catch {
		const hint = finishReason === "MAX_TOKENS"
			? "輸出被截斷（finishReason=MAX_TOKENS），請調高 maxOutputTokens"
			: finishReason && finishReason !== "STOP"
				? `模型提前結束：finishReason=${finishReason}`
				: "模型沒有回傳可解析的 JSON";
		return { refuse: hint, model, raw: text.slice(0, 400) };
	}
}

// ── Gemini：比賽前用來驗證 prompt 寫得夠不夠好 ──
//
// 比賽當天只能用 Bedrock（規則明訂）。這條路徑純粹是開發期驗證用——
// stub 是我寫的關鍵字規則，它「答對」不代表 prompt 對真模型也有效。
//
// 走 REST 不裝 SDK，少一個相依。
//
// ⚠ 刻意「不」給嚴格的 responseSchema。
// AimchartAI 的 CLAUDE.md 記了一個真實教訓：strict schema 會讓拒答路徑
// 物理上不可能發生——模型被迫填滿每個欄位，於是「我做不到」這個正確答案
// 說不出口，392 個測試全綠也看不出來。這裡只要求輸出是 JSON，
// 讓 {"refuse": "..."} 仍然是合法回應。
async function generateGemini(prompt) {
	const key = process.env.GEMINI_API_KEY;
	if (!key) throw new Error("未設定 GEMINI_API_KEY");
	const model = process.env.GEMINI_MODEL || "gemini-3.6-flash";
	const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`;

	// 503（負載過高）與 429（限流）是暫時性的，退避後重試。
	// 實測 Gemini 會間歇回 503；demo 現場失敗一次就很難看，9/12 的 Bedrock
	// 同樣可能限流，所以這段邏輯留著，換 provider 也用得上。
	const body = JSON.stringify({
		contents: [{ role: "user", parts: [{ text: prompt }] }],
		generationConfig: {
			responseMimeType: "application/json",
			temperature: 0,          // 挑欄位這件事不需要創意
			// 思考 token 會吃掉這個額度（實測 thoughtsTokenCount 破千），
			// 給太少會讓 JSON 從中間被切斷，錯誤看起來卻像「模型不聽話」
			maxOutputTokens: 8192,
		},
	});

	let res, lastErr = "";
	for (let attempt = 0; attempt < 4; attempt++) {
		if (attempt) await new Promise((r) => setTimeout(r, 800 * 2 ** (attempt - 1)));
		res = await fetch(url, {
			method: "POST",
			headers: { "content-type": "application/json", "x-goog-api-key": key },
			body,
		});
		if (res.ok) break;
		lastErr = (await res.text()).slice(0, 200);
		if (res.status !== 503 && res.status !== 429) break;   // 其他錯誤重試也沒用
		console.warn(`[gemini] ${res.status}，第 ${attempt + 1} 次重試`);
	}

	if (!res.ok) throw new Error(`Gemini ${res.status}: ${lastErr}`);
	const j = await res.json();
	const cand = j.candidates?.[0];
	const text = (cand?.content?.parts || []).map((p) => p.text || "").join("").trim();

	// 把 finishReason 帶出去。截斷與「模型不聽話」看起來一樣，但成因完全不同：
	// MAX_TOKENS 要加額度，SAFETY 要改問法，STOP 才是真的格式問題。
	const why = cand?.finishReason;
	if (!text) {
		throw new Error(`Gemini 回傳空內容（finishReason=${why}）：` + JSON.stringify(j).slice(0, 200));
	}
	return { text, model: `gemini/${model}`, finishReason: why };
}

// ── 假的 spec 產生器：關鍵字比對的小型規劃器 ──
//
// 刻意不是「回傳一份寫死的 spec」——那樣測不出下游的驗證、編譯、對齊檢查
// 到底有沒有在運作。這裡真的依問題挑表挑欄位，只是用關鍵字而不是模型。
// 9/12 換成 Bedrock 後，下游一行都不用改。
async function stubSpec(prompt) {
	const q = (prompt.split("## 使用者的問題")[1] || prompt).trim();
	await new Promise((r) => setTimeout(r, 500));

	// 先擋掉目錄涵蓋不到的指標。
	//
	// 這一關是後來補的：原本只比對年齡族群，於是「青年的收入中位數」會比中
	// 「青年」而回傳人口組件——一個完全有效、但回答了另一個問題的 spec。
	// 下游的驗證、編譯、對齊檢查全都攔不到這種錯，因為 spec 本身沒有問題。
	const 目錄外 = /收入|所得|薪資|租金|補貼|房價|就業|失業|創業|貸款|學歷|教育程度|通勤|交通|犯罪|醫療/;
	if (目錄外.test(q)) {
		const 詞 = q.match(目錄外)[0];
		return { text: JSON.stringify({
			refuse: `資料目錄裡沒有「${詞}」相關的資料。目前只涵蓋各行政區的年齡人口分配（5 歲組距）與其衍生指標（扶養比、老化指數）。`,
		}), model: "stub (關鍵字規劃器，尚未接 Bedrock)" };
	}

	const 台北 = /台北市|臺北市|北市/.test(q) && !/新北/.test(q);
	const table = 台北 ? "city_age_distribution_taipei" : "city_age_distribution_newtaipei";
	const 合計列 = 台北 ? "總計" : "新北市";
	const city = 台北 ? "taipei" : "metrotaipei";

	// 年齡 → 欄位。
	//
	// 原本是硬寫關鍵字（/青年|老年|幼年/），結果問「30-34 歲人口」就對不上。
	// 改成從問題裡把年齡數字抓出來，落在哪幾個 5 歲組距就用哪幾欄——
	// 這才是資料本身的結構，不是我猜使用者會怎麼措辭。
	// percent3 = 0-4 歲，之後每欄加 5 歲，percent23 = 100 歲以上（已驗算）。
	const bandCol = (age) => "percent" + (3 + Math.min(Math.floor(age / 5), 20));
	const bandLabel = (age) => {
		const lo = Math.floor(age / 5) * 5;
		return lo >= 100 ? "100歲以上人口數" : `${lo}_${lo + 4}歲人口數`;
	};

	let cols = [];
	let name = null;

	// 「歲」後面接「以上／以下」會改變範圍——漏掉會嚴重少算：
	// 問「65 歲以上人口」若只回 65-69，等於漏掉 70 歲以上的所有組距。
	const nums = [...q.matchAll(/(\d{1,3})\s*(?:[-~到至]\s*(\d{1,3}))?\s*歲(以上|以下|含以上)?/g)];
	if (nums.length) {
		const ages = new Set();
		for (const m of nums) {
			let a = +m[1], b = m[2] ? +m[2] : a;
			if (/以上/.test(m[3] || "")) b = 100;
			if (/以下/.test(m[3] || "")) { b = a; a = 0; }
			for (let x = Math.min(a, b); x <= Math.max(a, b) && x <= 100; x += 5) ages.add(x);
			ages.add(Math.min(Math.max(a, b), 100));
		}
		const seen = new Set();
		for (const a of [...ages].sort((x, y) => x - y)) {
			const c = bandCol(a);
			if (!seen.has(c)) { seen.add(c); cols.push([bandLabel(a), c]); }
		}
		if (cols.length > 8) cols = cols.slice(0, 8);   // 超過配色能區分的範圍
		// 名稱要用「組距的邊界」而不是使用者打的數字：
		// 問 30-34 時 lo=30、hi=34 同屬一個組距，命名成 30-38 就錯了。
		const lo = Math.floor(Math.min(...ages) / 5) * 5;
		const hiBand = Math.floor(Math.max(...ages) / 5) * 5;
		name = hiBand >= 100 ? `${lo}歲以上人口分區` : `${lo}-${hiBand + 4}歲人口分區`;
	} else {
		const words = [
			{ re: /青年|年輕/, ages: [20, 25, 30], name: "青年人口分區" },
			{ re: /老年|高齡|長者/, ages: [65, 70, 75], name: "老年人口分區" },
			{ re: /幼年|兒童|小孩/, ages: [0, 5, 10], name: "幼年人口分區" },
		];
		const w = words.find((x) => x.re.test(q));
		if (w) { cols = w.ages.map((a) => [bandLabel(a), bandCol(a)]); name = w.name; }
	}
	const hit = cols.length ? { cols, name, unit: "仟人" } : null;

	// 比例類：老化指數與扶養比是 ratio，不可縮放
	if (/老化指數|扶養比|撫養比/.test(q)) {
		return { text: JSON.stringify({
			index: "aging_index_by_district", name: "老化指數分區", city, table,
			query_type: "three_d",
			x: { column: "區域別", exclude: [合計列] },
			series: [{ label: "老化指數", column: "percent33" }],
			filters: [{ column: "統計類型", eq: "計" }],
			latest_by: "年份",
			chart: { types: ["DistrictChart", "ColumnChart"], unit: "" },
			short_desc: "各行政區的老化指數",
			long_desc: "老化指數 = 65 歲以上人口 ÷ 0-14 歲人口 × 100。數值越高代表高齡化越明顯。",
		}), model: "stub (關鍵字規劃器，尚未接 Bedrock)" };
	}

	if (!hit) {
		return { text: JSON.stringify({
			refuse: "目前的資料目錄只涵蓋各行政區的年齡人口分配與其衍生指標，無法回答這個問題。",
		}), model: "stub (關鍵字規劃器，尚未接 Bedrock)" };
	}

	return { text: JSON.stringify({
		index: hit.name === "青年人口分區" ? "youth_population_by_district" : "population_band_by_district",
		name: hit.name, city, table, query_type: "three_d",
		x: { column: "區域別", exclude: [合計列] },
		series: hit.cols.map(([label, column]) => ({ label, column })),
		filters: [{ column: "統計類型", eq: "計" }],
		latest_by: "年份",
		transform: { divide: 1000 },
		chart: { types: ["DistrictChart", "ColumnChart"], unit: hit.unit },
		short_desc: `各行政區的${hit.name.replace("分區","")}分布`,
		long_desc: hit.name === "青年人口分區"
			? "受限於公開統計採 5 歲組距，《青年基本法》定義的 18-35 歲無法直接切出，本組件以 20-24、25-29、30-34 三個完整組距近似，未涵蓋 18-19 歲與 35 歲。"
			: `依 5 歲組距呈現各行政區的${hit.name.replace("分區","")}分布。`,
	}), model: "stub (關鍵字規劃器，尚未接 Bedrock)" };
}

export const providerName = PROVIDER;
