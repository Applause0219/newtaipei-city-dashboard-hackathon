# 本機跑官方 Go 後端（不用 Docker）

平常做組件不需要這套——`npx vite` 直接接官方公開 API 就夠了。
這份是給「需要驗證 SQL 真的能被後端編譯」或「主辦方要看完整架構」時用的。

已在 macOS 15 (arm64) 實測跑通，全程沒有 Docker。

## 一次性安裝

```bash
brew install go postgresql@16 redis onnxruntime
brew services start postgresql@16
brew services start redis
```

## 建資料庫

```bash
createdb dashboard
createdb dashboardmanager
```

Schema 有兩種建法，**建議用官方的**：

```bash
source local-demo/backend/env.sh
cd Taipei-City-Dashboard-BE && go build -o /tmp/tcd-be . && /tmp/tcd-be migrateDB
```

`migrateDB` 用 GORM AutoMigrate 建出全部 17 張表（含 auth / chat / AI 摘要）。
另一種是我們手寫的 `local-demo/db/00_schema_dashboardmanager.sql`，只涵蓋
組件相關的 9 張表，給只有 psql、不想裝 Go 的人用。兩者實測相容——
先手動建表再跑 `migrateDB`，複合主鍵 `(index, city)` 不會被改掉。

再灌資料：

```bash
psql -d dashboard        -f db-sample-data/dashboard-demo.sql
psql -d dashboardmanager -f db-sample-data/dashboardmanager-demo.sql
psql -d dashboard        -f local-demo/db/01_data_table.sql
psql -d dashboardmanager -f local-demo/db/02_register_component.sql
psql -d dashboardmanager -f local-demo/db/03_add_to_dashboard.sql
```

## 啟動

```bash
source local-demo/backend/env.sh
/tmp/tcd-be
curl "http://127.0.0.1:8080/api/v1/component/9001/chart?city=metrotaipei"
```

前端要接本機後端的話，把 `vite.config.js` 的 proxy target 從
`https://citydashboard.taipei/api/v1` 改成 `http://127.0.0.1:8080/api/v1`。

## 實測結果

| 城市 | 結果 |
|---|---|
| `?city=taipei` | 12 區 × 3 層，392 仟人 |
| `?city=metrotaipei` | 29 區 × 3 層，735 仟人 |

各系列長度都等於 categories 數（沒有錯位），新北數值與 `chart_9001.json`
逐區完全一致。

## 為了在 macOS 跑起來改了官方兩個地方

都在 `Taipei-City-Dashboard-BE/app/models/qdrant.go`，只影響向量檢索，
且維持原本的預設行為。

**一、ONNX 函式庫路徑寫死。** 原本是
`ort.SetSharedLibraryPath("/usr/lib/libonnxruntime.so")`——Linux 專用路徑，
macOS 上是 `.dylib` 且 `/usr/lib` 受 SIP 保護寫不進去，於是必定
`log.Fatalf` 開不了機。改成讀 `ONNXRUNTIME_LIB_PATH`，沒設就用原本的預設值。

**二、缺模型檔就整台掛掉。** `InitLmSession` / `InitTokenizer` 在載不到
`/opt/lm_model/onnx-e5/` 的 e5 模型時呼叫 `log.Fatalf`。改成印警告並回 `nil`。

第二點是照官方自己的做法改的——同一支 `app.go` 裡，等時圈服務初始化失敗
是用 goroutine 加 `logs.FWarn` 不阻塞開機，只有 LM 這裡是致命的，前後不一致。
而且 `GenVector` 本來就有 `global.LMTokenizer == nil` 的檢查會回傳錯誤，
所以回 `nil` 是安全的。

代價：`POST /component/component`（向量檢索）不能用。要用的話得下載
`intfloat/multilingual-e5-base` 的 ONNX 版放到 `LM_MODEL_PATH`。

## 啟動時會看到但可以忽略的訊息

- `Transit service init failed: relation "gtfs_bundle" does not exist`
  ——等時圈服務缺 GTFS 資料表，官方本來就設計成非阻塞。
- 灌官方 demo 時的 `ERROR: role "postgres" does not exist`
  ——只是 `ALTER TABLE ... OWNER TO postgres`，資料照常進去。

## 安全提醒

`env.sh` 裡的密碼與 `JWT_SECRET` 只用於本機開發。比賽的規定是
「請務必確認未包含任何機密憑證資訊」——正式環境請換掉，並確認
`env.sh` 不會被 commit（`.gitignore` 已含 `*.env`，但這支叫 `env.sh`，
要另外確認）。
