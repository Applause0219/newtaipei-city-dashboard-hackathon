# 新北市現住人口婚姻狀況（按年齡別、性別）

## 來源驗證

- 資料頁：https://data.ntpc.gov.tw/datasets/2c5e1515-2b2f-4334-a53e-27fa4adcdbec
- API：https://data.ntpc.gov.tw/api/datasets/2c5e1515-2b2f-4334-a53e-27fa4adcdbec/json?page={0-based}&size=5000
- 驗證日期：2026-09-06；資料頁 HTTP 200、JSON API HTTP 200；請求帶瀏覽器 User-Agent。
- 實際抓到 500 列，涵蓋 2000–2024 共 25 年，單頁已包含全部資料。
- 提供機關：新北市政府主計處；資料集頁標示更新頻率為每年。

## 主要欄位說明（資料集頁原文對應）

`itemvalue1(年)`、`itemvalue2(年齡別)`；
`itemvalue3(總計)`、`itemvalue4(總計男)`、`itemvalue5(總計女)`；
`itemvalue6(未婚計)`、`itemvalue7(未婚男)`、`itemvalue8(未婚女)`；
`itemvalue9(有偶計)`、`itemvalue10(有偶男)`、`itemvalue11(有偶男相同性別)`、
`itemvalue12(有偶女)`、`itemvalue13(有偶女相同性別)`；
`itemvalue14(離婚計)`、`itemvalue15(離婚男)`、`itemvalue16(離婚男相同性別)`、
`itemvalue17(離婚女)`、`itemvalue18(離婚女相同性別)`；
`itemvalue19(喪偶計)`、`itemvalue20(喪偶男)`、`itemvalue21(喪偶男相同性別)`、
`itemvalue22(喪偶女)`、`itemvalue23(喪偶女相同性別)`。

程式用別名表同時接受 `itemvalueN` 與 `item valueN`；任何角色欄位消失或別名衝突都會拋錯。

## 年齡與 18–35 判定

來源年齡列為 `未滿15歲`、`15~19歲`、`20~24歲`、…、`95~99歲`、`100歲以上`，另有 `總計` 列。
解析結果遵守整數區間契約：例如 `未滿15歲` → `(None, 14)`、`15~19歲` → `(15, 19)`、`100歲以上` → `(100, None)`；`總計` 是 all-age aggregate，兩個界線留空但保留原始標籤。未知標籤直接 raise，不靜默略過。

18–35 判定為 **apportioned**：20–24、25–29、30–34 完整落在區間，15–19 含 15–17，35–39 含 36–39。DAG 只保留來源組別，不把跨界組別假裝成 exact，也不在 ETL 內按人口比例或均勻比例拆數。

## 欄位陷阱與對帳

- `總計`、男、女是同一批人口的不同呈現，不能把總計列和男／女列一起相加。DAG 用 `resident_marital_status_count` 與 `breakdown.marital_status` 區分狀態，且每個來源欄位只輸出一次。
- `itemvalue11/13/16/18/21/23` 是同性婚姻子項，另用 `resident_marital_status_same_sex_count`，不加回主要的男／女婚姻計數。
- 每次執行都檢查所有來源數值欄位的 key 集合、值與輸出完全對帳，並逐列驗證每個主要婚姻狀態的 `total = male + female`；不符直接 raise。
- 每年度的 `總計` 人口與其餘年度中位數比較，超過 3 倍直接 raise。這是尺度防護，不會靜默吞入疑似改變範圍的年度。
- 值是 `count`／單位「人」，不是 rate；沒有做率的 18–35 比例縮放。

## 輸出語意

- `gender` 指年齡主體本人（男、女、total），不放婚姻狀態。
- `area_code=65000`、`area_level=city`，代表新北市全市，可與既有青年事實表直接 join。
- all-age `總計` 列的 age bounds 為 `None`；這是來源明示的合計，不是由其他年齡推估。沒有年齡資訊的 aggregate 不會被拿去計算 18–35。

## 觀察與未解決

- 2000–2024 有完整長序列，適合觀察青年婚姻狀態與性別差異。
- 本來源只有年齡組，無法 exact 得到 18–35；若需更精準答案，應在查詢層搭配單齡人口或改用戶政司單齡來源，並標示分攤假設。
