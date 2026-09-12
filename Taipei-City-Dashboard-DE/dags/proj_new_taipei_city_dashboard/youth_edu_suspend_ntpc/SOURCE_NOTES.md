# 來源與實抓驗證

- 來源：教育部大專校院校務資訊公開 UDB 靜態 CSV。
  - 現行：`https://udb.moe.edu.tw/download/udata/static_file/114/學13-2.於學年底處於休學狀態之人數-以「校(含學制班別)」統計(111學年度起).csv`
  - 歷史：`https://udb.moe.edu.tw/download/udata/static_file/un_update/學13-2.於學年底處於休學狀態之人數-以「校(含學制班別)」統計(110學年度以前).csv`
  - 校籍縣市對照：`https://stats.moe.gov.tw/files/detail/{year}/{year}_student.csv`（103–113 可用；114 回傳 404）。
- 實抓日：2026-09-10。114 檔實際含 111–113 學年度；歷史檔含 105–110。另探測 113、112、111 目錄，均 404。
- 實抓輸出：`(26258, 15)`，105–113 學年度；新北市來源列數依序為 176、178、176、178、187、185、182、188、186。
- 校籍 crosswalk：172 組學校代碼／名稱，164 組由 `student.csv` 代碼命中，8 組由教育部校籍代碼官方縣市 allow-list 補足，未解析數 0。**這是校址所在地，不是學生戶籍地，不可當作新北市青年的休退學率。** UDB 本身沒有縣市欄，也不是逐校區 GPS 判定。
- `...` 壓制值不轉成 0；實抓隱藏值分布於歷史原因欄，總數 1,586（逐學年度 attrs 另行記錄）。輸出不含隱藏格，並在 attrs 記錄欄位統計。
- 111–113 的學生自請／學校勒令原因、小計、總計均逐列相加檢核；歷史資料若原因格被壓制，該列明確記為 hidden skip，不以 0 補值。
- 來源沒有學生年齡；`age_lower`、`age_upper`、`age_band_raw` 全為 NULL，18–35 判定為 unavailable，未由學制推估。
- 驗證腳本：scratchpad 的 `verify_youth_edu_suspend_ntpc.py`；實抓契約、crosswalk、壓制值、對帳與尺度 guard 均通過。
