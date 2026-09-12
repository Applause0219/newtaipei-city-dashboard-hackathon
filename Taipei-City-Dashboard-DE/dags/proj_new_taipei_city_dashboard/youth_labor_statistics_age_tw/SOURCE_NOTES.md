# 青年（15–29 歲）勞動統計

## 來源與 API

- 官方查詢頁：[青年勞動統計查詢](https://statdb.mol.gov.tw/statiscla/webMain.aspx?sys=100&funid=singleAnyPage&titlename=%E9%9D%92%E5%B9%B4%E5%8B%9E%E5%8B%95%E7%B5%B1%E8%A8%88%E8%A9%A2&pageid=amq01)。
- SDMX metadata：[amq01/all](https://statdb.mol.gov.tw/statiscla/webMain.aspx?sdmx/amq01/all)。
- 查詢定義：[sys=212／amq01](https://statdb.mol.gov.tw/statiscla/webMain.aspx?sys=212&x=2100&funid=amq01&r=14213847)。定義回傳 `minym=6700`、`maxym`；DAG 依此動態決定歷史起點，並以 metadata 的最新月期別作為終點。
- API 查詢為匿名 GET、API-JSON 格式。每次固定一組性別與年齡，選取八項指標；共 12 個查詢，避免服務對多維度多選結果的 observation 壓平順序產生歧義。

API 的資料維度是：

- `fldid`：15 歲以上民間人口、勞動力、勞參率、就業者、就業率、失業者、失業率、非勞動力。
- `code1`：總計、男、女。
- `code2`：總計（本表青年 15–29 歲）、15–19 歲、20–24 歲、25–29 歲。

API 沒有縣市或行政區維度；`area_code=TW`、`area_level=country` 是本 DAG 對全國來源的明確標記，不是新北市資料。API 回應的 `sender` 為行政院主計總處，服務入口則是勞動部統計資料庫；兩者原樣保留於來源連結與 metadata。

## 輸出

DAG 名稱與 ready table 都是 `youth_labor_statistics_age_tw`，輸出共同 15 欄契約：

- `youth_civilian_population_count`：青年民間人口。
- `youth_labor_force_count`：青年勞動力。
- `youth_labor_force_participation_rate`：青年勞動力參與率。
- `youth_employed_count`：青年就業者。
- `youth_employment_rate`：青年就業率。
- `youth_unemployed_count`：青年失業者。
- `youth_unemployment_rate`：青年失業率。
- `youth_non_labor_force_count`：青年非勞動力。

計數單位為千人，率為百分比；期間為月資料，依官方 API 可取得的期別動態更新。`breakdown` 保存 API 查詢網址、metadata／查詢定義網址、指標／性別／年齡代碼及原始標籤。

## 驗證與解讀界線

- 嚴格驗證 API 的 SDMX 維度順序、八項指標、三性別、四年齡組及每個月的 observation 數；不同性別／年齡查詢的期別也必須完全一致。
- 數值必須為有限非負數；率值限制在 0–100。零是來源值，不會被當成缺漏補入。
- API 的月資料歷史可回到民國 67 年（1978 年），比入口目前列出的年度 PDF 更完整；目前實測最新月期別為 2026 年 7 月，實際值以每次 metadata 為準。
- `總計` 年齡列是本青年表的 15–29 歲合計，不應解讀成所有 15 歲以上人口。
- 來源年齡組不是命題的 18–35 歲。15–19、20–24、25–29 可作全國基準；不可把它們直接命名為新北市 18–35，也不可把率值乘人口比例換算。

## 與既有 DAG 的差異

`youth_labor_participation_age_tw` 使用主計總處人力資源調查資料，提供另一套較長的全國勞動力年齡序列；本 DAG 使用勞動部青年專表，補上青年就業／失業人數、就業率與性別 × 年齡的月資料。兩者不互相覆蓋，查詢端應保留來源差異。
