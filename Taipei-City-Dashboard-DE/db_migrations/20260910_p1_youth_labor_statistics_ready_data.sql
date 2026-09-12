-- 勞動部青年勞動統計（全國基準）ready_data 表。
-- 本檔名中的 p1 確保它排在 z_youth_fact_matview 前執行。

BEGIN;

CREATE TABLE IF NOT EXISTS public.youth_labor_statistics_age_tw (
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

CREATE INDEX IF NOT EXISTS youth_labor_statistics_age_tw_age_idx
    ON public.youth_labor_statistics_age_tw (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_labor_statistics_age_tw_period_idx
    ON public.youth_labor_statistics_age_tw (period_start, period_end);
CREATE INDEX IF NOT EXISTS youth_labor_statistics_age_tw_breakdown_idx
    ON public.youth_labor_statistics_age_tw USING gin (breakdown);
ALTER TABLE IF EXISTS public.youth_labor_statistics_age_tw OWNER TO airflow;

COMMIT;
