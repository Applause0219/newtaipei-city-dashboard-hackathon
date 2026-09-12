# Worklog

只記「改了什麼、為什麼、下一步」。設計說明在 `ARCHITECTURE.md`。

分支：**`feat/ai-component-generation`**（與組員的重構分開走，避免互相踩）

---

## 2026-09-12

AWS 開通日。主線是「把 stub 換成真的 Bedrock」，但接上真模型後浮現四個
stub 完全測不到的問題，其中兩個會**安靜地給錯資料**。

### A. Bedrock 接通

| 檔案 | 改動 |
|---|---|
| `ai/provider.js` | `AnthropicBedrockMantle` → `AnthropicBedrock` |
| `ai/load-secrets.js` | 新增，共用金鑰載入器 |
| `ai/probe-bedrock.mjs` | 新增，列出可用模型 |
| `ai/invoke-test.mjs` | 新增，逐個實際呼叫 |
| `ai/probe-openai.mjs` | 新增，走 Converse API |
| `ai/probe-providers.mjs` | 新增，列所有供應商 |

**用錯 client 類別是最嚴重的一個。** `AnthropicBedrockMantle` 走的是
Claude Platform on AWS，需要 Marketplace 訂閱、端點也不同；主辦發的臨時帳號
是一般的 Amazon Bedrock。用錯會拿到 404 或 AccessDenied，而錯誤訊息完全不會
指向「類別選錯」。

其他三項：
- region 預設 `us-east-1` → `us-west-2`（主辦畫面的預設值，且限定只能用這兩個）
- `BEDROCK_MODEL` **移除預設值**。沒設定就直接報「先跑 probe」——好過用一個猜的
  ID 去撞出一個指向錯方向的錯誤
- `stop_reason` → `finishReason` 對應。少了這個，被截斷的輸出會被報成
  「模型沒有回傳可解析的 JSON」，跟 Gemini 路徑當初踩過的坑一樣

加了 429/503 指數退避重試——黑客松是共用帳號，限流幾乎一定會遇到。

### B. 模型可用性實測結果

**這個帳號的 Anthropic 模型全部是 `INFERENCE_PROFILE`，沒有 ON_DEMAND。**
直接寫 `anthropic.claude-opus-5` 一定 403，必須加 `us.` 前綴。

**「列得出來」不等於「叫得動」**：`ListInferenceProfiles` 回 27 個，實際呼叫
只有 13 個成功。Claude 5 系列（Opus 5 / Sonnet 5 / Fable 5）與 Opus 4.7 / 4.8
**全部 403**。能用的最高階是 Opus 4.6。

GPT 同樣模式：GPT-6、GPT-5.6 全 403，只有開源權重的 `gpt-oss-20b` /
`gpt-oss-120b` 可用（需走 Converse API，不吃 Anthropic messages 格式）。

目前設定：

```
BEDROCK_MODEL=us.anthropic.claude-sonnet-4-6
AWS_DEFAULT_REGION=us-west-2
```

挑 Sonnet 4.6 的理由：挑欄位不需要 Opus，而現場延遲差得出來
（Sonnet 4.6 1107ms、Opus 4.6 1449ms、Haiku 4.5 819ms）。`us.` 也普遍比
`global.` 快——同一個 sonnet-4-6 是 1107ms vs 2509ms。

### C. 四個真模型才浮現的 bug

守門全部加在**程式層**，不是 prompt。prompt 只能勸，擋不住。

**① `two_d` 多數列被安靜丟掉**（最危險）

模型輸出 `query_type: "two_d"` 卻給了 `percent7/8/9` 三個欄位。
`compileSpec` 的 two_d 分支用的是 `series[0]`，後兩個直接丟掉。畫出來是
29 個區、數字合理、標題寫「青年人口」——**實際只有 20-24 歲那一段**。

每個欄位單獨看都合法，所以驗證、編譯、對齊三關全部放行。
→ `validateSpec` 加數列數檢查。

**② `city` 與資料表不符**

問「比較雙北各區的青年人口」，模型產出 `city=metrotaipei` 但
`table=city_age_distribution_taipei`，名稱還叫「雙北青年人口分區」。
結果是一張標著雙北、只有台北 12 區的圖。

成因是 ComponentSpec 一次只能指定一張表，跨城市表達不出來，模型只好挑一邊。
→ `validateSpec` 用 `catalog.yaml` 的 `city:` 欄位比對（那個欄位一直都在，
只是從來沒被用過）。

**③ 年份當 x 軸讓 SQL 炸掉**

`ARRAY_POSITION(varchar[], integer)` 型別不合。影響的不只一題，是**所有
時間序列**。→ `compileSpec` 統一轉 `::varchar`（官方 `two_d`／`three_d` 的
`x_axis` 契約本來就是字串）。

順帶修了年份排序：原本照人口總和排，會得到 `2004, 2005, 2003, 2006` 這種
在趨勢圖上毫無意義的順序。→ `orderProbeSQL` 依 catalog 的 `type: year` 切換。

**④ 我自己的 SigV4 簽章有 bug**

query string 寫在 `path` 裡，而 `@smithy/signature-v4` 是從 `request.query`
欄位算正規化查詢字串的。錯誤訊息是 `SignatureDoesNotMatch`——**會叫你去檢查
Secret Key，方向完全錯**。

### D. 性別維度（原本會無聲失效）

原本 `series` 只能放欄位名，表達不了「同欄位、不同篩選值」，所以問
「各區女性的青年人口」只能退回 `統計類型 = 計`，**回男女合計且不說**。

改成每個數列可以帶自己的 filter：

```json
"series": [
  {"label": "男性 20-24歲", "column": "percent7",
   "filter": {"column": "統計類型", "eq": "男"}},
  {"label": "女性 20-24歲", "column": "percent7",
   "filter": {"column": "統計類型", "eq": "女"}}
]
```

連帶改 `orderProbeSQL`：它原本假設「一個 x 對應一列」，把欄位加起來排序。
有了 per-series filter，同一個區會落在不同列（男一列、女一列），假設就破了。
→ 改成先 UNION 展開再 `GROUP BY`。
**回歸驗證：沒有 per-series filter 時，前 29 名排序與舊版逐字相同。**

**光改格式不夠。** 模型仍可能寫出標籤「女性 20-24歲」、篩選卻是 `計` 的 spec。
→ `catalog.yaml` 為 `統計類型` 補 `values: [計, 男, 女]`，`validateSpec` 拿它
比對標籤與實際篩選值。判斷依據是 catalog 資料而非硬寫「男／女」，所以任何
有列舉值的分類欄位都自動受保護。

### E. 其他改動

- **圖表型別自動修正**：模型挑了 `three_d` 不支援的 `BarChart` 時，`normalizeSpec`
  改成合法的並記一筆 `warnings`。挑哪個欄位是正確性問題，用哪種圖是呈現問題——
  後者自動修不會讓數字變錯，但不假裝沒發生
- **`stats.period` / `stats.table`**：每個生成結果都說出自己是哪一年、用哪張表
- **`catalog.js` 動態查年份範圍**：啟動時問資料庫每張表的 min/max 年份，寫進 prompt
- **`spec-prompt.js`**：把 `ALLOWED_CHARTS` 程式化注入 prompt（原本模型不知道有哪些
  圖可選，會發明不存在的 `LineChart`），並新增規則 9–13

### F. 年份不一致（新發現的陷阱，只緩解未消除）

| 資料表 | 涵蓋年份 |
|---|---|
| `city_age_distribution_newtaipei` | 2000–2023 |
| `youth_population_district_newtaipei` | 2000–2024 |
| `city_age_distribution_taipei` | 2020–2025 |

模型挑哪張表使用者看不到，於是「新北各區青年人口」回 2024 的 739,771，
「女性青年人口」卻回 2023 的數字。**兩個都對，但差 16,132。**

資料本身一致（實測：女 363,902 + 男 392,001 = 計 755,903 ✓）。

緩解不是消除，是讓它**看得見**：`stats.period` 一定回傳，年份範圍寫進 prompt。
**被問到就照實說：資料來源不同年，這是公開統計的更新節奏差異。**

### G. 版控與環境

- 建立 private repo `Applause0219/newtaipei-city-dashboard-hackathon`
- 官方 remote `origin` → 改名 `upstream`，避免誤推
- 原 clone 是 `--depth 1 --single-branch`，歷史不完整導致第一次 push 失敗，
  改用 orphan 分支建立乾淨起點
- 開 `feat/ai-component-generation` 分支，與組員重構分流

**`generate.js` 有兩個生 NUL 位元組**（在 `.join()` 的字串字面值裡，
從第一個 commit 就有）。後果是整個檔案被 `file(1)`／`grep(1)` 判定為二進位，
而帶 `-I` 的 grep 會**靜默跳過**——回報找不到，不是回報跳過。

今天因此誤判 `stats.period` 被組員覆蓋掉（實際上一直都在）。
→ 改成 `\u0000` 跳脫。行為完全相同（執行期 charCode 仍是 0），
但檔案變回純文字。

> 教訓：`grep` 回報「找不到」時，先確認它有沒有真的讀那個檔案。

---

## 下一步（依投報率排序）

### 1. 載入 75 張 youth 表並登記進 `catalog.yaml` 🔴

組員的 48 支 ETL DAG 與 migration 已經定義好 **75 張 youth 表**，涵蓋就業、
居住、教育、健康、犯罪、交通、財務。但本機 `dashboard` 資料庫只有 **1 張**，
`catalog.yaml` 只登記 3 張。

**這一步同時解鎖三件事：**
- 藍圖 §9.1 第 7 類 Cross-Dataset Pattern（目前「沒資料可跑」）
- Demo 劇本要的「人口 × 就業 × 居住」三領域
- 組件生成能回答就業／居住類問題（目前只會拒答）

缺口從「等資料」變成「載入 + 寫語意描述」——今天就能做。

### 2. 生成的組件寫回資料庫 🟡

**待決定做到哪一層：**

- **A. 只落地**：寫進 `dashboardmanager`，重整不消失，可交給主辦方灌。
  但檢索不到，demo 流程不變。工作量小。
- **B. 落地 + 本機檢索**：另外讓 `inject.js` 攔截 `/vector/component`，把官方
  檢索結果與我們生成的組件合併。能演「生成完 → 下次問就查得到」的閉環，
  **不需要架 Qdrant**（自產組件數量少，欄位比對就夠）。

現況：`inject.js` 只攔截 6 條路由，其餘轉發到 `https://citydashboard.taipei/api/v1`，
所以**本機 `dashboardmanager` 目前沒被讀過任何一次**——只做 A 的話 demo 看不出差別。

### 3. Agent 層（藍圖 §6）🟡

藍圖列了 20 個 tool，現況零個。`pipeline.js` 是寫死順序的決定性流程。

**這主要是對外講法的風險**：簡報若說「AI Agent 自主分析」，被追問「它怎麼決定
下一步」會答不出來。反過來講「決定性分析管線、同樣輸入必得同樣輸出」其實是
更強的賣點，因為可重現、可稽核。

若要真的做，建議用**已裝好的 Bedrock SDK 自己寫 tool-calling 迴圈**（約 150 行，
現有函式直接註冊成 tool），不要引入外部 agent harness——理由見下。

### 4. 提案交流講稿 🟡

還是零。

---

## 評估過但不採用

### Pi agent harness（`earendil-works/pi`）

| 項目 | 數字 |
|---|---|
| 規模 | 1,713 檔、33MB、1,398 個 `.ts` |
| `pi-agent-core` | 25,305 行 TypeScript |
| npm 解壓體積 | agent-core 3.6MB ＋ pi-ai 4.3MB ＋ chord 0.9MB |
| Bedrock 支援 | ✔ 有（`amazon-bedrock.ts`）|

**不採用，三個理由：**

1. **它是編碼用的 agent**，內建工具是檔案／bash 導向，不是領域分析。我們要的
   20 個 tool（`run_trend_analysis`、`publish_component`…）無論如何都得自己寫——
   Pi 只省掉「跑迴圈」這個最簡單的部分。
2. **它是 TypeScript**，而 `local-demo/` 刻意維持零建置步驟。引入等於在 demo 前
   增加一個建置階段和一整棵相依樹（含 `chord` 這個完整的應用組合 runtime）。
3. **時間**：9/13 13:00 截止。現有 6,569 行、73 個測試、端到端可跑。

這跟藍圖 §15 選 Python 套件是同一個判斷，理由也記在 `ai/mining.js:14-18`：

> 為了一個變點偵測多裝一個 Python runtime，等於在比賽現場多一個會壞掉的東西。

**如果比賽後要長期維護，Pi 值得重新評估**——多供應商抽象和 session 管理是真的好用。
現在不是時候。
