# tools/ — 產生器與驗證器

這三支不是分析程式，是**產生設定檔的工具**。改了年齡範圍或資料之後要重跑。

## 為什麼組件設定要用產生的，不是手寫

路徑 A（`chart_9001.json` 靜態檔）與路徑 B/C（資料庫 `query_charts`）
必須給出同一組數字**和同一個行政區順序**。

順序不是排版問題，是正確性問題：後端編譯 `three_d` 時按列序逐一 append，
不檢查每個數值屬於哪個分類（`componentData.go:301-325`）。
兩條路徑的順序一旦分岔，數字就會接到錯的行政區上，而且不會報錯。

**實際發生過兩次：**

1. 舊版兩邊順序就不同（三重區與中和區對調），沒人發現。
2. 產生 SQL 時陣列換行處少了逗號。SQL 規定兩個字串常值之間只有空白時
   自動接成一個，所以 `'汐止區'` 換行 `'淡水區'` 變成 `'汐止區淡水區'`。
   那四個區在陣列裡找不到，`array_position` 回 NULL 被排到最後。
   語法完全合法、psql 不報錯，靠 `verify-paths.mjs` 才抓到。

所以順序一律向資料庫查，不手寫；改完一定跑驗證。

## 使用

```bash
cd local-demo

# 1. 重生靜態圖表資料（路徑 A）
node tools/gen-chart-json.mjs

# 2. 重生組件註冊 SQL（路徑 B/C），然後套用
node tools/gen-register-sql.mjs
psql -d dashboardmanager -v ON_ERROR_STOP=1 -f db/02_register_component.sql

# 3. 驗證兩條路徑一致 —— 每次改完都要跑
node tools/verify-paths.mjs
```

注意資料庫分兩個：

| 資料庫 | 放什麼 |
|---|---|
| `dashboard` | 資料表（`youth_population_district_newtaipei` 等） |
| `dashboardmanager` | 組件設定（`components` / `query_charts` / `component_charts`） |

註冊 SQL 寫進 `dashboardmanager`，但它裡面的查詢是對 `dashboard` 執行的。

`02_register_component.sql` 現在是冪等的（`ON CONFLICT ... DO UPDATE`），
可以直接重跑；原本的版本第二次執行會撞 `query_charts_pkey`。

## 年齡範圍

目前是 **15-39**，由五個完整組距相加：

```
15-39 = percent6 + percent7 + percent8 + percent9 + percent10
      = 15-19  + 20-24  + 25-29  + 30-34  + 35-39
```

全部是官方原始數字，零內插、零拆分。這是 Exact Youth Proxy（B 級）。

先前版本用 20-24/25-29/30-34 三個組距，是 Youth Segment（C 級）——
一樣合法，但比可用範圍窄。

法定青年仍是 18-35。**不可以把 15-39 的結果寫成 18-35。**
18 與 35 都落在組距中間，要精確切出必須推估，那是明文禁止的。

改範圍時：改 `gen-chart-json.mjs` 與 `gen-register-sql.mjs` 裡的 `BINS`，
兩邊必須一致，然後重跑上面三步。
