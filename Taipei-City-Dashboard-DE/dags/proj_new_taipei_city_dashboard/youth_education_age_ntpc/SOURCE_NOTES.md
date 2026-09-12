# 新北市十五歲以上現住人口教育程度（按年齡別、性別）

## 來源驗證

- 資料頁：https://data.ntpc.gov.tw/datasets/7854f7b9-894c-4505-b62b-a2053575b80a
- API：https://data.ntpc.gov.tw/api/datasets/7854f7b9-894c-4505-b62b-a2053575b80a/json?page={0-based}&size=5000
- 驗證日期：2026-09-06；資料頁 HTTP 200、JSON API HTTP 200；請求帶瀏覽器 User-Agent。
- 實際抓到 972 列，涵蓋 1998–2024 共 27 年；每年 12 個年齡列 × 計／男／女，單頁已包含全部資料。
- 提供機關：新北市政府主計處；資料集頁標示更新頻率為每年。

## 主要欄位說明（資料集頁原文對應）

`field1(年)`、`itemvalue2(年齡別)`、`itemvalue3(類別)`；
`itemvalue4(總計畢業)`、`itemvalue5(總計肄業)`、`itemvalue6(識字者畢業)`、
`itemvalue7(識字者肄業)`、`itemvalue8(博士畢業)`、`itemvalue9(博士肄業)`、
`itemvalue10(碩士畢業)`、`itemvalue11(碩士肄業)`、
`itemvalue12(大學_獨立學院_畢業)`、`itemvalue13(大學_獨立學院_肄業)`、
`itemvalue14(專科_二_三年制畢業)`、`itemvalue15(專科_二_三年制肄業)`、
`itemvalue16(專科_五年制_後兩年畢業)`、`itemvalue17(專科_五年制_後兩年肄業)`、
`itemvalue18(專科_五年制_前三年畢業)`、`itemvalue19(專科_五年制_前三年肄業)`、
`itemvalue20(高中畢業)`、`itemvalue21(高中肄業)`、`itemvalue22(高職畢業)`、
`itemvalue23(高職肄業)`、`itemvalue24(國_初_中畢業)`、`itemvalue25(國_初_中肄業)`、
`itemvalue26(初職畢業)`、`itemvalue27(初職肄業)`、`itemvalue28(小學畢業)`、
`itemvalue29(小學肄業)`、`itemvalue30(自修)`、`itemvalue31(不識字者)`。

程式以角色別名表接受 `itemvalueN` 與 `item valueN`；找不到欄位或別名衝突會拋錯。

## 年齡與 18–35 判定

年齡標籤為 `15歲以上總人口`、`15~19歲`、`20~24歲`、`25~29歲`、`30~34歲`、
`35~39歲`、`40~44歲`、`45~49歲`、`50~54歲`、`55~59歲`、`60~64歲`、`65歲以上`。
依契約，`15歲以上總人口` 解析成 `(15, None)`；例如 `65歲以上` 是 `(65, None)`。未知標籤直接 raise。

18–35 判定為 **apportioned**：20–24、25–29、30–34 完整涵蓋，15–19 與 35–39 跨界。
15 歲以上摘要列本身過粗，不能拿來宣稱 18–35；DAG 保留原始列，後續換算只能使用明細年齡組並標示分攤假設。沒有由教育程度、年級或其他身分推估年齡。

## 對帳與真實資料問題

- `計`、`男`、`女` 是同一來源列的三種性別口徑，不可把三類一起相加；DAG 用 `gender=total/male/female` 保留各自原值，且每個來源欄位只輸出一次。
- 每次執行都做輸入／輸出 source-cell 集合與數值對帳；任何來源 cell 遺失、重複或變更都會 raise。
- 實測發現一個來源內部不一致：1998 年 `25~29歲` 的 `itemvalue19(專科_五年制_前三年肄業)` 為 `計=250、男=0、女=98`，不是 `計=男+女`。這不是 ETL 捏造的數字；程式保留三個原值、印出警告並只 allow-list 這一筆，未來新增不一致會直接 raise。
- 每年 `15歲以上總人口／計／總計畢業` 與其餘年度中位數比較，超過 3 倍直接 raise，防止來源突然換成不同尺度。
- 所有值都是 `count`／單位「人」，不是 rate；沒有做按人口比例的 18–35 換算。

## 輸出語意

- `gender` 指教育資料中人口本人性別；教育程度指標放在 JSON `breakdown.attainment`，避免把不同教育指標誤加總。
- `area_code=65000`、`area_level=city` 代表新北市全市，可與其他青年事實表 join。
- 15 歲以上摘要指標另用 `education_attainment_summary_count`，明細年齡組用 `education_attainment_count`，避免下游把摘要和分項重複計算。

## 未解決

- 來源是年齡組，不是單齡；18–35 不能 exact。若要精準答案，需另找單齡教育人口來源，不能由本表自行拆分。
- `總計畢業`、`識字者畢業`與各教育程度欄位存在統計口徑的巢狀關係；查詢時必須依 `breakdown.attainment` 選定一個指標，不可跨指標相加。
