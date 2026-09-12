-- 從 youth_fact 捨棄「組距過粗」的年齡組
--
-- 規則：與 18-35 部分重疊、且組寬 > 36 歲（目標區間 18 歲的兩倍）的列，
-- 不進入統一事實表。2026-09-11 實測符合者 5,280 列、3 支 DAG：
--
--   youth_indigenous_education_age_ntpc  15歲以上        組寬 106  2,856 列
--   youth_education_age_ntpc             15歲以上總人口  組寬 106  2,268 列
--   youth_oas_age_structure_ntpc         15_64歲比率     組寬  50     78 列
--   youth_oas_age_structure_ntpc         15_64歲人口數   組寬  50     78 列
--
-- 這類組別只切得出「15 歲以上／以下」，對 18-35 沒有可用的解析度，
-- 留在事實表裡只會讓查詢端誤用。
--
-- 與 18-35 完全無交集的寬組（65歲以上、未滿15歲…）不受影響：
-- 它們是明確的 exact 0，對年齡結構與總計仍有用途。
--
-- 為什麼在 matview 過濾而不是 DELETE：
-- ready_data 表是 load_behavior=replace（TRUNCATE + 重新插入），
-- DELETE 撐不過下一次排程。在這裡過濾是持久的，
-- ready_data 表則保留這些列作為原始檔，可追溯。
--
-- 為什麼用 pg_get_viewdef() 而不是把 UNION 複製一份：
-- youth_fact 的 UNION（目前 71 張表）只定義在 20260910_z_youth_fact_matview.sql。
-- 若在這裡再貼一份，之後有人在 z_matview 加表，兩份定義就會分叉，
-- 而本檔排在後面執行，會把新加的表蓋掉。改為執行時讀出「當下」的定義、
-- 包一層過濾後重建，UNION 永遠只有一處、過濾條件也只有一處。
-- youth_fact_18_35 的定義同理，讀出後原樣重建。
--
-- 對既有資料庫：若之後重新套用 z_matview（例如又加了表），
-- 需要再執行一次本檔，過濾才會套回去。重複執行是安全的（只會多包一層相同條件）。
--
-- 20260911_band_span_guard.sql 的函式守門保留，作為第二道防線。

BEGIN;

DO $migration$
DECLARE
    fact_def  text;
    v1835_def text;
BEGIN
    fact_def  := regexp_replace(pg_get_viewdef('public.youth_fact'::regclass, true),       ';\s*$', '');
    v1835_def := regexp_replace(pg_get_viewdef('public.youth_fact_18_35'::regclass, true), ';\s*$', '');

    DROP VIEW public.youth_fact_18_35;
    DROP MATERIALIZED VIEW public.youth_fact;

    EXECUTE 'CREATE MATERIALIZED VIEW public.youth_fact AS '
         || 'SELECT * FROM (' || fact_def || ') u '
         || 'WHERE NOT ( '
         || '      (u.age_lower IS NOT NULL OR u.age_upper IS NOT NULL) '
         || '  AND COALESCE(u.age_lower, 0)   <= 35 '
         || '  AND COALESCE(u.age_upper, 120) >= 18 '
         || '  AND NOT (COALESCE(u.age_lower, 0) >= 18 AND COALESCE(u.age_upper, 120) <= 35) '
         || '  AND COALESCE(u.age_upper, 120) - COALESCE(u.age_lower, 0) + 1 > 36 '
         || ')';

    EXECUTE 'CREATE VIEW public.youth_fact_18_35 AS ' || v1835_def;
END
$migration$;

-- 重建 matview 會連同索引一起消失，以下重建。
--
-- ★ 唯一索引與 z_matview 版本不同，這是刻意的修正：
-- z_matview（沿用 20260910 最初版本）用 COALESCE(...) 表達式建唯一索引，
-- 檔頭並聲稱「CONCURRENTLY 需要唯一索引，已於下方建立」。
-- 但 Postgres 規定 REFRESH ... CONCURRENTLY 的唯一索引**只能用純欄位名稱**，
-- 不可為表達式索引。實測結果：
--   ERROR: cannot refresh materialized view "public.youth_fact" concurrently
--   HINT:  Create a unique index with no WHERE clause on one or more columns
-- 也就是 CONCURRENTLY 從來沒有生效過，只是一直都用一般 REFRESH 所以沒發現。
--
-- 改用純欄位 + NULLS NOT DISTINCT（PG 15+）：NULL 被視為相等，
-- 去重語意與原本的 COALESCE 版相同（仍會擋下年齡為 NULL 的重複列——
-- 這道去重先前抓到過 dropout 學期塌陷、就業結構性別錯標等真缺陷，不能弱化）。
-- 唯一差異是空字串 '' 與 NULL 不再被視為同一值，這反而更精確。
-- 實測 REFRESH MATERIALIZED VIEW CONCURRENTLY 可正常執行（約 16 秒）。
CREATE UNIQUE INDEX youth_fact_uniq_idx
    ON public.youth_fact (dataset_id, indicator_id, period_start, period_end,
                          age_lower, age_upper, age_band_raw, gender,
                          area_code, breakdown, value_type, unit)
    NULLS NOT DISTINCT;

CREATE INDEX youth_fact_indicator_period_idx
    ON public.youth_fact (indicator_id, period_start, period_end);
CREATE INDEX youth_fact_age_idx       ON public.youth_fact (age_lower, age_upper);
CREATE INDEX youth_fact_area_idx      ON public.youth_fact (area_code, area_level);
CREATE INDEX youth_fact_breakdown_idx ON public.youth_fact USING gin (breakdown);
CREATE INDEX youth_fact_dataset_idx   ON public.youth_fact (dataset_id);

ALTER MATERIALIZED VIEW public.youth_fact OWNER TO airflow;
ALTER VIEW public.youth_fact_18_35 OWNER TO airflow;

COMMIT;
