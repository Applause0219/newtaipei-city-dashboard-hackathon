# 失業率（年齡別）來源筆記

## 已驗證來源

- 官方資料集：[data.gov.tw 6637「人力資源調查失業率」](https://data.gov.tw/dataset/6637)。
- 目前 XML 端點：
  `https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/230038/mp0101a07.xml`
- 以瀏覽器 User-Agent 實際 GET 200，回應 `text/xml`、632 筆，與 scratchpad `unemployment_rate_age.xml` 的 1978–2026 年／年月混合形狀及欄位一致。

## 轉換與對帳

- `年月別_Year_and_month` 的四碼值（例如 `1978`）轉 `period_type=year`；六碼 `YYYYMM` 或 `YYYYMmm` 轉 `period_type=month`，並計算正確月底日期。
- 年齡欄位透過 `age_15-19` 等角色標籤解析成整數區間；`age_65_over` 轉 `(65, None)`。無法解析或欄位消失會直接 `raise`。
- 年齡列的 `gender=total`，因來源只提供年齡合計率；另將來源中的總計／男／女及教育程度率分別輸出為 gender／education 指標。所有數值 `value_type=rate`、`unit=%`。
- 每次執行比對所有選定數值欄位的筆數與 Decimal 總和；不符直接失敗。`breakdown` 保存原始欄位名和原始期間。
- 來源是台灣全國，不是新北市統計，使用 `area_code=TW`、`area_level=country`，避免錯誤套用新北市 `65000`。

## 18–35 判定：`unavailable`

來源年齡組是 15–19、20–24、25–29、30–34、35–39 等，沒有精確的 18–35 組；更重要的是來源只有率，沒有各年齡組失業人數與勞動力分母。因此 ETL 原樣輸出各組 `rate`，不依人口比例縮放、不把率相加，也不在 ETL 內計算 18–35。若產品要合併率，必須另取分子、分母後加權，判定為 `unavailable`。

## 陷阱與觀察

- `data.gov.tw` 的資料集欄位與 XML 資源是合併後的活來源；不要把舊 `sys=100` 登入頁或未帶篩選的 SDMX metadata 當資料。
- `ws.dgbas.gov.tw` 在本 runner 的憑證鏈不被預設 CA 信任；程式先正常 TLS，僅對該官方主機使用窄範圍未驗證 TLS fallback，並保留 User-Agent。正式 Airflow 環境應安裝正確 CA 後移除 fallback。
- 失敗的查找路徑已留在 `YOUTH_ENDPOINT_RESEARCH_AGENT.md`；包括 table10 XLSX/ODS 的 `ws` 直接檔案在本 runner 的 TLS／gateway 問題，以及 `sys=100` 登入頁。
