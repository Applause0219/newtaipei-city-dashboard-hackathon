BEGIN;

-- ============================================================================
-- 青年統計事實層
--
-- 命題（新北市青年局）指定青年 = 18 至 35 歲，依行政院《青年基本法》。
-- 但全台灣沒有任何統計單位發布這一組：各機關一律 5 歲一組
--   15-19 / 20-24 / 25-29 / 30-34 / 35-39
-- 18–35 從第一組切進去（只要 18、19）、從最後一組切出來（只要 35），
-- 兩端都落在組內。因此年齡必須存成可運算的區間，而非字串標籤，
-- 且需要一套可審計的組內分攤方法 —— 這就是本檔的目的。
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1) 資料源登錄簿
--
-- 既有的 dataset_info 記錄「這支 DAG 從哪抓、多久抓一次」，是給維運看的。
-- 這張表記錄「這份資料在青年議題上是什麼、能不能換算到 18–35、時序夠不夠做預測」，
-- 是給分析與 Chatbot 看的。兩者以 airflow_dag_id 對應，不重複造輪子。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.youth_dataset_catalog (
    dataset_id      text PRIMARY KEY,             -- 與 DAG 的 ready table 同名
    airflow_dag_id  text,                         -- → dataset_info.airflow_dag_id
    name_cn         text        NOT NULL,
    source_url      text,
    source_platform text,                         -- data.ntpc.gov.tw | stat.gov.tw | oas.bas.ntpc.gov.tw | 戶政司 …
    source_dept     text,
    themes          text[]      NOT NULL DEFAULT '{}',   -- 人口 / 就業 / 教育 / 居住 / 創業 …

    -- 年齡換算能力。決定這份資料能不能出現在 18–35 的圖上。
    age_granularity text,                         -- single_year | five_year | custom_band | none
    age_convertible text,                         -- exact | apportioned | unavailable
    age_note        text,                         -- 換算限制的白話說明，會被 Chatbot 引用

    -- 時序深度。命題要求預測「哪些領域缺工」，沒有歷史就沒有預測。
    period_type     text,                         -- year | month | quarter | academic_year
    earliest_period date,
    latest_period   date,
    is_snapshot     boolean     NOT NULL DEFAULT false,  -- true = 來源只給當下狀態，歷史須自行累積
    load_behavior   text,

    description     text,                         -- 供 Qdrant embedding，寫詳細
    relevance_score numeric,                      -- AI 產出的青年相關性 0–1，人工複核後才寫入
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN public.youth_dataset_catalog.is_snapshot IS
    '來源只回傳當下狀態而非完整序列。此類資料未及時開始累積，歷史即永久遺失。';


-- ----------------------------------------------------------------------------
-- 2) 事實表
--
-- 單一窄長表 + 一致維度。跨領域交叉（命題舉例：教育程度 × 起薪）因此
-- 只是同一張表上的一次 GROUP BY，而不是一個工程專案。
--
-- 儀表板的圖表契約只要求一段回傳 x_axis / y_axis / data 的 SQL，
-- 所以新增一張圖 = 在 query_charts 新增一列，不必新增 DAG、不必新增表。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.youth_fact (
    fact_id        bigserial PRIMARY KEY,
    dataset_id     text        NOT NULL,
    indicator_id   text        NOT NULL,   -- population_count | initial_salary_mean | job_opening_count …

    period_start   date        NOT NULL,
    period_end     date        NOT NULL,
    period_type    text        NOT NULL,

    -- 年齡存區間，不存標籤。18–35 的換算完全是區間運算。
    age_lower      smallint,               -- 含下界；NULL = 非年齡別資料
    age_upper      smallint,               -- 含上界；NULL = 無上界（「35歲以上」）
    age_band_raw   text,                   -- 來源原始標籤原封不動保留，供追溯與 Chatbot 引用

    gender         text,                   -- male | female | total
    area_code      text,                   -- 全國 TW | 新北市 65000 | 行政區 65000010 …
    area_level     text,                   -- nation | city | district | village

    breakdown      jsonb       NOT NULL DEFAULT '{}',  -- {"education":"大學","industry":"製造業"}

    value          numeric     NOT NULL,
    unit           text        NOT NULL,   -- 人 | 元/月 | % | 件 | 倍 | 戶
    value_type     text        NOT NULL,   -- count | rate | median | mean | index | ratio

    source_row_ref jsonb,                  -- {"s3_key": "...", "row_hash": "..."} 回指原始層
    ingested_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS youth_fact_indicator_period_idx
    ON public.youth_fact (indicator_id, period_start DESC);
CREATE INDEX IF NOT EXISTS youth_fact_age_idx
    ON public.youth_fact (age_lower, age_upper);
CREATE INDEX IF NOT EXISTS youth_fact_area_idx
    ON public.youth_fact (area_code, period_start DESC);
CREATE INDEX IF NOT EXISTS youth_fact_breakdown_idx
    ON public.youth_fact USING GIN (breakdown);
CREATE INDEX IF NOT EXISTS youth_fact_dataset_idx
    ON public.youth_fact (dataset_id);


-- ----------------------------------------------------------------------------
-- 3) 分攤權重：內政部戶政司單齡人口
--
-- 15-19 組要取 18、19 兩歲，直覺做法是乘 2/5。但青年人口在單一年齡上分布並不平均
-- （少子化的世代落差、大學就學造成的遷徙）。改用同區同年的單齡人口當權重，
-- 分攤比例就成為一個查得到、算得出、審計得了的數字。
--
-- 這張表本身不畫任何一張圖，但沒有它，所有 18–35 的數字都站不住。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.youth_age_weight (
    area_code   text     NOT NULL,
    period_year integer  NOT NULL,
    gender      text     NOT NULL,   -- male | female | total
    age         smallint NOT NULL,   -- 單一年齡
    population  numeric  NOT NULL,
    PRIMARY KEY (area_code, period_year, gender, age)
);


-- ----------------------------------------------------------------------------
-- 4) 組內分攤比例
--
-- 回傳來源年齡組 [p_age_lower, p_age_upper] 中應計入目標區間的比例：
--   1     完全落在目標內（中間三組）
--   0     與目標無交集
--   0–1   兩端需分攤，以單齡人口加權
--   NULL  需分攤但查無權重 → 呼叫端應標記 unavailable，不得推估
--
-- 找不到權重時刻意回 NULL 而非退回均勻分布：寧可誠實說算不出來，
-- 也不要生一個看起來合理、實際上無法辯護的數字。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.youth_band_fraction(
    p_area_code    text,
    p_year         integer,
    p_gender       text,
    p_age_lower    smallint,
    p_age_upper    smallint,
    p_target_lower smallint DEFAULT 18,
    p_target_upper smallint DEFAULT 35
) RETURNS numeric
LANGUAGE sql STABLE AS $$
    WITH band AS (
        SELECT COALESCE(p_age_lower, 0)::int   AS lo,
               COALESCE(p_age_upper, 120)::int AS hi
    ),
    tgt AS (
        SELECT GREATEST(band.lo, p_target_lower::int) AS lo,
               LEAST(band.hi, p_target_upper::int)    AS hi
        FROM band
    ),
    w AS (
        SELECT age, population
        FROM public.youth_age_weight
        WHERE area_code   = p_area_code
          AND period_year = p_year
          AND gender      = p_gender
    )
    SELECT CASE
        -- 無交集
        WHEN (SELECT lo FROM tgt) > (SELECT hi FROM tgt)
            THEN 0::numeric
        -- 整組落在目標內：不需分攤
        WHEN (SELECT lo FROM band) >= p_target_lower::int
         AND (SELECT hi FROM band) <= p_target_upper::int
            THEN 1::numeric
        -- 需分攤但無權重：呼叫端須標 unavailable
        WHEN NOT EXISTS (SELECT 1 FROM w)
            THEN NULL::numeric
        ELSE (
            SELECT SUM(population) FILTER (
                       WHERE age BETWEEN (SELECT lo FROM tgt) AND (SELECT hi FROM tgt))
                 / NULLIF(SUM(population) FILTER (
                       WHERE age BETWEEN (SELECT lo FROM band) AND (SELECT hi FROM band)), 0)
            FROM w
        )
    END;
$$;


-- ----------------------------------------------------------------------------
-- 5) 涵蓋度標籤
--
-- 每個呈現在圖上的數字都要帶一個涵蓋度，讓讀者知道它是實數還是推估。
-- 命題的預期效益之一是「培養青年資料素養」——把推估標出來，正是這件事。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.youth_band_coverage(
    p_fraction numeric
) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_fraction IS NULL      THEN 'unavailable'
        WHEN p_fraction IN (0, 1)    THEN 'exact'
        ELSE                              'apportioned'
    END;
$$;


-- ----------------------------------------------------------------------------
-- 6) 便利檢視：已換算至 18–35 的事實
--
-- 圖表 SQL 直接查這個 view，不必每次重寫分攤邏輯。
-- 注意 value 已乘上分攤比例，僅對 value_type = 'count' 有意義；
-- rate / median / mean 不可依人口比例縮放，故此處保留原值並由 coverage 標示。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.youth_fact_18_35 AS
SELECT
    f.*,
    frac.fraction,
    public.youth_band_coverage(frac.fraction) AS coverage,
    CASE
        WHEN f.value_type = 'count' THEN f.value * frac.fraction
        ELSE f.value
    END AS value_18_35
FROM public.youth_fact f
CROSS JOIN LATERAL (
    SELECT public.youth_band_fraction(
        f.area_code,
        EXTRACT(YEAR FROM f.period_start)::int,
        COALESCE(f.gender, 'total'),
        f.age_lower,
        f.age_upper
    ) AS fraction
) frac
WHERE f.age_lower IS NOT NULL OR f.age_upper IS NOT NULL;


ALTER TABLE IF EXISTS public.youth_dataset_catalog OWNER TO airflow;
ALTER TABLE IF EXISTS public.youth_fact            OWNER TO airflow;
ALTER TABLE IF EXISTS public.youth_age_weight      OWNER TO airflow;

COMMIT;
