-- P0 青年資料來源的 ready_data 表
--
-- 本檔須在 20260910_z_youth_fact_matview.sql 前執行；檔名中的 p0 讓
-- 依檔名排序執行 migration 時，新表會先於 materialized view 建立。
-- 六支 DAG 都輸出同一份 15 欄 ready_data 契約。

BEGIN;

CREATE TABLE IF NOT EXISTS public.youth_card_consumption_age_ntpc (
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
    value          numeric,
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_card_consumption_age_ntpc_age_idx
    ON public.youth_card_consumption_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_card_consumption_age_ntpc_period_idx
    ON public.youth_card_consumption_age_ntpc (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_card_consumption_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_stat_area_population_age_ntpc (
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
    value          numeric,
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_stat_area_population_age_ntpc_age_idx
    ON public.youth_stat_area_population_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_stat_area_population_age_ntpc_period_idx
    ON public.youth_stat_area_population_age_ntpc (period_start, period_end);
CREATE INDEX IF NOT EXISTS youth_stat_area_population_age_ntpc_area_idx
    ON public.youth_stat_area_population_age_ntpc (area_code);
ALTER TABLE IF EXISTS public.youth_stat_area_population_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_stat_area_education_age_ntpc (
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
    value          numeric,
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_stat_area_education_age_ntpc_age_idx
    ON public.youth_stat_area_education_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_stat_area_education_age_ntpc_period_idx
    ON public.youth_stat_area_education_age_ntpc (period_start, period_end);
CREATE INDEX IF NOT EXISTS youth_stat_area_education_age_ntpc_area_idx
    ON public.youth_stat_area_education_age_ntpc (area_code);
CREATE INDEX IF NOT EXISTS youth_stat_area_education_age_ntpc_breakdown_idx
    ON public.youth_stat_area_education_age_ntpc USING gin (breakdown);
ALTER TABLE IF EXISTS public.youth_stat_area_education_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_stat_area_school_age_ntpc (
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
    value          numeric,
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_stat_area_school_age_ntpc_age_idx
    ON public.youth_stat_area_school_age_ntpc (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_stat_area_school_age_ntpc_period_idx
    ON public.youth_stat_area_school_age_ntpc (period_start, period_end);
CREATE INDEX IF NOT EXISTS youth_stat_area_school_age_ntpc_area_idx
    ON public.youth_stat_area_school_age_ntpc (area_code);
ALTER TABLE IF EXISTS public.youth_stat_area_school_age_ntpc OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_moi_population_single_age (
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
    value          numeric,
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_moi_population_single_age_age_idx
    ON public.youth_moi_population_single_age (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_moi_population_single_age_period_idx
    ON public.youth_moi_population_single_age (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_moi_population_single_age OWNER TO airflow;

CREATE TABLE IF NOT EXISTS public.youth_moi_indigenous_single_age (
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
    value          numeric,
    unit           text        NOT NULL,
    value_type     text        NOT NULL,
    data_time      timestamptz
);
CREATE INDEX IF NOT EXISTS youth_moi_indigenous_single_age_age_idx
    ON public.youth_moi_indigenous_single_age (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_moi_indigenous_single_age_period_idx
    ON public.youth_moi_indigenous_single_age (period_start, period_end);
ALTER TABLE IF EXISTS public.youth_moi_indigenous_single_age OWNER TO airflow;

COMMIT;
