# Taipei City Dashboard 部署

本文件依照 [官方 Front-end project setup](https://citydashboard.taipei/documentation/front-end/project-setup) 的 Docker 流程，整理目前 repository 的 root stack、資料端 Airflow，以及兩者共用的 `br_dashboard` network。

## 先決條件

- Docker Desktop 已啟動，Linux containers 可正常執行。
- Docker Desktop 至少配置 6 GB memory；目前 Airflow 有多個 worker，建議 8 GB 以上。
- 不要把任何 `.env`、API key、DB password 或 Airflow Fernet key 提交到 Git。

## Root stack：DB、初始化、FE/BE

以下指令在 `docker` 目錄執行。第一次使用時先複製 `docker/.env.template` 成 `.env` 並填入必要值。

```powershell
Set-Location .\docker
Copy-Item .\.env.template .\.env

if (-not (docker network inspect br_dashboard 2>$null)) {
  docker network create --driver=bridge --subnet=192.168.128.0/24 --gateway=192.168.128.1 br_dashboard
}

docker compose -p dashboard-db --env-file .env -f docker-compose-db.yaml up -d
docker compose -p dashboard-init --env-file .env -f docker-compose-init.yaml run --rm dashboard-be-init-manager
docker compose -p dashboard-init --env-file .env -f docker-compose-init.yaml run --rm dashboard-be-init-dashboard
docker compose -p dashboard-init --env-file .env -f docker-compose-init.yaml run --rm dashboard-fe-init
docker compose -p dashboard-app --env-file .env -f docker-compose.yaml up -d --build
docker compose -p dashboard-db --env-file .env -f docker-compose-db.yaml ps
docker compose -p dashboard-app --env-file .env -f docker-compose.yaml ps
```

`dashboard-be-init-manager`、`dashboard-be-init-dashboard` 只應在全新資料庫或已確認要重建 sample data 時執行。正式資料庫請先備份並確認匯入 SQL 的影響。

root stack 的 FE 由 Vite dev server 提供，入口為 `http://localhost/`（Nginx）或 `http://localhost:8081/`（FE direct）；BE direct port 是 `8088`，Airflow develop 使用 `8080`。Compose project name 分開設定，是為了避免不同 compose 檔案共用 Docker project 時誤判 orphan container；部署時不要加未限定範圍的 `--remove-orphans`。

## Data-end：Airflow develop / sit

在 `Taipei-City-Dashboard-DE/docker/develop` 執行：

```powershell
Set-Location .\Taipei-City-Dashboard-DE\docker\develop
Copy-Item .\.env.template .\.env
# 編輯 .env，至少設定 Airflow 密碼、MATADATA_DATABASE、CELERY_RESULT_BACKEND、REDIS_CONN

docker compose -p airflow-sit --env-file .env -f docker-compose.yaml up -d --build
docker compose -p airflow-sit --env-file .env -f docker-compose.yaml ps
docker compose -p airflow-sit --env-file .env -f docker-compose.yaml exec airflow-scheduler airflow dags list-import-errors
docker compose -p airflow-sit --env-file .env -f docker-compose.yaml exec airflow-scheduler pdftotext -v
```

develop 預設連到 root stack 的 `postgres-manager` 與 `redis`，所以要先啟動 root DB stack，且兩邊必須都加入 `br_dashboard`。Airflow UI 是 `http://localhost:8080/`；若部署在反向代理下，將 `AIRFLOW_WEBSERVER_BASE_URL` 與 `AIRFLOW_HEALTHCHECK_PATH` 改成實際 prefix。

## Data-end：Airflow prod

`Taipei-City-Dashboard-DE/docker/prod/.env.template` 是 production 的設定起點。請填入外部 PostgreSQL、SMTP、穩定且只保管在 secret store 的 `AIRFLOW_FERNET_KEY`，再執行：

```bash
cd Taipei-City-Dashboard-DE/docker/prod
cp .env.template .env
docker compose -p airflow-prod --env-file .env -f docker-compose.yaml up -d --build
docker compose -p airflow-prod --env-file .env -f docker-compose.yaml ps
```

prod 自帶 `airflow-redis`，不要把它和 root stack 的 `redis` 混用。`gitsync.sh` 已固定使用 `airflow-sit` / `airflow-prod` project，更新 DAG 後會重新 build；部署失敗會回傳非零狀態並寫入 `/var/log/gitsync.log`。

## Migrations

`Taipei-City-Dashboard-DE/db_migrations` 的 SQL 不會由 Compose 自動執行。這些 migration 含有 schema/view/table 變更，應先審核順序、確認目標資料庫並完成備份，再由資料庫管理流程逐一套用；不要把它們放進 Airflow container startup 讓每次重啟自動執行。
