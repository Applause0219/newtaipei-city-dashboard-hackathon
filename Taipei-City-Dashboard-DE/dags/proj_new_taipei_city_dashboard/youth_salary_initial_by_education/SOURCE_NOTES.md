# 初任人員薪資（按教育程度別）來源筆記

## 已驗證來源

- 官方資料集：[data.gov.tw 6647「初任人員薪資」](https://data.gov.tw/dataset/6647)。
- 結構化平均數 JSON：
  `https://apiservice.mol.gov.tw/OdService/download/A17000000J-020066-J3o`
- 結構化分布 JSON：
  `https://apiservice.mol.gov.tw/OdService/download/A17000000J-020066-aj4`
- 兩個端點均已用瀏覽器 User-Agent 實際 GET 200，且欄位與 scratchpad 的 114 年樣本一致：平均數 228 筆、中位數 57 筆。

## 轉換與對帳

- `年度` 是民國年，轉成西元年度的 `period_start=YYYY-01-01`、`period_end=YYYY-12-31`，`period_type=year`。
- 平均數資源輸出兩種事實：`薪資平均數（千元）` 為 `mean`，`占該教育程度別初任人員人數結構比（%）` 為 `rate`。
- 分布資源只輸出 `薪資中位數（千元）` 為 `median`。D1/Q1/Q3/D9 沒有對應的契約 `value_type`，因此保留在 `breakdown`，不把分位數錯標成 `median`。
- `特性別` 的男性／女性列才將 `gender` 設為 `male`／`female`；其他列為 `total`。資料主體仍是初任受僱人員。
- 全國來源不是新北市行政區資料，使用 `area_code=TW`、`area_level=country`，不冒充新北市 `65000`，避免錯誤 join。
- 每次執行都比對選定來源數值的筆數及 Decimal 總和與輸出；不符直接 `raise`。所有欄位以別名／角色尋找，欄位消失時直接失敗。

## 18–35 判定：`unavailable`

來源沒有個人年齡欄位。「初任人員」是統計定義，不是年齡；不能推估 18–35，也不能把教育程度當年齡。`age_lower`、`age_upper`、`age_band_raw` 全部為 `None`。

## 多年度狀態與陷阱

- 目前活的 OAS 結構化資源只有 114 年；不能對同一 resource id 加年度參數假造 110–113 年。
- 勞動部 110–114 年官方發布頁及 PDF 確實存在，但 PDF 是非結構化報告，且不同年度表格內容／欄位範圍不等於目前 JSON 資源；本 DAG 尚未把 PDF 混入，時間範圍因此只有 2025 年。
- CSV 端點 `...-SCO` 也已確認可下載；實作選 JSON，避免手工 split 造成含逗號數字錯位。數字解析仍明確移除千分位逗號。
- `psdn`／`psweb` 是舊 WebForms／登入或查詢外殼，沒有找到與目前 114 JSON 相同的歷年結構化端點；`/psdn/Home/DataDownload` 已確認 404，未作為來源。
