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

## Pi agent harness 評估（`earendil-works/pi`）

**第一次判斷不採用，理由有兩個是錯的。實跑 spike 後推翻。**

### 實測結果

寫了一個只註冊「查新北各區青年人口」單一工具的 agent，接
`us.anthropic.claude-sonnet-4-6`，問「哪三個區青年人口最多」：

```
── 完成（6929ms）──
事件序列: agent_start → turn_start → message_start → message_end
        → message_update → tool_execution_start → tool_execution_end
        → turn_end → agent_end
```

模型自己決定呼叫工具、工具打真的 PostgreSQL、回答
板橋 96,548／新莊 82,069／中和 71,597——**與直接查資料庫逐字相同**。

### 原本的理由哪裡錯了

| 原本的說法 | 實際 |
|---|---|
| TypeScript，需要建置步驟 | **錯**。npm 版出貨編譯好的 ESM JS ＋ `.d.ts`，純 Node `import` 可用 |
| 編碼用 agent，工具是檔案／bash 導向 | **錯**。`pi-agent-core` 是通用的，編碼工具在另一個 `coding-agent` 套件 |
| 時間不夠 | 這不是技術論據 |

### 真正的成本與收穫

**成本：**
- `node_modules` 110MB、55 個套件（目前除了 bedrock-sdk 幾乎零相依）
- npm 版的內建模型註冊表是**空的**（`getBuiltinModels()` 回 0），
  必須手工建 `Model` 物件。單一模型沒問題，但是個未文件化的粗糙邊緣

**收穫：**
- `beforeToolCall` 可回 `{block: true}` 擋下工具執行——與我們「先閘門後評分」
  的架構完全對得上
- 事件流就是藍圖 §25 demo 劇本要的那個漏斗畫面
- compaction、parallel/sequential 工具執行、串流部分結果
- 供應商抽象（開發期可切 Gemini，比賽用 Bedrock，不動 `provider.js`）

### 與「LLM 不碰數字」衝突嗎——不衝突

agent 迴圈決定的是**呼叫哪個工具**，不是算數字。只要每個工具本身是決定性的
（`mining.js` 現在就是），數字依然可重算、可稽核。

藍圖 §6.3「為什麼使用 Agent 而不是單一 Prompt」講的正是這件事。

### 建議做法：新增而非取代

把 `pipeline.js` 現有的各階段包成工具，**不重寫任何分析程式碼**：

- `/api/insight` → 決定性管線（可重現，用於報告）
- `/api/agent` → agent 迴圈（探索式，用於對話 demo）

兩條路共用同一批決定性函式。可重現性的故事不但沒被削弱，
反而多了一個「同一組工具、兩種使用方式」的說法。

---

## 9/12 晚間：實機測試找出的兩個問題

使用者在真機上追問「為什麼青年的Ａ1事故佔比這麼高」，回報兩件事：
組件變成全暗、文字裡的數字跟組件對不上。查下去是兩個不相干的根因。

### 1. 生成的組件過幾分鐘變成「總合 0」

`App.vue` 每 600 秒呼叫 `contentStore.updateCurrentDashboardAllChartData()`，
它對儀表板上**每一個**組件打 `GET /component/{id}/chart`。
即時生成的組件 id 是前端自己編的 `90001+`，後端沒有這筆，回 404，
接著被 catch 清成 `chart_data = []`（contentStore.js:471）。

`DistrictChart` 拿到空 series 會走「多數列」那個分支，對 41 個預設 0 做 reduce，
總合於是正好是 0，地圖每一區的顏色深度都是 `0 / highest`——全暗。

所以不是那次追問把組件弄壞的，是計時器到點，**四個組件同時**被清空。

修法：`mountGenerated` 標 `generated: true`，兩個更新迴圈都跳過。
資料本來就在 `chart_data` 裡，不需要也不能重抓。

反向對照（瀏覽器實測）：把旗標拿掉再觸發一次更新，
`chart_data` 立刻變回 `[]`；留著旗標則 23 個點、總合 401,488.5 不變。

### 2. 文字回答的數字沒有守門

`build_component` 有 8 道守門，`query_indicator` 一道都沒有。
而使用者讀的是文字，文字的數字走的是後者。

`population_count` 橫跨 2 個資料集（`youth_pop_single_age`、
`youth_oas_population_age_ntpc`）、2 個地理層級（city / district）、
3 種性別值（total / male / female）。全部不篩一起加，
分母算出 **32,376,008**——新北市實際約 400 萬，超收 8 倍。
青年人口佔比於是報成 11.7%，真值 15.0%。

> 圖表那條路擋得下來的錯，文字這條路照樣講得出口。

修法：`query_indicator` 補上與 `validateSpec` 同一套規則——
dataset_id 跨資料集時必填、area_level 預設 district、
gender 有 total 時預設 total，並把實際生效的條件寫進 `applied` 回給模型。

### 3. 附帶：查詢過程在說謊

`trace` 只記了 `indicator_id` 與 `by`，於是畫面上兩次
「查數值（A1當事者、period）」看起來一模一樣，
實際上一次篩了 15-29 歲、一次沒篩。
使用者拿到兩個不同的數字卻看不出差在哪——這條稽核軌跡等於沒有用。
改成記下所有實際生效的條件。

system prompt 也補了第三條規則：比較一定要兩邊都查，
不可引用記憶裡的數字（原本它講「青年佔人口約 15%」，那個數字**從來沒查過**，
只是剛好猜對，這比猜錯更危險）。

### 驗證

同一題「為什麼青年的Ａ1事故佔比這麼高」：

| | 修復前 | 修復後 |
|---|---|---|
| 青年佔人口 | 11.7%（沒查過、口徑錯） | 630,856 / 4,047,001 = 15.6% |
| 對得上 SQL | ✗ | ✓ |
| 生成組件 | 0 個 | 1 個（各區18項） |

測試 114/114 通過，新增 `ai/agent-tools.test.mjs` 16 項。

---

## 9/12 深夜～9/13：跨領域分析的品質整頓

### 起點：八題跨領域測試，只有兩題完全正確

自己出了八題跨領域問題，每一題的每個數字都拿 psql 直接對照。

| 題目 | 判定 | 實際情況 |
|---|---|---|
| 青年人口 vs 租金 | ✗ 方向相反 | 說「沒有明顯正相關」，實際 r=0.668 |
| 所得 vs 結婚 | ✗ 誤判無資料 | 說婚姻沒有分區資料，實際有 26,134 列 |
| 租金佔所得比 | ✗ 排名錯、漏 4 區 | 五股 35.0% 整個漏掉 |
| 生育率 vs 青年人口 | △ 誇大 | r=−0.200（很弱），說成「呈現相反的關係」 |
| 事故 vs YouBike | △ 低估＋期間標錯 | r=0.924（極強），說成「不完全一致」 |
| 青年失業率 | ✓ 正確拒答 | 只有全國層級，並主動提替代方案 |
| 青年人口流失 | △ 4/5 正確 | 第 5 名錯（新店 vs 蘆洲） |
| 離婚/結婚比 | ✓ 完全正確 | 8 列全部對上 |

**它查到的原始數字幾乎都是對的。** 錯的一律是「模型自己從那些數字再推一步」。
守門擋住了圖表的數字，沒擋住文字的推論。

### 一個反覆出現的模式

> 每次出現「模型自己再算一步」的地方就會出錯。

到目前為止抓到三種衍生維度，三種都錯過：

| 衍生維度 | 症狀 | 對策 |
|---|---|---|
| 兩個指標的關聯 | 方向估反、強弱誤判 | `correlate_indicators` |
| 兩個指標的比例 | 排名顛倒、漏掉整個區 | `compare_indicators` |
| 同指標兩個期間的差 | 只對看到的幾列相減 | `compare_periods` |

錯法完全一樣：它只對**自己列出來的那幾列**做計算，而那份清單通常是照
別的維度排的（人口大小），不是照要比較的那個量。

三個工具都共用同一個 `resolveSelector`，避免出現「一條路徑有守門、另一條沒有」。
「強／弱」這種形容詞也由程式判定，不讓模型自己定義什麼叫「明顯」。

### 其他三個根因

**`query_indicator` 沒有期間參數。** `by=district` 永遠只回最新一期卻不說是哪一期，
模型只好拿 `inspect_indicator` 的期間範圍去猜——把 2025 單年的 17,547
標成「2020–2025 累計」（真正累計是 84,584）。
查 2021 年分區資料查不到時，它去讀 `build_component` 的 5 列 preview，
然後用那 5 列排出「前 5 名」。

**跳過搜尋直接猜指標代號。** 「所得高的區結婚是不是也多」那題一次
`search_indicators` 都沒呼叫，猜了只有 city 層級的
`resident_marital_status_count` 就宣布無法比較。

**agent 每次呼叫都從零開始。** `runAgentLoop` 的 `context.messages` 寫死成 `[]`。
使用者回「好」，模型收到的只有一個孤零零的「好」，於是回了一段自我介紹。

### 修完之後

同樣八題重跑，**八題全部通過人工 SQL 對照**。平均還變快了
（q7 從 99 秒降到 45 秒）——工具算一次，勝過模型來回湊資料。

品質也出現質變。它開始講出沒教過的東西：

> 結婚人數會受人口規模影響……若要更精準應改用結婚率，
> 但目前資料庫中尚無現成的結婚率指標。

> 流失**比例**最高的反而是東北角偏遠山海區——貢寮 −30.1%、石門 −29.3%，
> 萎縮幅度超過四分之一。

這些不是話術。它能講第一句，是因為現在有一個確定的 0.641 可以拿來質疑；
能講第二句，是因為 SQL 真的把 29 個區都算完了。

### 產品面：收斂成一條路

拿掉向量檢索推薦清單。移除理由不是它不準，是它**永遠會回東西**——
問「香蕉的價格波動與月球引力的關聯」照樣回 10 個組件，分數 0.843~0.848
全部高於 0.8 門檻。一個永遠說「有」的推薦，使用者無從判斷何時該信。

三顆按鈕（幫我建立組件／深入分析／建立儀表板）一併移除——
該走哪條管線是系統的責任，不該變成使用者的選擇題。

側邊欄加了三個建議問題 chip，點了只填入輸入框不送出。

### 測試

`165` 項全數通過。新增：

- `xdomain.test.mjs` 33 項 —— 三種衍生維度各自的回歸測試
- `transcript.test.mjs` 18 項 —— Bedrock 對話角色順序的邊界

### 還沒做

- 提案講稿
- 生成的組件仍是記憶體內，重整就消失
- `/api/insight`（藍圖 §9–§12 的完整管線，約 2,400 行）沒有前端入口，
  而且還指向舊的寬表，沒接上 70 個資料集的長表

---

## 9/13 圖表型別：從兩種變成整套

起因是比對組員那條路線（`feat/youth-ai-agent`，Python + PydanticAI）之後的一個
反直覺發現：**他的 agent 能用的圖表其實比我們少**（`tools.py` 的
`_CHART_TYPES_BY_QUERY` 只有 10 種，我們的 `ALLOWED_CHARTS` 有 20 種），
但畫面上看起來豐富得多。追下去是三個不同的原因，只有一個是真的技術差距。

### 根因一：agent 從來沒看過圖表清單

```
$ grep -o "DistrictChart\|ColumnChart\|BarChart\|..." local-demo/ai/agent.js | sort | uniq -c
   1 ColumnChart
   1 DistrictChart
```

整份 agent system prompt 只出現過這兩個名字，就在那個 ComponentSpec 範例裡。
模型照著範例填，於是每一題畫出來的圖都一樣。

而把清單組成 prompt 的 `chartOptionsForPrompt()` 早就寫好了——它只被
`spec-prompt.js:111` 用到，那是**舊的一次性管線**。agent 這條路從來沒接上。
`build_component` 的工具說明也只寫「spec: ComponentSpec 物件」，沒有 schema。

這是典型的「兩條路各自演化，共用的那塊只有一邊接線」。

**修法**：`agent.js` 接上 `chartOptionsForPrompt()`，再加一段「怎麼挑圖」的
規則。規則不是憑感覺寫的，是拉官方 API 的 223 個組件統計出來的實際用法：

| query_type | 前三名 |
|---|---|
| two_d（178 次） | BarChart 63、ColumnChart 32、DistrictChart 31 |
| three_d（119 次） | ColumnChart 37、DistrictChart 20、BarPercentChart 18 |
| time（34 次） | TimelineSeparate 18、TimelineStacked 8、ColumnLine 8 |

`BarChart` 是官方最常用的一種，我們一次都沒用過。範例裡的 chart.types 也從
`["DistrictChart","ColumnChart"]` 改成 `["BarChart","DistrictChart"]`——
那題問的是「哪一區最貴」，本來就是排名問題，29 個區塗在地圖上很難比大小。

### 根因二：一條過時的規則把折線圖封死了

`spec-prompt.js` 規則 11 寫著：

> time 需要真正的日期欄位，本專案的表都沒有。

寬表時代成立。長表建好之後就不成立了：

```
$ psql -d dashboard -c "\d youth_fact"
 period_start   | date
```

規則沒跟著改，代價是**所有時間趨勢都只能畫成長條圖**，而折線圖在官方 223 個
組件裡被用了 34 次，`TimelineSeparateChart` 單獨 18 次是第二常用的圖表。
組員那邊有 `time`，我們沒有——這是三個原因裡唯一真的技術差距。

**修法**：規則改成講型別（date 可以、整數年不行），並且**改由程式擋**而不是
靠 prompt 自律。`validateSpec` 新增兩條：

- `time` 的 `x.column` 型別必須是 `date`，不是就告訴模型改用 `three_d`
- `time` 不可以設 `latest_by`——那會把整段時間收斂成一個點

`compileSpec` 加了獨立的 time 分支。它跟 three_d 有個關鍵差異：
**不需要 xOrder 補齊**。後端編譯 time 是依 `y_axis` 名稱歸戶再 append
`{x,y}`（`componentData.go:341-355`），不像 three_d 按列序對 category。
座標跟著值一起走，不可能對錯行。也因此 x 軸不轉 varchar——前端要的是
能 `new Date()` 的值。

時間格式必須是 Go 那個 `"2006-01-02T15:04:05+08:00"`：
`TimelineSeparateChart.vue:96-98` 的 `parseTime` 直接對這個格式做字串替換，
給它 `"2026-01-01"` tooltip 會顯示半截。

順手修掉 `orderProbeSQL` 只認 `year` 不認 `date` 的問題——長表的時間軸
本來會依數值大小亂排。

趨勢圖的可畫門檻也跟長條圖不同：**每個數列**至少 3 個時間點，不是全部
加起來夠就好。一個數列 8 點、另一個 1 點，總數過關，畫出來是一條線
旁邊一個孤點。

### 根因三：畫面上那些漂亮的圖根本不是 agent 畫的

組員的 commit `103f3df`「匯入 Taipei dashboard 圖表組件與範例資料」把官方
demo 資料庫拉了進來（`db-sample-data/dashboard-demo.sql` 是 655 MB 的 LFS
檔）。桑基圖、旭日圖、四分位圖那些是**預先建好的官方組件**，不是問答生成的。

這個沒什麼好修的，記下來是為了避免拿它當基準線比錯對象。

### 順帶記錄：兩條路線的思考過程差在哪

組員那邊**有**完整的 SSE 事件流（`api.py` 的 `/api/v1/agent/stream`），
ChatBox 也有「思考過程（N 步）」摺疊區。不是沒做，是標籤內容不同。
他的 `chatStore.js` 用正則從 SQL 裡抓表名：

```js
const m = ev.args.query.match(/FROM\s+([\w."]+)/i);
```

所以使用者看到 `執行 SQL → youth_fact` / `取得結果（23 筆）`。
我們看到的是 `correlate_indicators（population_count、rental_contract_rent_median）
→ r=0.665（強・正相關）n=23`。

**他的是進度條，我們的是稽核軌跡。** 他的畫面說「它在忙」，我們的說
「它問了什麼、拿回什麼」。這個差別不是工程量，是「工具切多細」的後果：
他只有 `execute_sql` 一個查詢工具，能顯示的就只有表名。

### 實測

live agent 走完整條路：

```
[板橋和新莊的青年人口，過去幾年的走勢是怎麼變的？]  5 步 / 29s
   → 板橋區 vs 新莊區青年人口逐年走勢｜time｜TimelineSeparateChart｜chartable=true
```

數字與 psql 直查逐筆一致（板橋 2018/2019/2020 = 98022 / 95450 / 92024）。

### 測試

`182` 項全數通過（原 165 + 新增 17）。新增 `charttypes.test.mjs`：

- A 組 4 項：**prompt 必須看得見圖表清單**——這是根因一的回歸測試，
  門檻訂在「至少 8 種」，退化前是 2 種
- B 組 4 項：time 的守門（x 型別、latest_by、圖表配對）
- C 組 6 項：time 的 SQL 形狀與實際資料（不補零、照時序、時間格式、
  數列點數不足時指名是哪一個）
- D 組 2 項：two_d / three_d 沒被改壞

---

## 9/13 散布圖：讓相關係數看得見

`correlate_indicators` 算出 r=0.665，但畫出來是兩排長條圖。長條圖比的是各自
的高低，看不出「配對」這件事；散布圖一個點一個行政區，離群的區一眼就看得到。
這是我們的分析強項第一次有對應的圖。

### 為什麼不走 ComponentSpec

ComponentSpec 的形狀是「一個 x 欄位 + 多個 series」，表達不出「兩個指標當
兩個座標軸」。硬塞要同時動 `validateSpec`、`compileSpec`、`orderProbeSQL`
三處，還會撞上 `generateComponent` 無條件執行的 `orderProbeSQL` 探測步驟
（bubble 沒有類別軸，探測沒有意義）。

而 `resolveSelector` 早就把需要的條件解析全做完了。所以改成獨立工具
`plot_correlation`，**逐字共用 correlate_indicators 那組 where**——
畫出來的點一定就是算 r 的那批資料，不是另外查一次可能不同的東西。

測試裡直接驗這件事：從散布圖的 23 個點自己重算一次 Pearson，
要等於工具回報的 0.665。對不上就代表兩邊查到的不是同一批。

### 三個必須踩對的前端契約

**一個行政區一個 series。** 看起來浪費（23 個 series 各一個點），但這是
BubbleChart 的硬性契約：tooltip 標題讀的是 **series 名稱**
（`BubbleChart.vue:158`、`:278`），不是點本身。做成「一個 series 裝 23 個點」
的話，每個泡泡 hover 起來標題都一樣，行政區的身分整個消失。
legend 是關掉的（`:153`），所以 23 個 series 不會洗版。

**`categories` 是陣列，`unit` 是 JSON 字串。** 元件用數字索引讀
`categories[0]/[1]/[2]`，單位讀 `parsedUnit?.x/.y/.z`——後者是
`JSON.parse(chart_config.unit)` 的結果，給普通字串會 parse 失敗退回原字串，
然後 `.x` 是 undefined，tooltip 就沒有單位。

**必須 INNER JOIN，不可以補零。** 人口有 29 區、租金只有 27 區。
`three_d` 那套 `LEFT JOIN + coalesce(m, 0)` 在長條圖上只是少一根，
在散布圖上是**造出一個 (x, 0) 的假離群點**，既壓扁 y 軸又製造不存在的相關性。
這是 three_d 契約與 bubble 契約方向相反的地方。

### 差點中的地雷

`CHARTABLE.maxSeries = 8`。bubble 是 23 個 series，如果套用 three_d 的判準
就會 `chartable: false`，然後 `agent.js` 的 `chartable !== false` 不成立，
**組件根本不會被推到前端，而且畫面上不會有任何錯誤訊息**。
`plot_correlation` 自己組 chart_data、不經過 `generateComponent`，所以繞開了。

### z 軸與兩個小修

沒有第三個維度，z 對每個泡泡都一樣。ApexCharts 算半徑是
`n = zRange / gridHeight * 16`，z 全相同時 zRange=0 → n=0 → falsy → n=1，
於是半徑等於 z 本身，再夾進 `[5, 30]`。所以 `BUBBLE_Z = 12` 直接就是像素半徑。
瀏覽器實測 23 顆圓的 `r` 屬性全部是 12，推導正確。

順手修了 `BubbleChart.vue` 兩個地方：

- z 那列加 `v-if="tooltip.categories[2]"`。泡泡大小固定時一直顯示
  「：12」是沒有意義的數字。
- x 軸刻度在資料全是整數時不顯示小數。刻度是 ApexCharts 把 `[min, max]`
  均分出來的，本來會出現「24,359.64 人」——讀起來像精確到小數點後兩位的
  統計，其實只是刻度。

### 瀏覽器實測

走完整條 UI 流程（開側邊欄 → 點建議問題 → 送出）：

```
23 個 series、23 顆半徑 12px 的泡泡
x 軸 1,283 – 116,668（人）  y 軸 4k – 28k（元/月）
沒有 Infinity / NaN，console 零錯誤
tooltip：板橋區 / 18–35 歲青年人口（人）：106062 人 / 租金中位數（元/月）：19900 元/月
```

模型的答案還自己補了一句「相關不等於因果，也可能是都市化程度這個共同因素
同時驅動兩者」——這種統計素養的警語是工具把 r 與 strength 算好之後才出現的。

### 測試

`199` 項全數通過（182 + 新增 17）。新增的都在 `xdomain.test.mjs` 的 F 組，
跟 correlate 的測試放在一起，因為要驗的正是兩者的一致性。

### 還沒做的

- 第三個維度（泡泡大小接第三個指標）。SQL 只要多一個 CTE，但參數變多、
  模型挑錯的機會也變多，先不做。
- BubbleChart 讀 `map_config.color`，其餘 21 個圖表元件都讀
  `chart_config.color`，看起來是上游的複製貼上錯誤。目前泡泡用的是
  ApexCharts 預設調色盤。不影響正確性，沒有動它。
