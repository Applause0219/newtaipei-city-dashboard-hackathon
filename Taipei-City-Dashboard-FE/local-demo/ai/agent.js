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

function systemPrompt(catalog) {
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
		"   - 「強／弱／明顯」這種形容詞，用工具回傳的 strength 欄位，不要自己定義。",
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
		"",
		"## 建議的做法",
		"",
		"- 先 search_indicators 找資料，再 inspect_indicator 確認地理層級與性別值",
		"- 回答裡只要出現一組可以互相比較的數字，就順手 build_component 把它畫出來。",
		"  使用者要能在畫面上看到跟文字同一組數字，不然他無從核對。",
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
			chart: { types: ["DistrictChart", "ColumnChart"], unit: "元/月" },
			short_desc: "各行政區最新一期租金中位數",
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

/**
 * 跑一次 agent。
 *
 * @param {string} question
 * @param {object} opts
 * @param {function} opts.onEvent  每個事件通知一次（工具呼叫、完成…）
 * @returns {Promise<{text, trace, components, ms, model}>}
 */
export async function runAgent(question, { onEvent = () => {}, db } = {}) {
	const t0 = Date.now();
	const catalog = await loadCatalog(db);

	const components = [];
	const { tools, trace } = buildTools(catalog, (call) => {
		onEvent({ type: "tool", ...call });
	});

	// build_component 成功時把圖表留下來，最後一起回前端掛上去
	const origBuild = tools.find((t) => t.name === "build_component");
	const wrapped = { ...origBuild, async execute(id, p) {
		const r = await origBuild.execute(id, p);
		if (r.details?.component?.ok && r.details.component.chartable !== false) {
			components.push({ spec: p.spec, ...r.details.component });
		}
		return r;
	} };
	const toolList = tools.map((t) => (t.name === "build_component" ? wrapped : t));

	const { runAgentLoop } = await import("@earendil-works/pi-agent-core");
	const { bedrockProviderModule } = await import("@earendil-works/pi-ai/bedrock-provider");

	const out = await runAgentLoop(
		[{ role: "user", content: [{ type: "text", text: question }] }],
		{ systemPrompt: systemPrompt(catalog), messages: [], tools: toolList },
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

	return { text, trace, components, ms: Date.now() - t0, model: `bedrock/${MODEL_ID}` };
}
