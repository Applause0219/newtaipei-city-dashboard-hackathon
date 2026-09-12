-- 拒絕分攤「過寬」的年齡組：組寬 > 36 歲一律回 NULL（→ coverage = unavailable）
--
-- 為什麼：
-- 目標區間 18-35 只有 18 歲寬。當來源組別遠寬於目標時，
-- 「按人口權重取出其中一段」這個動作已經不是估計，而是臆測。
-- 實測資料庫內符合此情況的有 1,812 列、3 支 DAG：
--
--   youth_indigenous_education_age_ntpc  15歲以上（組寬 106）  1,176 列  比例 0.2597
--   youth_education_age_ntpc             15歲以上總人口（106）   588 列  比例 0.2595
--   youth_oas_age_structure_ntpc         15_64歲人口數（50）      24 列  比例 0.3154
--   youth_oas_age_structure_ntpc         15_64歲比率（50）        24 列  比例 0.3154
--
-- 以「15歲以上」為例：分攤等於宣稱能從 106 年跨距裡只憑人口形狀
-- 切出 18 年，並丟掉 74% 的母體。對人口本身勉強可行，
-- 對教育程度這種與年齡強相關的指標則不可信。
--
-- 另有實測佐證（見 YOUTH_SOURCE_MAP.md 第三十節）：即使是 5 歲組，
-- 用人口權重分攤「交通事故當事人」仍系統性低估 6.5-9.9%，
-- 誤差集中在跨越 18 歲行為斷點的 15-19 組。組越寬，這類偏誤只會更大。
--
-- 門檻取 36 = 目標區間寬度的兩倍。這是可辯護的分界而非任意數字：
-- 它保留 20 歲寬以內的組（25-44、30-49、30-39），
-- 排除 50 歲以上與所有開放區間（age_upper IS NULL 會被算成 120）。
--
-- 這些列**不會被刪除**——「15歲以上總人口」本身是合法且有用的數字，
-- 只是不該被當成 18-35 的估計值。改標 unavailable 讓它誠實呈現。
--
-- 維持原簽章（不加參數），避免與既有 5 引數呼叫產生多載歧義；
-- 門檻以常數內嵌並在此文件說明。

BEGIN;

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
        -- ★ 組寬超過目標區間兩倍（36 歲）：拒絕分攤，標 unavailable。
        --   開放區間（age_upper IS NULL）因為上界被算成 120，也會落在這裡。
        WHEN (SELECT hi FROM band) - (SELECT lo FROM band) + 1 > 36
            THEN NULL::numeric
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

ALTER FUNCTION public.youth_band_fraction(text, integer, text, smallint, smallint, smallint, smallint)
    OWNER TO airflow;

COMMIT;
