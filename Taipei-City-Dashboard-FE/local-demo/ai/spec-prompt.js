// 把資料目錄組成給模型的 system prompt。
//
// 對照 AimchartAI 的 aimchart/core/prompt.py：prompt 依 catalog 動態生成，
// 不是寫死的字串。加一張新表到 catalog.yaml，prompt 就自動涵蓋它。
//
// 這裡刻意只告訴模型「有哪些表、哪些欄位、各是什麼意思」，不給它 SQL 範例——
// 給了 SQL 範例，它就會開始改寫 SQL；我們要的是它只輸出 spec。
import { catalogForPrompt } from "./catalog.js";
import { chartOptionsForPrompt } from "./component-spec.js";

/** ComponentSpec 的形狀，直接寫進 prompt 讓模型照著填 */
const SCHEMA = `{
  "index":      "小寫英數底線，開頭是英文字母，例如 youth_rental_subsidy",
  "name":       "中文組件名稱，8 字以內",
  "city":       "taipei 或 metrotaipei",
  "table":      "下方目錄裡的資料表名稱",
  "query_type": "見下方「query_type 與可用圖表」",
  "x":       { "column": "分組欄位", "exclude": ["要排除的值，例如合計列"] },
  "series":  [ { "label": "數列名稱", "column": "數值欄位",
                 "filter": { "column": "欄位", "eq": "值" } } ],   // filter 選填，見規則 12
  "filters": [ { "column": "欄位", "eq": "值" } ],
  "latest_by": "只取最新一期時填年份欄位，否則省略",
  "transform": { "divide": 1000 },
  "chart":   { "types": ["DistrictChart", "ColumnChart"], "unit": "仟人" },
  "short_desc": "一句話說明",
  "long_desc":  "含資料限制的完整說明"
}`;

const RULES = `規則：

1. 只能使用下方目錄列出的資料表與欄位。目錄裡沒有的，一律不准出現——
   寧可回 {"refuse": "理由"}，也不要編一個看起來合理的欄位名。
2. 不要輸出 SQL。你的工作是挑欄位，SQL 由程式產生。
3. 不要輸出任何數值。你沒有看到資料，任何數字都是猜的。
4. 同一張圖的 series 必須是同一種量：count（人數）不能和 ratio（百分比、指數）混用。
5. ratio 型別不可套用 transform.divide——縮放後就不再是任何東西的百分比。
6. 人數欄位若數值很大，用 "transform": {"divide": 1000} 並把 unit 設為「仟人」。
7. 做分區比較時，x.exclude 必須排除合計列（各表的合計列名稱見目錄的「注意」）。
8. long_desc 要誠實寫出資料限制。例如公開統計採 5 歲組距，
   《青年基本法》的 18-35 歲無法直接切出，只能以 20-34 近似——這種限制要寫。

9. city 必須與資料表所屬的城市一致（見目錄）。一個組件只能對應一張表、一個城市；
   使用者若要求跨城市比較（例如「雙北」），回 {"refuse": "..."} 並建議拆成兩個組件，
   不要挑一邊然後把組件命名成涵蓋兩邊——那會產生一張名實不符的圖。
10. query_type 與 chart.types 只能從下方清單挑。清單以外的圖表元件不存在，
   寫了會被擋下（例如沒有 LineChart）。
11. 目錄中的「年份」是整數年，不是日期欄位，所以時間趨勢仍用 three_d 搭 ColumnChart，
   不要用 time——time 需要真正的日期欄位，本專案的表都沒有。

12. 要把「同一個欄位、不同篩選值」畫成多個數列時（最常見是男女對照），
   把條件放進**該數列自己的** series[].filter，不要放進最外層的 filters——
   放外層會讓整張圖只剩一個性別，圖卻還標著兩個。例：

     "series": [
       {"label": "男性 20-24歲", "column": "percent7",
        "filter": {"column": "統計類型", "eq": "男"}},
       {"label": "女性 20-24歲", "column": "percent7",
        "filter": {"column": "統計類型", "eq": "女"}}
     ]

13. 數列的 label 必須與它實際取到的資料相符。
   標籤寫「女性」卻沒有對應的 filter，會被擋下——那種圖畫得出來、數字也像真的，
   但回答的是另一個問題，使用者沒有線索能發現。

只輸出 JSON，不要有其他文字，不要包在程式碼區塊裡。`;

export function buildSpecPrompt(catalog, question) {
	return [
		"你是青年城市儀表板的組件設計助理。",
		"使用者用自然語言描述想看的東西，你要挑出該用哪張表、哪些欄位、怎麼分組，",
		"輸出一份 ComponentSpec。",
		"",
		"## 輸出格式",
		SCHEMA,
		"",
		RULES,
		"",
		"## query_type 與可用圖表",
		"",
		chartOptionsForPrompt(),
		"",
		"series 只有一項時用 two_d，兩項以上用 three_d。",
		"",
		"## 可用的資料目錄",
		"",
		catalogForPrompt(catalog),
		"## 使用者的問題",
		question,
	].join("\n");
}

export { SCHEMA, RULES };
