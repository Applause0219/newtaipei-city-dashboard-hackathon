-- ============================================================================
-- 重構 youth_dataset_catalog → youth_asset_manifest
--
-- 對齊 youth_insight_agent_blueprint.md 的 AssetManifest 設計（Section 5.3–5.4）。
--
-- 主要變更：
--   1. 加入 asset_type / table_name / component_ids，支援 Component ↔ Table lineage
--   2. 以 JSONB dimensions / measures / age_schema 取代扁平欄位，
--      使 Agent 能理解每個 Dataset 的語意結構
--   3. measures 內含 aggregation_rule（blueprint Section 7.5）：
--      sum | require_denominator | require_weight | not_aggregatable
--   4. age_classification 對齊 blueprint Section 7.3（A/B/C/D）
--   5. 移除 Qdrant embedding 相關假設（blueprint Section 5.2 明確不使用）
--   6. 加入 deterministic metadata 欄位（系統自動掃描填入）
--   7. 加入 AI-assisted semantic metadata 欄位
--
-- 欄位對照（舊 → 新）：
--   dataset_id      → asset_id
--   name_cn         → title
--   themes          → domains
--   source_dept     → source_agency
--   load_behavior   → update_frequency
--   description     → summary（移除 Qdrant 用途）
--   age_granularity → 併入 age_schema JSONB
--   age_convertible → 併入 age_classification (A/B/C/D)
--   earliest_period → time_range_start
--   latest_period   → time_range_end
--   is_snapshot     → 移除（由 update_frequency + period_type 表達）
--   relevance_score → 移除（Agent 動態排序，不靜態存分數）
-- ============================================================================

BEGIN;

-- 保留舊表供回溯，不直接 DROP
ALTER TABLE IF EXISTS public.youth_dataset_catalog
    RENAME TO youth_dataset_catalog_deprecated;

CREATE TABLE IF NOT EXISTS public.youth_asset_manifest (

    -- === Identity（blueprint Section 5.4: asset_id, asset_type） ===
    asset_id        text PRIMARY KEY,
    asset_type      text NOT NULL DEFAULT 'dataset',    -- dataset | component

    -- === Human-readable（blueprint Section 5.4: title, summary） ===
    title           text NOT NULL,                       -- 中文標題
    summary         text,                                -- AI-assisted，供 Agent 搜尋與理解用

    -- === Domain（blueprint Section 5.4: domain） ===
    domains         text[] NOT NULL DEFAULT '{}',        -- employment | housing | population | education | health | …

    -- === Data Location ===
    table_name      text,                                -- e.g. public.youth_unemployment_rate_age
    component_ids   integer[] DEFAULT '{}',              -- 對應的 Taipei City Dashboard 組件 ID

    -- === Source Provenance（blueprint Section 5.4: source） ===
    source_agency    text,                               -- 資料來源機關
    source_url       text,                               -- 原始資料 URL
    source_platform  text,                               -- data.ntpc.gov.tw | stat.gov.tw | …
    update_frequency text,                               -- yearly | monthly | quarterly | daily | once

    -- === Dimensions（blueprint Section 5.4: dimensions） ===
    --
    -- Agent 需要知道哪些欄位代表時間、年齡、地理，才能正確篩選與分析。
    -- 格式範例：
    --   {"time": "period_start", "age": "age_lower/age_upper", "geography": "area_code"}
    dimensions      jsonb NOT NULL DEFAULT '{}',

    -- === Measures（blueprint Section 5.4 + 7.5: measures） ===
    --
    -- 每個 Measure 必須標註 aggregation_rule，避免 AI 錯誤聚合。
    -- 格式範例：
    --   [
    --     {"name": "unemployment_rate", "unit": "%",
    --      "value_type": "rate", "aggregation_rule": "require_denominator"},
    --     {"name": "unemployed_count", "unit": "人",
    --      "value_type": "count", "aggregation_rule": "sum"}
    --   ]
    --
    -- aggregation_rule 值域（blueprint Section 7.5）：
    --   sum                 — 人口數、件數、金額總額
    --   require_denominator — 比例 / Rate（需分子分母才可跨組聚合）
    --   require_weight      — 平均值（需加權才可跨組聚合）
    --   not_aggregatable    — 中位數、指數（不可跨年齡組聚合）
    measures        jsonb NOT NULL DEFAULT '[]',

    -- === Age Schema（blueprint Section 7.2–7.3） ===
    --
    -- 記錄此資料來源實際使用的年齡區間。
    -- 格式範例：
    --   [
    --     {"label": "20-24歲", "lower": 20, "upper": 24,
    --      "lower_inclusive": true, "upper_inclusive": true, "is_original": true},
    --     {"label": "25-29歲", "lower": 25, "upper": 29,
    --      "lower_inclusive": true, "upper_inclusive": true, "is_original": true}
    --   ]
    age_schema      jsonb NOT NULL DEFAULT '[]',

    -- Age Classification（blueprint Section 7.3）
    --   A — official_exact   : 完整 18–35
    --   B — exact_youth_proxy: 完整落在 15–40 內（如 15–39、20–39）
    --   C — youth_segment    : 青年子區段（如 20–24、25–29）
    --   D — out_of_scope     : 超出可用範圍（如 15–44、10–19）
    age_classification text CHECK (age_classification IN ('A', 'B', 'C', 'D')),
    age_note           text,                             -- 白話限制說明，報告 / Chatbot 會引用

    -- === Time Range（blueprint Section 5.4: time_range） ===
    time_range_start text,                               -- e.g. "2018"
    time_range_end   text,                               -- e.g. "2025"
    period_type      text,                               -- year | month | quarter | academic_year

    -- === Deterministic Metadata（blueprint Section 5.3 — 系統自動掃描填入） ===
    column_names     text[],                             -- 實際資料表的所有欄位名稱
    sql_data_types   jsonb,                              -- {"column_name": "smallint", ...}
    row_count        integer,
    null_ratio       jsonb,                              -- {"column_name": 0.05, ...}
    distinct_counts  jsonb,                              -- {"column_name": 42, ...}
    sample_values    jsonb,                              -- {"column_name": ["val1", "val2"], ...}

    -- === AI-assisted Semantic Metadata（blueprint Section 5.3） ===
    likely_age_columns        text[],                    -- AI 判斷的年齡相關欄位
    time_dimension            text,                      -- AI 判斷的時間維度欄位
    geographic_dimension      text,                      -- AI 判斷的地理維度欄位
    metric_definitions        jsonb,                     -- [{"name": "...", "definition": "...", "denominator": "..."}]
    possible_analysis_use_cases text[],                  -- AI 建議的分析用途

    -- === Lineage ===
    airflow_dag_id  text,                                -- → dataset_info.airflow_dag_id

    -- === Housekeeping ===
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 搜尋索引：Agent 透過 domain / age_classification / period_type 篩選候選 Dataset
CREATE INDEX youth_asset_manifest_domains_idx
    ON public.youth_asset_manifest USING GIN (domains);
CREATE INDEX youth_asset_manifest_age_class_idx
    ON public.youth_asset_manifest (age_classification);
CREATE INDEX youth_asset_manifest_period_type_idx
    ON public.youth_asset_manifest (period_type);

-- 全文搜索：blueprint Section 5.5 建議用 SQL full-text / keyword search
CREATE INDEX youth_asset_manifest_title_summary_idx
    ON public.youth_asset_manifest
    USING GIN (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(summary, '')));

ALTER TABLE public.youth_asset_manifest OWNER TO airflow;

COMMENT ON TABLE public.youth_asset_manifest IS
    '對齊 youth_insight_agent_blueprint.md Section 5.3–5.4 的 AssetManifest 設計。'
    ' Agent 搜尋可分析 Dataset 的主要入口。';

COMMIT;
