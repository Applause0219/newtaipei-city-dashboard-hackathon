# OAS 11842 戶籍動態來源筆記

## 實測來源與維度

- 導覽頁：StatFile2.aspx?Mode=3&CateNo=121&ParentNo=null12102。
- EVENTTARGET=lbtGUID、EVENTARGUMENT=11842 的 WebForms postback
  成功進入報表；回應約 7,472,333 bytes。
- 實測只有一個非空複分類：lisComplex1，前綴 P0202，共 52 個
  指標。日期選項共 28 個，1998/01/01 至 2025/01/01。
- 52 個指標包含遷入/遷出總數、男女、來源/去向分項、同區地址變更、
  出生/死亡男女、結婚/離婚對數，以及八個率。人數單位為「人」，
  婚姻登記單位為「對」，率單位為「‰」。

## 查詢與解析判斷

- 每次查詢都以同一個 requests.Session 先 GET 導覽頁，再 POST 報表頁。
  axCycle_Server、axEffect_Server、axMode_Server、
  axSubjectNo_Server 從報表頁的 hidden fields 動態讀取。
- axX 為 [Measures];P0202，axCode 為 52 個指標碼加
  010000065000。ShowQuery 使用 percent-encoded URL、瀏覽器
  User-Agent，並帶導覽頁 Referer；缺少動態欄位或 Referer 時曾得到 HTTP
  500，因此程式不硬編碼伺服器欄位。
- 回應是 HTML，不是 JSON；最大資料表每年應有 52 格。第一格會把年度、
  地區與第一個值黏在一起，解析器只取第一格結尾的數字。逗號會移除，
  -/— 依來源交叉表語意視為零，空值、...、未知格式則直接失敗。

## 事實表與 18-35 判定

- 原始完整交叉表是 28 × 52 = 1,456 格，涵蓋 1998-2025，area_code=65000
  且 area_level=city。1999 與 2000 整年度不寫入：來源分別回傳
  死亡人數_女=59,806 與 67,010，但死亡總數只有 14,609 與 15,065，
  男女細項不可能同時成立。2018 也不寫入：遷入總數比 14 個分項加總少
  1。三年均列在 KNOWN_BAD_YEARS 並記錄理由，因此目前有效輸出為
  25 年、1,300 列；不是靜默修正來源數字。
  其餘完整交叉表未先濾除零值，對帳先確認輸入 source cell 集合、總值、
  性別及分項都一致。
- 本表沒有年齡維度。age_lower、age_upper、age_band_raw 全部為 NULL，
  並在 breakdown 標示 age_scope=unavailable。青年 18-35
  判定為 unavailable；不以遷入/遷出總量或率按人口比例推估。
- 人數、對數、率分用不同 indicator_id 與 unit，避免串接時把人數、
  對數或千分率混成同一指標。

## 對帳與防護

- 每年斷言 52 格；出生與死亡總數須符合男女合計，遷入與遷出總數須符合
  14 個來源/去向分項，並檢查自然增加率與社會增加率公式。遷入/遷出
  的性別標籤在早期年度是來源保留欄位且值為 0，未把它誤當成可用的
  男女分拆。
- 對遷入、遷出、出生、死亡四個總數做年度尺度防護：與其餘年度中位數
  相差超過 3 倍即失敗。1999/2000 的來源性別異常與 2018 的分項差 1
  均以明確理由排除，不以任何估算值補回。
- verify 會重新實抓 28 個年度，輸出 shape、15 欄 non-null、對帳結果、
  2025 出生/死亡交叉比對數字與 18-35 判定；DAG 與 verify 共用本模組的
  _fetch_records、drop_scope_anomalies、transform_records、_reconcile。

## 跨來源結果

- verify 實際載入既有 youth_birth_mother_age_ntpc（RIS ODRP056，按發生日期）
  比對 2019-2025。兩邊不是同一口徑：例如 2019 OAS 出生 28,387、RIS
  27,965；2025 OAS 15,337、RIS 15,141。這是 OAS 戶籍動態與 RIS
  按發生日期的定義差異，verify 會列出差異但不互相覆寫。
- verify 也載入既有 youth_death_single_age_ntpc（RIS ODRP031）比對
  2019-2025；2019-2023 與 2025 一致，2024 為 OAS 30,213、RIS 26,160。
  2024 差異保留為來源診斷，不因外部口徑差異修改 OAS，也未把它混入
  OAS 內部對帳或新增估算。
