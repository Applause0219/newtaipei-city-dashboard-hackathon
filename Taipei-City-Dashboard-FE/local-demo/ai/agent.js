// Agent 迴圈（藍圖 §6）。
//
// 與 generate.js 那條決定性管線**並存**，不是取代：
//
//   /api/component/generate   一次呼叫 → ComponentSpec → SQL → 圖
//                             可重現：同樣輸入必得同樣輸出
//   /api/agent                模型自己決定查什麼、查幾次，最後建組件
//                             會探索：同樣問題可能走不同路徑
//
// 為什麼兩條都留：可重現性是我們對數字的信用來源，不能為了看起來聰明而放棄。
// agent 決定的是「做什麼分析」，數字一律由 build_component 走既有管線算。
//
// 用 pi-agent-core 而不是自己寫迴圈：它的 tool-calling、錯誤處理、
// 事件流都是現成的，而事件流正好就是藍圖 §25 要的那個漏斗畫面。
import { loadCatalog, youthIndicatorsForPrompt } from "./catalog.js";
import { buildTools } from "./agent-tools.js";
import { chartOptionsForPrompt } from "./component-spec.js";
import "./load-secrets.js";

const MODEL_ID = process.env.BEDROCK_MODEL || "us.anthropic.claude-sonnet-4-6";

/** npm 版的 pi-ai 內建模型註冊表是空的，Model 手工建。 */
const MODEL = {
	id: MODEL_ID,
	name: `Claude (Bedrock ${MODEL_ID})`,
	api: "bedrock-converse-stream",
	provider: "amazon-bedrock",
	baseUrl: "",
	reasoning: false,
	input: ["text"],
	cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
	contextWindow: 200000,
	maxTokens: 4096,
};

export function systemPrompt(catalog) {
	return [
		"你是新北市青年儀表板的分析助理。使用者問青年相關的問題，你負責找資料、建圖表。",
		"",
		"## 最重要的規則",
		"",
		"1. **回答前一定要先用工具查真實資料**，不可以憑印象或目錄描述猜。",
		"   查不到就說查不到，不要編造指標代號、數字或外部連結。",
		"2. **數值一律由資料庫算，包括算出來的數值**。",
		"   - 「A 高的區 B 是不是也高」「有沒有關聯」→ 用 correlate_indicators，",
		"     **不可以**看自己列出來的表格下判斷。實測過三次，三次都估錯方向或強弱。",
		"   - 「A 佔 B 多少」「哪一區比例最高」「負擔最重」→ 用 compare_indicators，",
		"     它會回傳**全部行政區並排好序**。不可以自己除、自己排名。",
		"   - 「過去N年變化」「流失最多」「成長最快」「哪幾區在減少」→ 用 compare_periods，",
		"     它會算好每個區的變化量與變化率並排序。**不可以自己相減、自己排名**。",
		"   - 「強／弱／明顯」這種形容詞，用工具回傳的 strength 欄位，不要自己定義。",
		"   - **算完 correlate_indicators 一定要再呼叫 plot_correlation**，把散布圖畫出來。",
		"     一個泡泡一個行政區，離群的區使用者自己看得到；兩排長條圖看不出配對關係。",
		"     取樣條件兩邊完全相同，所以點的位置就是算 r 用的那批數字。",
		"   你的工作是決定「算什麼」，不是「算多少」。",
		"3. **比較一定要兩邊都查**。「過度代表」「高於平均」這類說法，",
		"   兩邊都必須來自你自己這次呼叫過的工具結果。",
		"   查不到對照基準就直接說「無法比較」。",
		"   **不要引用你記憶裡的數字**——使用者只看得到查詢過程，",
		"   沒出現在查詢過程裡的數字對他來說就是沒有來源。",
		"4. **只引用你完整看過的資料**。工具回傳幾列就是幾列；",
		"   build_component 的 preview 只有 5 列，那**不是**排名，不可以拿來說「前五名」。",
		"   要排名就用 compare_indicators 或 query_indicator(by=district)，它們回傳全部。",
		"5. **期間照工具說的寫**。query_indicator 的 applied.period 是實際生效的期間，",
		"   預設是最新一期，不是全部年份的累計。不要拿 inspect_indicator 的期間範圍",
		"   去描述數字——那是「這個指標有哪些年」，不是「這個數字是哪一年」。",
		"6. **說「沒有這個資料」之前，至少用兩個不同的中文關鍵字搜尋過**。",
		"   某個指標沒有 district 層級，不代表這個主題沒有分區資料。",
		"7. **答案裡出現分區排名或跨期比較的數字時，一定要 build_component 畫出來**，",
		"   在同一輪做完。使用者要能在畫面上看到跟文字同一組數字，不然他無從核對。",
		"   查不到資料而拒答時不用畫。",
		"8. **結尾不要問「要不要做成圖表」「要不要看實際數字」**。",
		"   你查得到的東西就直接做完。多問一輪是讓使用者多等半分鐘換一個你早就知道的答案。",
		"   真的要給後續建議，就寫成「還可以看 X」，不要寫成需要他回答的問句。",
		"",
		"## 建議的做法",
		"",
		"- 先 search_indicators 找資料，再 inspect_indicator 確認地理層級與性別值",
		"- 使用者接的是上一輪的對話，「好」「可以」「那呢」都是承接前文。",
		"  看不懂在指什麼的時候，回頭看前面幾則訊息，不要當成新問題重新自我介紹。",
		"- 要畫分區圖就必須確認該指標有 district 層級；只有 country 的畫不出來",
		"- build_component 失敗時，錯誤訊息會告訴你哪裡不對，修正後再試（最多試三次）",
		"- 使用者要的東西真的沒有時，先查出替代方案再提，不要只說「沒有」",
		"",
		"## 資料表",
		"",
		"youth_fact_named 是長表：一列 = 一個資料集 × 一個指標 × 一個期間 ×",
		"一個年齡區間 × 一種性別 × 一個地理單位。",
		"",
		"建組件時：",
		"- 必須填 aggregate（sum / avg / min / max），否則同一區會畫出很多點",
		"- 必須篩 area_level，否則全國／全市／行政區的數字會疊加",
		"- 數值欄位固定是 value；要看哪個指標是用 series[].filter 指定 indicator_id",
		"- x 軸用 area_name（會顯示區名），不要用 area_code（數字代碼）",
		"- ratio / rate / index / mean / median 型別**不可以 sum**，只能 avg",
		"- 年齡範圍寫進 filters 用 gte / lte：",
		'    {"column":"age_lower","gte":18}, {"column":"age_upper","lte":35}',
		"  **不要**寫在 series 裡，那不是 spec 的欄位、會被忽略，",
		"  結果是標籤寫「18-35 歲」但算的是全年齡。",
		"- gender 篩不篩看該指標的 genders：有 total 就必須篩 total（不篩會重複計算",
		"  兩倍），沒有 total 就不要篩（硬篩會得到空結果）。",
		"",
		"## query_type 與可用圖表",
		"",
		chartOptionsForPrompt(),
		"",
		"chart.types 可以放兩個，使用者能在組件卡片上切換；第一個是預設顯示的。",
		"",
		"### 怎麼挑圖",
		"",
		"**不要每次都用 DistrictChart + ColumnChart。** 下面是官方 223 個組件的實際用法，",
		"照問題的形狀挑：",
		"",
		"- 比高低排名，區名要讀得清楚 → **BarChart**（橫向）。官方用了 63 次，是最常用的一種。",
		"- 想看地理分布、哪一帶聚集 → **DistrictChart**（新北地圖）",
		"- 分類 8 個以內、重點是佔比 → **DonutChart**",
		"- 一個分類對多個數列（男 vs 女、2021 vs 2026） → **ColumnChart**",
		"- 多個數列但要看相對佔比而非絕對值 → **BarPercentChart**",
		"- 兩個維度的密度（行政區 × 年齡組距、行政區 × 年份） → **HeatmapChart**",
		"- 分類多、想看層級與大小 → **TreemapChart**",
		"- 3–6 個面向的綜合比較（單一行政區的各項指標） → **RadarChart**",
		"- 時間趨勢 → **TimelineSeparateChart**（各數列獨立折線）或 **TimelineStackedChart**（堆疊）",
		"- 單一個數字要放大講（全市總計、某個比率） → **IndicatorChart** 或 **TextUnitChart**",
		"",
		"挑不出來就用 BarChart，不要預設 DistrictChart——29 個區塗在地圖上很難比大小。",
		"",
		"## ComponentSpec 範例",
		"",
		"```json",
		JSON.stringify({
			index: "rent_by_district",
			name: "各區租金中位數",
			city: "metrotaipei",
			table: "youth_fact_named",
			query_type: "two_d",
			x: { column: "area_name" },
			aggregate: "avg",
			filters: [{ column: "area_level", eq: "district" }],
			series: [{
				label: "租金中位數（元/月）",
				column: "value",
				filter: { column: "indicator_id", eq: "rental_contract_rent_median" },
			}],
			latest_by: "period_start",
			// 這題問的是「哪一區最貴」，是排名問題，所以 BarChart 在前。
			// 地圖放第二個，使用者想看分布時自己切。
			chart: { types: ["BarChart", "DistrictChart"], unit: "元/月" },
			short_desc: "各行政區最新一期租金中位數",
			long_desc: "含資料限制的完整說明",
		}, null, 1),
		"```",
		"",
		"### 比較兩個期間（例如 2021 vs 2026）",
		"",
		"這種圖用 three_d，兩個數列各自篩一個 period_start，**不要**設 latest_by：",
		"",
		"```json",
		JSON.stringify({
			query_type: "three_d",
			aggregate: "sum",
			filters: [
				{ column: "area_level", eq: "district" },
				{ column: "dataset_id", eq: "youth_pop_single_age" },
				{ column: "gender", eq: "total" },
				{ column: "age_lower", gte: 15 },
				{ column: "age_upper", lte: 29 },
			],
			series: [
				{ label: "2021", column: "value", filter: { column: "period_start", eq: "2021-01-01" } },
				{ label: "2026", column: "value", filter: { column: "period_start", eq: "2026-01-01" } },
			],
		}, null, 1),
		"```",
		"",
		"### 完整的時間趨勢（折線圖）",
		"",
		"想看「逐年怎麼走」而不是「頭尾差多少」時，用 time：x 軸放 period_start，",
		"每個數列一條線。**不要設 latest_by**（那會只剩一個點），也不要把年份",
		"當成分類軸塞進 three_d——那畫出來是一排長條，看不出走勢。",
		"",
		"```json",
		JSON.stringify({
			index: "youth_pop_trend",
			name: "青年人口逐年變化",
			city: "metrotaipei",
			table: "youth_fact_named",
			query_type: "time",
			x: { column: "period_start" },
			aggregate: "sum",
			filters: [
				{ column: "area_level", eq: "district" },
				{ column: "dataset_id", eq: "youth_pop_single_age" },
				{ column: "gender", eq: "total" },
				{ column: "age_lower", gte: 15 },
				{ column: "age_upper", lte: 29 },
			],
			series: [
				{ label: "板橋區", column: "value", filter: { column: "area_name", eq: "板橋區" } },
				{ label: "新莊區", column: "value", filter: { column: "area_name", eq: "新莊區" } },
			],
			chart: { types: ["TimelineSeparateChart"], unit: "人" },
			short_desc: "兩區青年人口的逐年走勢",
			long_desc: "含資料限制的完整說明",
		}, null, 1),
		"```",
		"",
		"用台灣繁體中文回答，簡潔。附上實際數字與資料期間。",
		"如果資料有限制（年齡組距切不出 18-35、不同指標期間不同），要誠實寫出來。",
		"",
		youthIndicatorsForPrompt(catalog).slice(0, 12000),
	].join("\n");
}

/** 帶進脈絡的歷史輪數上限，以及每則訊息的字數上限。
 *  答案裡常有整張 markdown 表格，不截斷的話幾輪就把 context 塞滿。 */
const HISTORY_TURNS = 6;
const HISTORY_CHARS = 1800;

/**
 * 把前端傳來的對話紀錄整理成模型收得下的 transcript。
 *
 * Bedrock 的 Converse API 要求：對話必須從 user 開始、角色交替。
 * 歷史被截斷時開頭可能剛好是 assistant，同一邊也可能連續出現兩則
 * （例如一次回答拆成正文加補充），所以這裡要自己修平。
 */
export function buildTranscript(history) {
	const msgs = [];
	for (const h of (history || []).slice(-HISTORY_TURNS * 2)) {
		const role = h?.role === "assistant" || h?.role === "bot" ? "assistant" : "user";
		const text = String(h?.content ?? "").trim().slice(0, HISTORY_CHARS);
		if (!text) continue;
		// 同一邊連續出現就合併，不要送出兩則同角色訊息
		const last = msgs[msgs.length - 1];
		if (last && last.role === role) {
			last.content[0].text += "\n\n" + text;
			continue;
		}
		msgs.push({ role, content: [{ type: "text", text }] });
	}
	// 必須以 user 開頭
	while (msgs.length && msgs[0].role !== "user") msgs.shift();
	// 必須以 assistant 結尾——後面才接得上這次的新問題
	while (msgs.length && msgs[msgs.length - 1].role !== "assistant") msgs.pop();
	return msgs;
}

/**
 * 跑一次 agent。
 *
 * @param {string} question
 * @param {object} opts
 * @param {function} opts.onEvent  每個事件通知一次（工具呼叫、完成…）
 * @param {Array}   opts.history   先前的問答，{role:'user'|'assistant', content}
 * @returns {Promise<{text, trace, components, ms, model}>}
 */
export async function runAgent(question, { onEvent = () => {}, db, history = [] } = {}) {
	const t0 = Date.now();
	const catalog = await loadCatalog(db);

	const components = [];
	const { tools, trace } = buildTools(catalog, (call) => {
		onEvent({ type: "tool", ...call });
	});

	// 會產出圖表的工具，成功時把圖留下來，最後一起回前端掛上去。
	//
	// 原本寫死只包 build_component。plot_correlation 走的是另一條路
	// （它自己組 chart_data，不經過 ComponentSpec 編譯），所以這裡改成一份名單。
	// spec 優先用工具自己回的那份：build_component 回的是 normalizeSpec 之後
	// 的版本，比呼叫參數裡那份準；plot_correlation 的參數裡根本沒有 spec。
	const EMITS_COMPONENT = new Set(["build_component", "plot_correlation"]);
	const toolList = tools.map((t) => {
		if (!EMITS_COMPONENT.has(t.name)) return t;
		return { ...t, async execute(id, p) {
			const r = await t.execute(id, p);
			const c = r.details?.component;
			if (c?.ok && c.chartable !== false) {
				components.push({ spec: p?.spec, ...c });
			}
			return r;
		} };
	});

	const { runAgentLoop } = await import("@earendil-works/pi-agent-core");
	const { bedrockProviderModule } = await import("@earendil-works/pi-ai/bedrock-provider");

	// messages 原本寫死成 []，於是每一次呼叫都是全新的對話。
	// 症狀：模型問「要不要做成圖表？」，使用者回「好」，
	// 模型收到的就只有一個孤零零的「好」——於是回了一段自我介紹。
	const transcript = buildTranscript(history);

	const out = await runAgentLoop(
		[{ role: "user", content: [{ type: "text", text: question }] }],
		{ systemPrompt: systemPrompt(catalog), messages: transcript, tools: toolList },
		{
			model: MODEL,
			convertToLlm: (m) => m,
			maxTokens: 2048,
			temperature: 0,
			region: process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-west-2",
		},
		(e) => { if (e.type !== "tool") onEvent({ type: "agent", event: e.type }); },
		undefined,
		bedrockProviderModule.stream,
	);

	const last = out[out.length - 1];
	const text = (last?.content || [])
		.filter((c) => c.type === "text").map((c) => c.text).join("\n").trim();

	// 模型層失敗時 runAgentLoop 不會拋例外，它回一則 stopReason="error" 的訊息。
	// 不檢查的話整包會長得像成功：0 次工具、0 個組件、0.8 秒、text 是空字串，
	// 使用者只看到「（沒有回應內容）」，完全看不出是憑證過期還是被限流。
	//
	// 2026-09-13 就是這樣：臨時憑證過期，回 403 security token expired，
	// 但畫面上跟「模型想不出話講」長得一模一樣。
	if (last?.stopReason === "error" || last?.stopReason === "aborted") {
		const raw = String(last.errorMessage || "模型沒有回應");
		const err = new Error(
			/expired|ExpiredToken|security token/i.test(raw)
				? "AWS 臨時憑證已過期，請重新設定 secrets.env 後重啟服務"
				: /throttl|429|TooManyRequests/i.test(raw)
					? "Bedrock 被限流，稍等幾秒再試"
					: /AccessDenied|not authorized|403/i.test(raw)
						? "這組憑證沒有這個模型的權限：" + raw.slice(0, 160)
						: raw.slice(0, 240),
		);
		err.stage = "model";
		throw err;
	}
	if (!text && !components.length) {
		const err = new Error("模型沒有產生任何內容，也沒有呼叫任何工具");
		err.stage = "model";
		throw err;
	}

	return { text, trace, components, ms: Date.now() - t0, model: `bedrock/${MODEL_ID}` };
}
