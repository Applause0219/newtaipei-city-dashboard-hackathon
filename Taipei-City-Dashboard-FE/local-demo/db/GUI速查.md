# TablePlus 速查

## 建兩個連線

`Create a new connection` → PostgreSQL，兩個資料庫各建一筆：

| 欄位 | 值 |
|---|---|
| Host | `127.0.0.1` |
| Port | `5432` |
| User | 你的 macOS 帳號名（終端機打 `whoami`） |
| Password | 留空 |
| Database | `dashboard` 一筆、`dashboardmanager` 一筆 |

按 `Test` 應該變綠。免費版同時可開兩個連線，剛好夠。

**哪個資料庫放什麼**

- `dashboard`——實際的統計數字。我們的 `youth_population_district_newtaipei` 在這。
- `dashboardmanager`——組件與儀表板的設定。`query_charts` 在這。

## 你實際會做的四件事

**一、看有哪些表**：左側直接列出來，點一下就看到資料。

**二、找我們的組件設定**
```sql
SELECT city, query_type, source, short_desc
FROM query_charts WHERE index = 'youth_population_district';
```

**三、看某個組件的取數 SQL**（這是最常用的）
```sql
SELECT query_chart FROM query_charts
WHERE index = 'youth_population_district' AND city = 'metrotaipei';
```
把結果貼到新分頁執行，就能看到後端拿到的原始列。

**四、改設定後看效果**：改完 `query_charts` 不用重啟後端，重新打
`http://127.0.0.1:8080/api/v1/component/9001/chart?city=metrotaipei` 就是新的。

## 想抄官方寫法時

```sql
-- 看所有官方組件的取數 SQL，挑一個像的來改
SELECT c.id, qc.index, qc.city, qc.query_type, qc.query_chart
FROM query_charts qc JOIN components c ON c.index = qc.index
ORDER BY c.id;
```

## 兩個提醒

- **改 `component_charts` 會同時影響雙北**，因為那張表主鍵只有 `index`，跨城市共用。
- **`three_d` 的 SQL 一定要固定排序**，而且三個系列的行政區順序要一致，否則後端會把數值對到錯的區（不會報錯）。細節見 `README.md`。
