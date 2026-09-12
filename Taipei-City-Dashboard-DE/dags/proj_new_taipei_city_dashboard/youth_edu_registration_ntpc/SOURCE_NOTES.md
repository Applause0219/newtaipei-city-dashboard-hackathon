# 來源與實抓驗證

- 來源：教育部大專校院校務資訊公開 UDB 靜態 CSV：`https://udb.moe.edu.tw/download/udata/static_file/114/學12-3.新生(含境外生)註冊率-以「校」統計.csv`。
  - 校籍縣市對照：`https://stats.moe.gov.tw/files/detail/{year}/{year}_student.csv`（103–113 可用；114 回傳 404）。
- 實抓日：2026-09-10。114 檔實際含 106–114 學年度；另探測 113、112、111 目錄，均 404。現行檔是全校層級，境外新生為 D、註冊率為 E（支援舊檔 E／D 標示）。
- 實抓輸出：`(186, 15)`，106–114 學年度；新北市來源列數依序為 21、21、21、21、21、21、20、20、20。
- 校籍 crosswalk：170 組學校代碼／名稱，160 組由 `student.csv` 代碼命中，10 組由教育部校籍代碼官方縣市 allow-list 補足（含馬偕醫學大學 1085），未解析數 0。**這是校址所在地，不是學生戶籍地，不可當作新北市青年的新生註冊率。** UDB 本身沒有縣市欄，也不是逐校區 GPS 判定。
- `...` 壓制值不轉成 0；實抓有 21 個境外新生 D 隱藏格，註冊率本身均可見。A–D 可見時驗證註冊率公式；來源將超過 100% 的結果封頂為 100%，此類列另記 `formula_checks_capped`。
- 輸出 `unit=%`、`value_type=rate`；註冊率不加總、不轉成年齡率。
- 來源沒有學生年齡；`age_lower`、`age_upper`、`age_band_raw` 全為 NULL，18–35 判定為 unavailable，未由學制推估。
- 驗證腳本：scratchpad 的 `verify_youth_edu_registration_ntpc.py`；實抓契約、crosswalk、壓制值、公式與尺度 guard 均通過。
