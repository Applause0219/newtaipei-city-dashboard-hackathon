-- 青年資料既有 64 張 ready_data 表
-- P0 新增的 6 張表另見 20260910_p0_ready_data_tables.sql。
--
-- 為什麼需要這份：
-- utils.load_stage.save_dataframe_to_postgresql 的 `replace` 行為是
--     TRUNCATE TABLE <table>;  then  to_sql(if_exists="append")
-- **它完全不建表**。本專案 64 支 DAG 全部使用 load_behavior=replace，
-- 因此在乾淨資料庫上第一次執行時，每一支都會因
--     relation "<table>" does not exist
-- 而失敗。這個問題只有實際連上 Postgres 執行才會浮現，
-- 轉換層驗證（stub 掉資料庫寫入）永遠測不到。
--
-- 欄位型別與 20260905_youth_fact.sql 的 youth_fact 一致，
-- 差別是每張表只有 15 欄契約本體，不含 fact_id / dataset_id /
-- source_row_ref / ingested_at（那些是統一事實表才有的）。
--
-- 產生於 2026-09-10，本檔共 64 張表；連同 P0 migration 共 70 張。

BEGIN;

CREATE TABLE IF NOT EXISTS public.youth_birth_mother_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_birth_mother_age_ntpc_age_idx
    ON public.youth_birth_mother_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_birth_mother_age_ntpc_period_idx
    ON public.youth_birth_mother_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_birth_mother_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_birth_parent_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_birth_parent_age_ntpc_age_idx
    ON public.youth_birth_parent_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_birth_parent_age_ntpc_period_idx
    ON public.youth_birth_parent_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_birth_parent_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_bureau_activity_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_bureau_activity_ntpc_age_idx
    ON public.youth_bureau_activity_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_bureau_activity_ntpc_period_idx
    ON public.youth_bureau_activity_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_bureau_activity_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_bureau_program_stats_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_bureau_program_stats_ntpc_age_idx
    ON public.youth_bureau_program_stats_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_bureau_program_stats_ntpc_period_idx
    ON public.youth_bureau_program_stats_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_bureau_program_stats_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_commute_census_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_commute_census_ntpc_age_idx
    ON public.youth_commute_census_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_commute_census_ntpc_period_idx
    ON public.youth_commute_census_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_commute_census_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_crime_suspect_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_crime_suspect_age_ntpc_age_idx
    ON public.youth_crime_suspect_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_crime_suspect_age_ntpc_period_idx
    ON public.youth_crime_suspect_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_crime_suspect_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_crime_victim_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_crime_victim_age_ntpc_age_idx
    ON public.youth_crime_victim_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_crime_victim_age_ntpc_period_idx
    ON public.youth_crime_victim_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_crime_victim_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_cultural_volunteer_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_cultural_volunteer_age_ntpc_age_idx
    ON public.youth_cultural_volunteer_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_cultural_volunteer_age_ntpc_period_idx
    ON public.youth_cultural_volunteer_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_cultural_volunteer_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_death_age_group_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_death_age_group_ntpc_age_idx
    ON public.youth_death_age_group_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_death_age_group_ntpc_period_idx
    ON public.youth_death_age_group_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_death_age_group_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_death_rate_single_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_death_rate_single_age_ntpc_age_idx
    ON public.youth_death_rate_single_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_death_rate_single_age_ntpc_period_idx
    ON public.youth_death_rate_single_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_death_rate_single_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_death_single_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_death_single_age_ntpc_age_idx
    ON public.youth_death_single_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_death_single_age_ntpc_period_idx
    ON public.youth_death_single_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_death_single_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_digital_access_age_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_digital_access_age_tw_age_idx
    ON public.youth_digital_access_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_digital_access_age_tw_period_idx
    ON public.youth_digital_access_age_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_digital_access_age_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_disability_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_disability_age_ntpc_age_idx
    ON public.youth_disability_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_disability_age_ntpc_period_idx
    ON public.youth_disability_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_disability_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_divorce_age_structure_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_divorce_age_structure_ntpc_age_idx
    ON public.youth_divorce_age_structure_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_divorce_age_structure_ntpc_period_idx
    ON public.youth_divorce_age_structure_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_divorce_age_structure_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_divorce_rate_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_divorce_rate_age_ntpc_age_idx
    ON public.youth_divorce_rate_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_divorce_rate_age_ntpc_period_idx
    ON public.youth_divorce_rate_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_divorce_rate_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_edu_dropout_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_edu_dropout_ntpc_age_idx
    ON public.youth_edu_dropout_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_edu_dropout_ntpc_period_idx
    ON public.youth_edu_dropout_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_edu_dropout_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_edu_graduates (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_edu_graduates_age_idx
    ON public.youth_edu_graduates (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_edu_graduates_period_idx
    ON public.youth_edu_graduates (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_edu_graduates OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_edu_registration_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_edu_registration_ntpc_age_idx
    ON public.youth_edu_registration_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_edu_registration_ntpc_period_idx
    ON public.youth_edu_registration_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_edu_registration_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_edu_students (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_edu_students_age_idx
    ON public.youth_edu_students (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_edu_students_period_idx
    ON public.youth_edu_students (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_edu_students OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_edu_suspend_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_edu_suspend_ntpc_age_idx
    ON public.youth_edu_suspend_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_edu_suspend_ntpc_period_idx
    ON public.youth_edu_suspend_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_edu_suspend_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_education_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_education_age_ntpc_age_idx
    ON public.youth_education_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_education_age_ntpc_period_idx
    ON public.youth_education_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_education_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_election_turnout_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_election_turnout_age_ntpc_age_idx
    ON public.youth_election_turnout_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_election_turnout_age_ntpc_period_idx
    ON public.youth_election_turnout_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_election_turnout_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_employee_salary_age_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_employee_salary_age_tw_age_idx
    ON public.youth_employee_salary_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_employee_salary_age_tw_period_idx
    ON public.youth_employee_salary_age_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_employee_salary_age_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_employment_structure_age_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_employment_structure_age_tw_age_idx
    ON public.youth_employment_structure_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_employment_structure_age_tw_period_idx
    ON public.youth_employment_structure_age_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_employment_structure_age_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_fertility_rate_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_fertility_rate_age_ntpc_age_idx
    ON public.youth_fertility_rate_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_fertility_rate_age_ntpc_period_idx
    ON public.youth_fertility_rate_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_fertility_rate_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_fraud_victim_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_fraud_victim_age_ntpc_age_idx
    ON public.youth_fraud_victim_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_fraud_victim_age_ntpc_period_idx
    ON public.youth_fraud_victim_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_fraud_victim_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_home_loan_annual_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_home_loan_annual_tw_age_idx
    ON public.youth_home_loan_annual_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_home_loan_annual_tw_period_idx
    ON public.youth_home_loan_annual_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_home_loan_annual_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_household_income_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_household_income_tw_age_idx
    ON public.youth_household_income_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_household_income_tw_period_idx
    ON public.youth_household_income_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_household_income_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_housing_burden_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_housing_burden_ntpc_age_idx
    ON public.youth_housing_burden_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_housing_burden_ntpc_period_idx
    ON public.youth_housing_burden_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_housing_burden_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_housing_price_index_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_housing_price_index_ntpc_age_idx
    ON public.youth_housing_price_index_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_housing_price_index_ntpc_period_idx
    ON public.youth_housing_price_index_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_housing_price_index_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_income_tax_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_income_tax_ntpc_age_idx
    ON public.youth_income_tax_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_income_tax_ntpc_period_idx
    ON public.youth_income_tax_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_income_tax_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_indigenous_education_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_indigenous_education_age_ntpc_age_idx
    ON public.youth_indigenous_education_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_indigenous_education_age_ntpc_period_idx
    ON public.youth_indigenous_education_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_indigenous_education_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_indigenous_population_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_indigenous_population_age_ntpc_age_idx
    ON public.youth_indigenous_population_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_indigenous_population_age_ntpc_period_idx
    ON public.youth_indigenous_population_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_indigenous_population_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_jcic_credit_age_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_jcic_credit_age_tw_age_idx
    ON public.youth_jcic_credit_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_jcic_credit_age_tw_period_idx
    ON public.youth_jcic_credit_age_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_jcic_credit_age_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_job_vacancy_industry (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_job_vacancy_industry_age_idx
    ON public.youth_job_vacancy_industry (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_job_vacancy_industry_period_idx
    ON public.youth_job_vacancy_industry (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_job_vacancy_industry OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_labor_participation_age_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_labor_participation_age_tw_age_idx
    ON public.youth_labor_participation_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_labor_participation_age_tw_period_idx
    ON public.youth_labor_participation_age_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_labor_participation_age_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_marital_status_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_marital_status_age_ntpc_age_idx
    ON public.youth_marital_status_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_marital_status_age_ntpc_period_idx
    ON public.youth_marital_status_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_marital_status_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_marriage_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_marriage_age_ntpc_age_idx
    ON public.youth_marriage_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_marriage_age_ntpc_period_idx
    ON public.youth_marriage_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_marriage_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_mental_health_antidepressant_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_mental_health_antidepressant_tw_age_idx
    ON public.youth_mental_health_antidepressant_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_mental_health_antidepressant_tw_period_idx
    ON public.youth_mental_health_antidepressant_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_mental_health_antidepressant_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_minimum_wage_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_minimum_wage_tw_age_idx
    ON public.youth_minimum_wage_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_minimum_wage_tw_period_idx
    ON public.youth_minimum_wage_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_minimum_wage_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_naturalized_population_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_naturalized_population_age_ntpc_age_idx
    ON public.youth_naturalized_population_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_naturalized_population_age_ntpc_period_idx
    ON public.youth_naturalized_population_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_naturalized_population_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_oas_age_structure_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_oas_age_structure_ntpc_age_idx
    ON public.youth_oas_age_structure_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_oas_age_structure_ntpc_period_idx
    ON public.youth_oas_age_structure_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_oas_age_structure_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_oas_education_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_oas_education_age_ntpc_age_idx
    ON public.youth_oas_education_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_oas_education_age_ntpc_period_idx
    ON public.youth_oas_education_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_oas_education_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_oas_household_dynamics_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_oas_household_dynamics_ntpc_age_idx
    ON public.youth_oas_household_dynamics_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_oas_household_dynamics_ntpc_period_idx
    ON public.youth_oas_household_dynamics_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_oas_household_dynamics_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_oas_marital_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_oas_marital_age_ntpc_age_idx
    ON public.youth_oas_marital_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_oas_marital_age_ntpc_period_idx
    ON public.youth_oas_marital_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_oas_marital_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_oas_population_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_oas_population_age_ntpc_age_idx
    ON public.youth_oas_population_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_oas_population_age_ntpc_period_idx
    ON public.youth_oas_population_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_oas_population_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_occupational_injury_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_occupational_injury_age_ntpc_age_idx
    ON public.youth_occupational_injury_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_occupational_injury_age_ntpc_period_idx
    ON public.youth_occupational_injury_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_occupational_injury_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_pop_single_age (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_pop_single_age_age_idx
    ON public.youth_pop_single_age (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_pop_single_age_period_idx
    ON public.youth_pop_single_age (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_pop_single_age OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_rental_market_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_rental_market_ntpc_age_idx
    ON public.youth_rental_market_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_rental_market_ntpc_period_idx
    ON public.youth_rental_market_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_rental_market_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_salary_initial_by_education (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_salary_initial_by_education_age_idx
    ON public.youth_salary_initial_by_education (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_salary_initial_by_education_period_idx
    ON public.youth_salary_initial_by_education (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_salary_initial_by_education OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_salary_initial_history_pdf (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_salary_initial_history_pdf_age_idx
    ON public.youth_salary_initial_history_pdf (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_salary_initial_history_pdf_period_idx
    ON public.youth_salary_initial_history_pdf (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_salary_initial_history_pdf OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_sexual_assault_perpetrator_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_sexual_assault_perpetrator_age_ntpc_age_idx
    ON public.youth_sexual_assault_perpetrator_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_sexual_assault_perpetrator_age_ntpc_period_idx
    ON public.youth_sexual_assault_perpetrator_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_sexual_assault_perpetrator_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_sexual_assault_victim_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_sexual_assault_victim_age_ntpc_age_idx
    ON public.youth_sexual_assault_victim_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_sexual_assault_victim_age_ntpc_period_idx
    ON public.youth_sexual_assault_victim_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_sexual_assault_victim_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_skill_certification_age_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_skill_certification_age_tw_age_idx
    ON public.youth_skill_certification_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_skill_certification_age_tw_period_idx
    ON public.youth_skill_certification_age_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_skill_certification_age_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_sport_behavior_age_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_sport_behavior_age_tw_age_idx
    ON public.youth_sport_behavior_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_sport_behavior_age_tw_period_idx
    ON public.youth_sport_behavior_age_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_sport_behavior_age_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_student_loan_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_student_loan_tw_age_idx
    ON public.youth_student_loan_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_student_loan_tw_period_idx
    ON public.youth_student_loan_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_student_loan_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_suicide_death_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_suicide_death_age_ntpc_age_idx
    ON public.youth_suicide_death_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_suicide_death_age_ntpc_period_idx
    ON public.youth_suicide_death_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_suicide_death_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_suicide_report_old_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_suicide_report_old_ntpc_age_idx
    ON public.youth_suicide_report_old_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_suicide_report_old_ntpc_period_idx
    ON public.youth_suicide_report_old_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_suicide_report_old_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_traffic_fatal_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_traffic_fatal_ntpc_age_idx
    ON public.youth_traffic_fatal_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_traffic_fatal_ntpc_period_idx
    ON public.youth_traffic_fatal_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_traffic_fatal_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_traffic_injury_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_traffic_injury_ntpc_age_idx
    ON public.youth_traffic_injury_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_traffic_injury_ntpc_period_idx
    ON public.youth_traffic_injury_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_traffic_injury_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_unemployment_rate_age (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_unemployment_rate_age_age_idx
    ON public.youth_unemployment_rate_age (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_unemployment_rate_age_period_idx
    ON public.youth_unemployment_rate_age (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_unemployment_rate_age OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_vocational_training_age_tw (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_vocational_training_age_tw_age_idx
    ON public.youth_vocational_training_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_vocational_training_age_tw_period_idx
    ON public.youth_vocational_training_age_tw (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_vocational_training_age_tw OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_volunteer_age_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_volunteer_age_ntpc_age_idx
    ON public.youth_volunteer_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_volunteer_age_ntpc_period_idx
    ON public.youth_volunteer_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_volunteer_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_youbike_station_ntpc (
    indicator_id   text        NOT NULL,
    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,
    age_lower      smallint,
    age_upper      smallint,
    age_band_raw   text,
    gender         text,
    area_code      text,
    area_level     text,
    breakdown      jsonb       NOT NULL DEFAULT '{}',
    value          numeric,        -- 可為 NULL：來源以哨兵值（如 -1）或隱匿標記
                                   -- 表示「該格存在但數值不可得」時，DAG 會輸出 NULL
                                   -- 並在 breakdown 記 source_value_status。
                                   -- 保留該列比丟棄有意義：它記錄了缺值本身。
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_youbike_station_ntpc_age_idx
    ON public.youth_youbike_station_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_youbike_station_ntpc_period_idx
    ON public.youth_youbike_station_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_youbike_station_ntpc OWNER TO airflow;

COMMIT;
