# 青年儀表板 · 架構與整合說明

給組員的技術文件。看完應該能：跑起來、知道東西在哪、知道自己能改什麼不會撞到別人。

更完整的圖解與實測結果見進度報告（含架構圖、對話逐字稿、五個已修的 bug）。

---

## 1. 五分鐘跑起來

```bash
cd Taipei-City-Dashboard/Taipei-City-Dashboard-FE
npx vite --port 5173 --host 127.0.0.1
```

開 <http://127.0.0.1:5173/dashboard?index=youth-newtaipei&city=metrotaipei>

**這三個參數都是必要的，錯一個就會失敗，而且症狀看起來跟原因無關：**

| 參數 | 為什麼 |
|---|---|
| 從 `Taipei-City-Dashboard-FE/` 啟動 | vite 只讀自己 cwd 的 `vite.config.js`。從 `local-demo/` 啟動，插件不會載入，所有自訂端點 404 |
| `--port 5173` | `vite.config.js:24` 寫死 `port: 80`，macOS 上需要 root |
| `--host 127.0.0.1` | `vite.config.js:23` 寫死 `0.0.0.0`，會把服務公開到整個區域網路 |

不需要 Docker、不需要資料庫、不需要後端。前端的 proxy 直接接官方公開 API。

驗證有沒有跑對：

```bash
curl -s -D- -o/dev/null http://127.0.0.1:5173/api/dashboard/ | grep -i x-inject-source
```

有 `X-Inject-Source` 這個 header 就代表插件載入成功。

---

## 2. 先分清楚：組件不是儀表板

這兩個詞全文反覆出現，混淆會讓後面看不懂。

| | 畫面上是什麼 | 資料庫 |
|---|---|---|
| **儀表板** | 左側選單的一個項目 | `dashboards` 一列 |
| **組件** | 頁面裡的一張卡片 | `components` 一列 |

`dashboards` 表的 `components` 欄位就是一個 id 陣列：

```
index              name       components
ltc_care_newtpe    長照關懷    {214, 215, 216, 218}
```

那些 id 對應到 `components` 表的實際組件。**儀表板本身沒有任何資料**，
它只是說「請顯示這幾個組件」。

> **組件是歌，儀表板是播放清單。** 同一首歌可以在多張清單裡；
> 建清單不會產生新歌；刪清單，歌還在。

### 一個組件由三張表構成

| 表 | 放什麼 |
|---|---|
| `components` | id、index、name |
| `query_charts` | **取數 SQL**、query_type、來源、說明（每個城市一列） |
| `component_charts` | 配色、圖表類型、單位（跨城市共用，只有一列） |

**組件沒有任何專屬程式碼**，就是這三筆資料列。圖表元件（`DistrictChart` 等 25 個）
是通用的，只認得 `categories` + `data` 的形狀。

所以「做一個新組件」＝「產生一組新的設定」，不需要寫任何前端程式。

---

## 3. 這個專案其實是三個專案

最容易搞混的一點。官方 repo 底下有三個子專案：

| 目錄 | 技術 | 做什麼 |
|---|---|---|
| `Taipei-City-Dashboard-FE` | Vue 3 · 99 個 `.vue` | 畫面、圖表、chatbot |
| `Taipei-City-Dashboard-BE` | Go · 82 個 `.go` | API，執行 SQL 並組成圖表格式 |
| `Taipei-City-Dashboard-DE` | Python · 467 個 `.py` | 213 條 Airflow 資料管線 |

**我們 99% 的工作都在 FE，而且集中在一個獨立資料夾 `local-demo/`。**

---

## 4. 我們改動了什麼

對官方原始碼的修改只有 **204 行、9 個檔案**。其餘全是新增的獨立資料夾。

```
 +3   -1   .gitignore                                  排除 .codegraph/
 +21  -5   BE/app/models/qdrant.go                     讓後端能在 macOS 開機
 +2   -0   FE/package.json                             加 bedrock-sdk 與 js-yaml
 +154 -2   FE/src/components/dialogs/ChatBox.vue       自動生成的按鈕與掛載
 +19  -2   FE/src/store/chatStore.js                   接上生成流程
 +2   -1   FE/vite.config.js                           掛上 local-demo 插件
 +1   -1   ×3  AiSummaryBox / MapAiSummaryBox / Mobile 顯示真實模型名稱
```

**原則：能寫在 `local-demo/` 就不改官方檔案。** 這樣主辦方可以只拿走想要的部分。

---

## 5. 資料有三條路徑

這是最重要的一張心智圖。**三條路送進前端的格式完全相同**，前端分不出資料從哪來。

### A · `inject.js` — 不碰資料庫

Vite 的 middleware，攔截 `/api/*`，回傳事先做好的 JSON。

- **用在**：比賽當天的保險。斷網、資料庫掛掉都還能 demo
- **資料**：`component_9001.json`（組件設定）+ `chart_9001.json`（圖表資料）
- **另外**：對官方 API 做快取（`cache/` 195 檔），`OFFLINE=1` 可完全離線

每個回應都有 `X-Inject-Source` header，debug 時看這個：

| 值 | 意思 |
|---|---|
| `local` | 我們自己的組件 |
| `cache` | 官方 API，從快取來 |
| `network` | 快取沒有，連網抓的 |
| `generate:ok` | 自動生成成功 |
| `ai:stub` / `ai:gemini` | AI 洞察 |

### B · 真的 Go 後端 — 讀真資料庫

完整跑官方架構：PostgreSQL + Redis + Go API。

- **用在**：證明交付物真的能用、展示完整架構
- **已驗證**：新北 29 區 735 仟人、台北 12 區 392 仟人
- 設定見 `backend/README.md`

### C · 自動生成 — Node 直接查資料庫

**不經過 A 的假 JSON，也不經過 B 的 Go 後端。** `ai/catalog.js` 直接用 `psql`
查 `dashboard` 資料庫，數字是 PostgreSQL 算出來的。

---

## 6. 檔案地圖

```
local-demo/
├── inject.js                  Vite 插件：攔 API、離線快取、AI 端點
├── component_9001.json        組件設定（給路徑 A 用）
├── chart_9001.json            圖表資料（給路徑 A 用）
├── cache/                     官方 API 快取，195 檔
├── prewarm.mjs                預熱快取的腳本
│
├── ai/                        ← 自動生成組件的全部邏輯
│   ├── catalog.yaml           35 個欄位的「意思」（人工維護）
│   ├── catalog.js             YAML × information_schema，含自檢
│   ├── spec-prompt.js         依目錄動態組 prompt
│   ├── component-spec.js      驗證 + SQL 編譯
│   ├── official.js            查官方有沒有類似組件
│   ├── provider.js            模型抽換點 ← 9/12 只改這裡
│   ├── generate.js            完整流程入口
│   ├── prompt.js              AI 洞察用的 prompt
│   ├── stub-answers.js        AI 洞察的離線備援
│   └── secrets.env            金鑰（gitignore，要自己建）
│
├── db/                        ← 給主辦方接手的 SQL
│   ├── 00_schema_dashboardmanager.sql
│   ├── 01_data_table.sql      2,250 筆資料
│   ├── 02_register_component.sql
│   ├── 03_add_to_dashboard.sql
│   ├── README.md
│   └── GUI速查.md              TablePlus 連線與常用查詢
│
└── backend/                   ← 本機跑 Go 後端
    ├── env.sh                 環境變數（gitignore）
    └── README.md
```

---

## 7. 自動生成組件的設計

核心原則一句話：**LLM 不碰數字。**

```
問題 → [模型] → ComponentSpec → [程式編譯] → SQL → [PostgreSQL] → 數值 → 畫圖
                     ↑                                    ↑
               模型到此為止                        數字在這裡產生
```

模型只輸出一份 **ComponentSpec**（挑哪張表、哪幾個欄位、怎麼分組），
**不寫 SQL、不產生任何數值**。SQL 由 `component-spec.js` 決定性地組出來。

### 三層防護

| 層 | 機制 | 擋掉什麼 |
|---|---|---|
| 封閉詞彙表 | `catalog.yaml` | 編造的表名、欄位名 |
| 不產生可執行字串 | `compileSpec()` | SQL injection |
| 先跑過再說 | `generate.js` | 排序錯位、空結果 |

### 兩個自動處理（模型不必操心）

**單一數列自動降級。** `normalizeSpec()` 會把只有一個數列的 `three_d`
改成 `two_d`。因為官方 `DistrictChart` 依**數列數量**切換資料格式解讀
（`DistrictChart.vue:135-141`）——一個數列時讀 `{x,y}` 物件，多個數列時讀純數字陣列。
單一數列的 `three_d` 會讓它拿到 `undefined`，總合顯示 `NaN`。

**ratio 欄位自動取小數。** catalog 標為 `ratio` 的欄位（百分比、指數）在資料庫是
`real`，加總會拖出 `7238.157860000002` 這種尾巴。依型別自動補 `round: 1`。

### 性別維度怎麼修的

原本 `series` 只能放欄位名，表達不了「同一個欄位、不同篩選值」，
所以問「各區女性的青年人口」只能退回 `統計類型 = 計`，**回男女合計且不說**。

改法是讓每個數列可以帶自己的篩選條件：

```json
"series": [
  {"label": "男性 20-24歲", "column": "percent7",
   "filter": {"column": "統計類型", "eq": "男"}},
  {"label": "女性 20-24歲", "column": "percent7",
   "filter": {"column": "統計類型", "eq": "女"}}
]
```

同一個 `percent7` 出現兩次，靠 filter 區分——這是原本的格式做不到的事。

連帶要改 `orderProbeSQL`：它原本假設「一個 x 只對應一列」，把欄位加起來排序。
有了 per-series filter，同一個區會落在不同列上（男一列、女一列），
所以改成先 UNION 展開再 `GROUP BY` 排序。
**沒有 per-series filter 時結果與舊版逐字相同**（已實測比對）。

### 光改格式不夠，還要擋住「標籤說謊」

模型仍可能寫出標籤「女性 20-24歲」、篩選卻是 `計` 的 spec——
圖畫得出來、數字也對得上某個東西，只是那個東西是男女合計。

所以 `catalog.yaml` 為 `統計類型` 補上 `values: [計, 男, 女]`，
`validateSpec` 拿它比對每個數列的標籤與實際篩選值，不一致就擋下。

判斷依據是 catalog 的資料而非硬寫「男／女」，**任何有列舉值的分類欄位都自動受保護**。

---

### 生成前會查官方有沒有

`official.js` 會查官方是不是已經有同一批資料做的組件，並講清楚**差在哪個視角**：

| 情況 | 回覆 |
|---|---|
| 視角不同 | 「補足既有組件沒涵蓋的切面」 |
| 視角相同且數列重疊 | 「⚠ 可能重複」 |
| 查不到（離線） | 「無法確認」——**不謊稱「沒有」** |

判斷依據是 `catalog.yaml` 的 `related_official_components`（人工標註官方組件 index），
**不是名稱字串比對**。因為向量檢索示範過那樣會錯——它把「全市**屋齡**分布」
排在「扶養比及**老化**指數」前面，字面接近、語意無關。

比對到**數列層級**：官方「全市年齡分區」是 0-14/15-64/65+ 粗分組，
跟我們做的 5 歲細組距是不同切面，不該被說成「已經有了」。

### ComponentSpec 長什麼樣

```json
{
  "index": "youth_population_by_district",
  "name": "青年人口分區",
  "city": "metrotaipei",
  "table": "city_age_distribution_newtaipei",
  "query_type": "three_d",
  "x": { "column": "區域別", "exclude": ["新北市"] },
  "series": [
    { "label": "20_24歲人口數", "column": "percent7" },
    { "label": "25_29歲人口數", "column": "percent8" }
  ],
  "filters": [{ "column": "統計類型", "eq": "計" }],
  "latest_by": "年份",
  "transform": { "divide": 1000 },
  "chart": { "types": ["DistrictChart", "ColumnChart"], "unit": "仟人" }
}
```

每個數列還可以帶自己的 `filter`，用來表達「同欄位不同值」：

```json
"series": [
  { "label": "男性 20-24歲", "column": "percent7",
    "filter": { "column": "統計類型", "eq": "男" } },
  { "label": "女性 20-24歲", "column": "percent7",
    "filter": { "column": "統計類型", "eq": "女" } }
]
```

這時最外層的 `filters` **不要**再放 `統計類型`——放了會把整張圖鎖在一個性別。

### 為什麼一定要 catalog.yaml

因為欄位叫 `percent2` 到 `percent33`，**名字完全沒有語意**。模型只看資料庫的
欄位清單，不可能知道 `percent7` 是 20-24 歲。沒有這份對照，生出來的會是
「語法正確但意義錯誤」——最難發現的那種錯。

對照關係全部用實際資料驗算過：

```
percent3..percent23  加總 = percent2    ✓  21 個 5 歲組距 = 總人口
percent6..percent15  加總 = percent26   ✓  15-64 歲工作年齡人口
percent28 / percent24 × 100 = percent33 ✓  老化指數，臺北 2025 = 190.77
```

---

## 8. 組件資料契約

如果你要做新組件，這是最該讀懂的部分。

### 回應外層

```json
{ "categories": [...], "data": [...], "status": "success" }
```

**兩個會踩的坑：**

1. 在 Vue 裡，圖表資料是 `response.data.data` —— Axios 加一層 `.data`，API 自己也有一層
2. `categories` 從最外層來，但圖表元件讀的是 `chart_config.categories`
   （`contentStore.js:332-336` 負責搬過去）

### 六種 query_type

| type | 形狀 | 常配的圖表 |
|---|---|---|
| `two_d` | `[{data:[{x,y}]}]` | BarChart, ColumnChart, DistrictChart, DonutChart |
| `three_d` | `{categories, data:[{name,data:[]}]}` | ColumnChart, DistrictChart, BarPercentChart |
| `percent` | 同 three_d | GuageChart, BarPercentChart |
| `time` | `[{name, data:[{x:ISO時間, y}]}]` | TimelineSeparateChart, ColumnLineChart |
| `map_legend` | `[{name,type,value}]` | MapLegend |
| `layered_flow` | `[{source,target,value,...}]` | SankeyChart |

### 三個會靜默失敗的陷阱

**① 排序沒固定 → 數值對到錯的項目**

後端組圖表時是**按資料列順序逐一附加**，完全不檢查每個數值屬於哪個分類
（`componentData.go:301-325`）。三個年齡層的行政區排序一旦不一致，數字就會錯位，
**而且不會報錯**。

我們的 SQL 一律用 `ARRAY_POSITION` 固定順序。

**② city 不對 → 組件整個不顯示**

`contentStore.js:685-695` 會依 `item.city === currentDashboard.city` 過濾。
只接受 `taipei` 或 `metrotaipei`，填 `newtaipei` 會被靜默丟掉。

**③ 缺 component_charts → 組件整個消失**

後端組裝組件時，`components` 和 `component_charts` 是 **INNER JOIN**
（`componentConfig.go:148-150`）。缺那一筆，組件不會顯示，也不會報錯。

---

## 9. 分工建議

以下幾塊彼此不衝突，可以同時進行：

| 範圍 | 碰哪些檔 | 需要什麼 |
|---|---|---|
| **新組件（資料）** | `db/01_data_table.sql` + `02_register_*.sql` | psql |
| **新組件（前端展示）** | `component_*.json` + `chart_*.json` | 只要 vite |
| **catalog 擴充** | `ai/catalog.yaml` | 要驗算欄位語意 |
| **prompt 調校** | `ai/spec-prompt.js` | 要有模型金鑰 |
| **AI 洞察** | `ai/prompt.js` + `stub-answers.js` | 只要 vite |
| **簡報 / 講稿** | 不碰程式碼 | — |

**盡量不要同時改** `ChatBox.vue`、`chatStore.js`、`inject.js` 這三個——
它們是接縫，容易衝突。要改先講一聲。

---

## 10. 已知問題

寫下來是為了不要有人重新踩一次。

### 🟡 同一個問題換個問法，年份可能不同

新北有兩張表，**最新年份不一樣**：

| 資料表 | 涵蓋年份 |
|---|---|
| `city_age_distribution_newtaipei` | 2000–2023 |
| `youth_population_district_newtaipei` | 2000–2024 |
| `city_age_distribution_taipei` | 2020–2025 |

模型挑哪張表使用者看不到，於是「新北各區青年人口」可能回 2024 的 739,771，
「新北各區女性青年人口」卻回 2023 的數字。**兩個都對，但差一萬六。**

**已做的緩解**——不是消除，是讓它看得見：

- `catalog` 啟動時去資料庫問每張表的年份範圍，寫進 prompt，
  模型問「最新」時知道哪張比較新（`catalog.js`）
- 每個生成結果都回傳 `stats.period` 與 `stats.table`

**被問到時就照實說：資料來源不同年，這是公開統計的更新節奏差異。**

### ✅ 已修（9/12 實測 Bedrock 後）

接上真模型才浮現的問題，都補了**程式層的守門**——不是改 prompt 就算數，
因為 prompt 只能勸，擋不住。

| 症狀 | 守在哪 |
|---|---|
| `two_d` 給多數列，後面的被安靜丟掉 | `validateSpec` |
| `city` 與資料表不符（雙北） | `validateSpec` |
| 年份當 x 軸讓 SQL 炸掉 | `compileSpec` 轉型 |
| 圖表型別選了不存在的 | `normalizeSpec` 自動改＋警告 |
| 標籤寫「女性」、實際取男女合計 | `validateSpec` 標籤比對 |

第一項最值得記：模型輸出 `two_d` 卻給了 `percent7/8/9` 三欄，
編譯器只取 `series[0]`，畫出來是 29 個區、數字合理、標題寫「青年人口」——
**實際只有 20-24 歲那一段**。每個欄位單獨看都合法，所以原本沒有任何一關會叫。

### 🟡 生成的組件只存在記憶體

重新整理頁面就消失。要永久保留得走 `db/` 那條路，還沒接。

對 demo 來說反而安全——不會把測試組件留在正式資料裡。

### 🟡 臺北的 5 歲組距資料來自官方 sample data

不是我們自己抓的。`data.taipei` 在公司網路連不上，未實測。

---

## 11. 開發期用 Gemini 驗證 prompt

比賽當天規則明訂只能用 Bedrock。Gemini 這條路徑**純粹是開發期驗證用**——
stub 是關鍵字規則，它「答對」只代表寫規則的人猜對措辭，測不出 prompt 對真模型有沒有效。

```bash
# 金鑰寫進 ai/secrets.env（符合 .gitignore 的 *.env）
AI_PROVIDER=gemini npx vite --port 5173 --host 127.0.0.1
```

實測 Gemini 明顯優於 stub 的地方：聽得懂「哪一區最老」這種間接問法、
會把扶養比與老化指數一起給、而且**拒答的品質高很多**——
問「雙北比較」時它自己推論出兩張表不能合併，那件事沒有寫在 prompt 裡。

接上去才發現的兩個問題（都已處理）：

- **思考 token 會吃掉 `maxOutputTokens`**，JSON 從中間被切斷。2048 → 8192
- **服務會間歇性 503**，加了指數退避重試（只對 503/429）

> ⚠ 刻意**不**給嚴格的 `responseSchema`。AimchartAI 的教訓：strict schema 會讓
> 拒答路徑物理上不可能發生——模型被迫填滿每個欄位，說不出「我做不到」。

---

## 12. 9/12 當天

### 這個帳號實際能用哪些模型（9/12 實測）

**不要照文件猜，照實測結果填。** 兩件事跟直覺不同：

**一、所有模型都必須加 `us.` 前綴。**
這個帳號的 Anthropic 模型全部是 `INFERENCE_PROFILE`，沒有一個支援 ON_DEMAND。
寫 `anthropic.claude-opus-5` 會得到 403，而錯誤訊息不會說「你少了前綴」。

**二、「列得出來」不等於「叫得動」。**
`ListInferenceProfiles` 回 27 個，實際呼叫只有 13 個成功。
**Claude 5 系列（Opus 5 / Sonnet 5 / Fable 5）與 Opus 4.7 / 4.8 全部 403。**
能用的最高階是 Opus 4.6。

目前設定（`ai/secrets.env`）：

```
BEDROCK_MODEL=us.anthropic.claude-sonnet-4-6
AWS_DEFAULT_REGION=us-west-2
```

挑 Sonnet 4.6 的理由：挑欄位不需要 Opus，而現場延遲差得出來
（Sonnet 4.6 1107ms、Opus 4.6 1449ms、Haiku 4.5 819ms）。
Opus 4.6（`us.anthropic.claude-opus-4-6-v1`）留作備援。

`us.` 也普遍比 `global.` 快——同一個 sonnet-4-6，1107ms vs 2509ms。

### 有 GPT 嗎？有，但只有開源權重那兩個

一樣的模式：**GPT-6 與 GPT-5.6 全部 403。**

```
✔ openai.gpt-oss-20b-1:0      1041ms
✔ openai.gpt-oss-120b-1:0     1264ms
✗ us.openai.gpt-6-astra       403 not available for this account
✗ us.openai.gpt-5.6-terra / sol / luna   403
```

`gpt-oss` 能力離 Sonnet 4.6 有距離，**不建議當主力**。若要用，注意兩件事：

- 它們不吃 Anthropic 的 messages 格式，要走 Bedrock 的 **Converse API**
  （`@aws-sdk/client-bedrock-runtime`，已隨相依裝好）
- 推理 token 會先吃掉 `maxTokens`——120b 在 64 token 額度下回了空字串、
  `stop=max_tokens`。跟 Gemini 那個坑一樣

其他可用的供應商還有 Amazon Nova、Meta Llama、Mistral、DeepSeek、Qwen 等，
`probe-providers.mjs` 會全部列出來。

### 要換模型或重查時

```bash
node local-demo/ai/probe-providers.mjs    # 所有供應商的模型
node local-demo/ai/probe-bedrock.mjs      # Anthropic：哪些需要 profile
node local-demo/ai/invoke-test.mjs        # 逐個真的呼叫一次，只信這個
node local-demo/ai/probe-openai.mjs       # OpenAI：走 Converse API
```

不用裝 `aws-cli`——`bedrock-sdk` 已經把 `@smithy/signature-v4` 一起裝進來了。

### 跑起來

```bash
AI_PROVIDER=bedrock npx vite --port 5173 --host 127.0.0.1
```

套件（`@anthropic-ai/bedrock-sdk`）已預裝，**不用安裝**。
出問題就拿掉 `AI_PROVIDER`，退回 stub，demo 不會開天窗。

### ⚠️ 憑證會過期

主辦發的是**臨時憑證**（`AWS_ACCESS_KEY_ID` 以 `ASIA` 開頭，帶 session token）。

**如果 demo 途中 Bedrock 開始回 403，第一個要查的就是它。**
從主辦頁面重新取一組，然後：

```bash
# 先清掉舊的，否則載入器會跳過（已存在的鍵不覆寫）
cd local-demo/ai
grep -v '^\(export \)\?AWS_' secrets.env > .tmp && mv .tmp secrets.env
pbpaste | grep '^export AWS_' >> secrets.env     # 先按網頁上的複製鈕
```

貼進終端機自己跑，**不要貼進聊天視窗**——貼了就等於外洩到對話紀錄裡。

### 拿到憑證後第一件事：實跑一次

**不要先寫測試。** 這條教訓來自 AimchartAI 的 worklog：392 個測試全綠，
卻靠實際呼叫才發現「strict schema 讓拒答路徑物理上不可能發生」。

9/12 當天這條教訓又應驗一次：換上真模型後，14 題跑出 4 個 stub 完全測不到的
問題，其中一個會安靜地給錯資料。stub 問不出「Bedrock 真的能動嗎」。

### 上傳 GitHub 前檢查

比賽規則明文要求不得包含憑證。目前已排除的：

| 檔案 | 被什麼擋住 |
|---|---|
| `.env` | `.gitignore:75` 的 `*.env` |
| `ai/secrets.env` | 同上 |
| `backend/env.sh` | `.gitignore:29` 的 `*.sh` |
| `.codegraph/` | `.gitignore:77` |

**填完 AWS 憑證後，推之前再跑一次 `git status` 確認。**

---

## 13. 用到的東西

| 元件 | 版本 | 備註 |
|---|---|---|
| Node | 26.7.0 | |
| PostgreSQL | 16.14 | Homebrew，非 Docker |
| Redis | 8.10.1 | 只有路徑 B 需要 |
| Go | 1.27.1 | 只有路徑 B 需要 |
| onnxruntime | 1.29.0 | 只有路徑 B 需要 |

**路徑 A 只需要 Node。** 資料庫、Redis、Go 都是路徑 B 才要。

```bash
# 只有要跑真後端才需要
brew install go postgresql@16 redis onnxruntime
brew services start postgresql@16 redis
```
