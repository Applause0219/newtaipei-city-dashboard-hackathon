-- dataset_info：DE 每支 DAG 執行時都會 upsert 的維運中繼資料表
--
-- 為什麼需要這份：
-- operators/common_pipeline.py 的 _create_or_update_dataset_info() docstring
-- 寫著「Create dataset_info table if not exists」，但**實作只有 UPSERT，
-- 並不建表**。而這張表不存在於 db-sample-data/dashboard-demo.sql，
-- 也不在 BE 的 GORM migration 裡——實測 dashboard、postgres、
-- dashboardmanager 三個資料庫都沒有它。
--
-- 結果是：在乾淨環境部署時，每一支 DAG 的
--     _create_or_update_dataset_info（前置 task）與
--     update_lasttime_in_data_to_dataset_info（ETL 尾端）
-- 都會因 relation "dataset_info" does not exist 而失敗。
-- 這同樣是只有實際連上 Postgres 執行才會浮現的問題。
--
-- 欄位取自 _create_or_update_dataset_info() 的 info dict（權威來源），
-- 加上程式碼註解提到但由觸發器/其他流程維護的欄位。
--
-- 型別說明：upsert 以 f"'{str(v)}'" 產生 SQL，**所有值都被當成字串**，
-- 缺值會變成字面的 'None'。因此除了明確的時間欄位之外一律用 text，
-- 以免 'None' 造成轉型錯誤。

BEGIN;

CREATE TABLE IF NOT EXISTS public.dataset_info (
    id                   text PRIMARY KEY,
    psql_table_name      text,
    name_cn              text,
    airflow_dag_id       text,
    mongo_collection     text,
    maintain_type        text,
    airflow_update_freq  text,
    source               text,
    source_type          text,
    source_department    text,
    gis_format           text,
    coordinate           text,
    is_geometry          text,
    dataset_description  text,
    etl_description      text,
    sensitivity          text,
    schedule_interval    text,
    lasttime_in_data     timestamptz,
    resource_updatetime  timestamptz,
    been_used_count      integer     NOT NULL DEFAULT 0,
    create_at            timestamptz NOT NULL DEFAULT now(),
    update_at            timestamptz,
    _mtime               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dataset_info_dag_idx
    ON public.dataset_info (airflow_dag_id);
CREATE INDEX IF NOT EXISTS dataset_info_table_idx
    ON public.dataset_info (psql_table_name);

ALTER TABLE IF EXISTS public.dataset_info OWNER TO airflow;

COMMIT;
