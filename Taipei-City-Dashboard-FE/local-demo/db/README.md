# 青年人口分區：資料庫版本

這裡是可以直接灌進臺北城市儀表板後端的 SQL。
專為**主辦方接手**準備——不需要我們的 `inject.js`，也不需要任何前端修改。

## 灌進去

```bash
createdb dashboard; createdb dashboardmanager

# 0. dashboardmanager 的資料表定義
#    官方 db-sample-data/dashboardmanager-demo.sql 只有 COPY 資料、
#    一個 CREATE TABLE 都沒有，不先跑這支會全部失敗。
psql -d dashboardmanager -f 00_schema_dashboardmanager.sql

# （可選）官方 demo 資料，讓畫面上有其他組件對照
psql -d dashboard        -f ../../../db-sample-data/dashboard-demo.sql
psql -d dashboardmanager -f ../../../db-sample-data/dashboardmanager-demo.sql

# 1. 資料表（2,250 筆，2000–2024 年）→ dashboard
psql -d dashboard        -f 01_data_table.sql

# 2. 組件註冊（components / query_charts ×2 / component_charts）→ dashboardmanager
psql -d dashboardmanager -f 02_register_component.sql

# 3. 掛上儀表板
psql -d dashboardmanager -f 03_add_to_dashboard.sql
```

灌官方 demo 時會看到 `ERROR: role "postgres" does not exist`——那只是
`ALTER TABLE ... OWNER TO postgres`，資料本身照常進去，可以忽略。

## 設計說明

**照官方模式做，沒有自創結構。**

- 資料表 `youth_population_district_newtaipei` 的欄位定義**與官方
  `city_age_distribution_newtaipei` 完全一致**（含 `percent25/27/29-33` 為 `real`，
  其餘 `integer`，以及 `年份 / 區域別 / 統計類型` 三個中文欄位）
- 取數不是預存 JSON，而是 `query_charts.query_chart` 欄位裡的一段 SQL，
  跟官方其他組件（如 `aging_kpi`）一樣即時查詢
- `component_charts` 只有一筆，跨城市共用——這是官方的設計，
  改雙北的圖表樣式，台北會同步改變

## 已驗證

在**真的 PostgreSQL 16** 上跑過完整流程：建庫 → 灌 schema → 灌官方 demo →
灌本組件 → 從 `query_charts.query_chart` 欄位取出 SQL 執行 → 模擬後端的
`three_d` 編譯（依 `componentData.go:301-325` 的邏輯）。

| 城市 | 結果 |
|---|---|
| metrotaipei | 87 列 = 29 區 × 3 層，合計 735 仟人 |
| taipei | 36 列 = 12 區 × 3 層，合計 392 仟人 |

兩者的三個系列 x_axis 順序皆一致（不會錯位），新北的數值與
`chart_9001.json` 逐區完全相同。

## 已知限制

**年齡範圍是 20-34，不是法定的 18-35。** 公開統計採 5 歲組距，
18 與 35 都落在組距中間，無法直接切出。這個限制寫在組件的 `long_desc` 裡，
使用者點「組件資訊」看得到。

## 台北資料（已補實，不再是佔位）

台北那筆用的是官方 `db-sample-data/dashboard-demo.sql` 就附的
`city_age_distribution_taipei`。實測該表與新北那張**結構完全相同**
（`percent7/8/9` = 20-24/25-29/30-34，統計類型 計/男/女），
2020–2025 年、12 個行政區。

唯一要注意的差異：**台北的合計列叫「總計」，新北叫「新北市」**，
所以兩段 SQL 的 `WHERE ... <> ...` 條件不同。

## 欄位對照

| 欄位 | 內容 |
|---|---|
| `percent7` | 20-24 歲人口數 |
| `percent8` | 25-29 歲人口數 |
| `percent9` | 30-34 歲人口數 |
| `年份` | 西元年（2000–2024） |
| `區域別` | 新北市 + 29 個行政區 |
| `統計類型` | 計 / 男 / 女（查詢只取「計」） |

## 資料來源

新北市政府資料開放平臺「現住人口之年齡分配」
新北市政府主計處，每月檢查、每年更新
https://data.ntpc.gov.tw/datasets/8308ab58-62d1-424e-8314-24b65b7ab492
