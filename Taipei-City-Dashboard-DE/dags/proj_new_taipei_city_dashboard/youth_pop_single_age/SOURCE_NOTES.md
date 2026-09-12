# 新北市單齡（各歲）人口統計

## 來源驗證
- URL 樣板: `https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{yyymm}`（yyymm 為民國年月，如 `11507` = 民國115年7月 = 2026-07）
- 資料集頁面: https://data.gov.tw/dataset/77132 （村里戶數、單一年齡人口（新增區域代碼），內政部戶政司）
- API 文件: https://www.ris.gov.tw/rs-opendata/api/Main/docs/v1
- 驗證時間: 2026-09-05
- curl 指令:
  ```bash
  curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36" \
    "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/11408"
  ```
- HTTP 狀態 / 回應大小: 200 / 約 9.9MB（單月、全國村里、未過濾）
- 回應樣本（前 1 筆，原始未加工，欄位極多故僅列前段）:
  ```json
  {
    "statistic_yyymm": "11408",
    "district_code": "65000010001",
    "site_id": "新北市板橋區",
    "village": "留侯里",
    "household_no": "742",
    "people_total": "1639",
    "people_total_m": "783",
    "people_total_f": "856",
    "people_age_000_m": "4",
    "people_age_000_f": "3",
    "...": "... (people_age_001_m/f 至 people_age_099_m/f, 最後為 people_age_100up_m/f) ..."
  }
  ```
  （註：Windows Git Bash 終端機顯示中文欄位值時會亂碼，此為終端機 code page 問題，非資料本身問題 — 已用 Python `requests` 讀取並寫入 UTF-8 檔案覆核，`site_id` 正確解碼為「新北市板橋區」。）

## 欄位對應
| 來源欄位 | 意義 | 對應到契約欄位 |
|---|---|---|
| `district_code`（11碼） | 村里代碼，前8碼為行政區代碼 | `area_code`（取前8碼，如 `65000010` = 板橋區） |
| `site_id` | 縣市+行政區中文名稱 | `breakdown.district_name`（追溯用） |
| `people_age_NNN_m` / `_f` | 單一歲數(000-099)男/女人口數 | `value`（依 gender 拆列），`age_lower=age_upper=NNN` |
| `people_age_100up_m` / `_f` | 100歲以上男/女人口數 | `value`；`age_lower=100, age_upper=None`，`age_band_raw="100歲以上"` |
| `statistic_yyymm` | 民國年月，用來決定該筆屬於哪一年的代表快照 | `period_start`/`period_end`（該西元年 1/1~12/31），`breakdown.source_month` |

村里資料先依「行政區代碼前8碼」groupby 加總為區級人口，再逐一年齡欄位 melt 成長格式。同時另外加總全市（`area_code=65000`, `area_level=city`）一列。

## 年齡粒度與 18–35 換算
- 來源年齡分組: **單一歲數**（0歲、1歲……99歲、100歲以上），並非5歲一組。
- 能否換算至 18–35: **exact**——不需要任何分攤/加權，直接取 age_lower=age_upper 為 18,19,...,35 的列即可，就是精確的單齡人口數。
- 本 DAG 產出的用途：作為其他青年統計 DAG（其原始來源多半只有 5 歲一組，如 15-19/20-24/...）在做組內分攤(apportionment)時的**權重來源**——例如 15-19 歲組要拆到「18、19 兩歲」時，用本表當年同區 18 歲、19 歲的單齡人口比例做加權，而非粗暴地乘 2/5。

## 時序深度
- 最早資料年月: **民國107年1月（2018-01）**。實測民國106年12月（10612）及更早月份回傳 `{"responseCode":"OD-0102-S","responseMessage":"查無資料"}`（查無資料），民國107年1月（10701）起有資料。
- 最新可得資料: 實測至 2026-09-05，最新可得為 **民國115年7月（2026-07, yyymm=11507）**；115年8月(11508)、9月(11509) 皆回「查無資料」，屬正常申報/處理延遲（約 1-2 個月）。
- 來源是完整序列還是當下快照: 來源本身**不是**單一回應即含全部歷史的 API（每次呼叫只回一個 `yyymm`），但每個過去月份的資料一旦公布即**不會再變動**，且可用 `yyymm` 參數任意指定過去月份取回——等同於「可完整重建的歷史序列」，只是需要逐月呼叫。
- 因此 `load_behavior` 選: **`replace`**。ETL 每次執行時，會依序呼叫「每年12月（民國107~114年）+ 當年最新可得月份（民國115年）」共9個時間點，重新彙整、`TRUNCATE` 後整批寫入，而非用 `current+history` 自行逐月累積。
  - 選擇理由：來源可完整回溯歷史（不是快照型資料），用 `replace` 每次重建可避免 `current+history` 長期執行下可能產生的重複列/漏月問題，資料一致性較高。
  - 取捨：每次執行仍需下載約 9 個月 × 全國村里(~10MB/月，過濾後只留新北市)的資料，運算量高於單月快照，因此 `schedule_interval` 設為每月一次、`queue` 會被 `common_pipeline._assign_queue` 判為 `heavy`（因 `dom != "*"`），符合月更資料的合理排程。
  - 若未來要延伸到「逐月」而非「逐年12月代表值」，建議改造成 `current+history`：由一支一次性 backfill DAG 先把107-01~現在全部月份灌進 history table，之後正式 DAG 只抓當月、append 進 history、replace current，避免每次全量重抓。此份 DAG 目前先以「逐年代表快照」滿足命題「時序深度」需求，量體與正確性間取一個務實折衷。

## 未解決 / 待確認
- 村里→行政區代碼前8碼的對應，是依觀察 `district_code` 結構歸納（`650000XX + 3碼村里序號`），與命題附的「新北市行政區代碼從 65000010（板橋區）起」吻合，但未逐一比對內政部行政區代碼表核實全部29區代碼；已用實際回傳資料驗證 29 個新北市行政區（`65000010` ~ `65000290`，間隔10）皆有資料，數量與新北市29個行政區一致。
- 「逐年」目前用每年12月（或當年最新月）當代表快照，非月度時間序列；如果 Chatbot / 預測模型需要月度精度，需改用 `current+history` 逐月累積（見上）。
- API 不支援伺服器端依縣市或年齡欄位過濾（`COLUMNS=` 參數實測無效，回傳大小不變），只能整包下載後在 client 端過濾 `site_id` 開頭為「新北市」，屬於來源限制非本 DAG 設計問題。
