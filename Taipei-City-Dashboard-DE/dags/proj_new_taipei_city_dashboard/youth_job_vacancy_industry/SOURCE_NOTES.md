# 行業別職缺來源筆記

## 已驗證來源

- 官方資料集：[data.gov.tw 9857「歷年各業廠商職缺數」](https://data.gov.tw/dataset/9857)。
- 官方 XML 端點：
  `https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/230513/mp05005.xml`
- 以瀏覽器 User-Agent 實際 GET 200，回應約 94 KB，59 筆，期間 199702–202411；欄位與 scratchpad 的 `fields_dump.txt` 一致。

## 轉換與對帳

- `年月別` 是西元 `YYYYMM`，轉成 `period_type=month` 及正確起訖日，不做民國年轉換。
- 20 個行業欄位逐一輸出，`value_type=count`、`unit=個`。`-` 表示來源無數值，保留為缺值且不靜默當 0。
- 來源是全國資料，使用 `area_code=TW`、`area_level=country`；沒有年齡欄位，不使用新北市 `65000`。
- 每列都驗證「工業分項＝工業」、「服務業分項＝服務業」、「工業＋服務業＝工業及服務業」（遇缺值不把缺值冒充 0），並將所有數值來源格的筆數及總和與輸出對帳；不符直接 `raise`。
- 這裡不能把 20 欄全部相加對總計，因為工業／服務業及其分項是階層性重疊欄位；驗證的是父子層級，不是錯誤的全欄加總。

## 18–35 判定：`unavailable`

來源只有行業與職缺個數，沒有個人年齡或年齡組別。不能由行業、職缺率或青年人口比例推估 18–35；`age_lower`、`age_upper`、`age_band_raw` 全部為 `None`。

## 陷阱與觀察

- data.gov metadata 的 `ws.dgbas.gov.tw` 直接檔案在本 runner 預設 CA 不信任；程式先走正常 TLS，僅對該官方主機提供窄範圍未驗證 TLS fallback。正式 Airflow 應安裝正確 CA 後移除 fallback。
- 來源欄位有語意但仍以角色／別名表比對；缺欄位會拋錯，不依欄位順序或寫死 `field1`。
- 已知的 `statdb.mol.gov.tw` 登入頁和未帶查詢參數的 `webMain.aspx?sys=100` 不是本 DAG 的來源；曾解出 MOL `a02252` JSON 查詢，但 data.gov XML 已與本地樣本完全對上，故採欄位更直接的官方 XML。
