-- 把 youth_fact 從「空的實體表」改成「涵蓋 71 張 ready_data 表的 materialized view」
--
-- 為什麼要改：
-- 20260905_youth_fact.sql 建立了 youth_fact 實體表、youth_band_fraction()
-- 與 youth_fact_18_35 視圖，但**沒有任何東西把資料放進 youth_fact**。
-- 71 支 DAG 各自寫進自己的表（ready_data_default_table = dag_id），
-- 沒有一支寫 youth_fact。實測結果是 youth_fact 恆為 0 列，
-- 於是整個 18-35 換算層（命題的核心）完全空轉。
--
-- 為什麼選 materialized view 而不是實體表 + 同步 DAG：
--   * 實體表需要額外的同步步驟，而同步會有 stale 風險——
--     DAG 更新了自己的表但同步沒跑，youth_fact 就是舊的，且看不出來。
--   * 純 VIEW 永遠即時，但每次查詢都要掃 71 張表，
--     而 youth_fact_18_35 對每一列都要 CROSS JOIN LATERAL 呼叫
--     youth_band_fraction()（內含對 youth_age_weight 的子查詢），
--     疊加起來對儀表板太慢。
--   * matview 可建索引、查詢快，且 REFRESH 是明確的動作，
--     什麼時候資料變新一目了然。
--
-- 欄位對應：
--   dataset_id  <- 來源表名（即 dag_id），取代原本需要另外維護的欄位
--   ingested_at <- 來源表的 data_time
--   fact_id     <- row_number()，僅供穩定引用，非跨次 REFRESH 穩定
--   source_row_ref <- NULL（原始層回指尚未實作）
--
-- REFRESH 方式（資料更新後執行）：
--   REFRESH MATERIALIZED VIEW CONCURRENTLY public.youth_fact;
-- CONCURRENTLY 需要唯一索引，已於下方建立。
--
-- 產生於 2026-09-11，涵蓋 71 張表。

BEGIN;

DROP VIEW IF EXISTS public.youth_fact_18_35;
DROP TABLE IF EXISTS public.youth_fact;

CREATE MATERIALIZED VIEW public.youth_fact AS
SELECT 'youth_birth_mother_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_birth_mother_age_ntpc
UNION ALL
SELECT 'youth_birth_parent_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_birth_parent_age_ntpc
UNION ALL
SELECT 'youth_bureau_activity_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_bureau_activity_ntpc
UNION ALL
SELECT 'youth_bureau_program_stats_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_bureau_program_stats_ntpc
UNION ALL
SELECT 'youth_card_consumption_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_card_consumption_age_ntpc
UNION ALL
SELECT 'youth_commute_census_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_commute_census_ntpc
UNION ALL
SELECT 'youth_crime_suspect_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_crime_suspect_age_ntpc
UNION ALL
SELECT 'youth_crime_victim_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_crime_victim_age_ntpc
UNION ALL
SELECT 'youth_cultural_volunteer_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_cultural_volunteer_age_ntpc
UNION ALL
SELECT 'youth_death_age_group_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_death_age_group_ntpc
UNION ALL
SELECT 'youth_death_rate_single_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_death_rate_single_age_ntpc
UNION ALL
SELECT 'youth_death_single_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_death_single_age_ntpc
UNION ALL
SELECT 'youth_digital_access_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_digital_access_age_tw
UNION ALL
SELECT 'youth_disability_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_disability_age_ntpc
UNION ALL
SELECT 'youth_divorce_age_structure_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_divorce_age_structure_ntpc
UNION ALL
SELECT 'youth_divorce_rate_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_divorce_rate_age_ntpc
UNION ALL
SELECT 'youth_edu_dropout_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_edu_dropout_ntpc
UNION ALL
SELECT 'youth_edu_graduates'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_edu_graduates
UNION ALL
SELECT 'youth_edu_registration_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_edu_registration_ntpc
UNION ALL
SELECT 'youth_edu_students'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_edu_students
UNION ALL
SELECT 'youth_edu_suspend_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_edu_suspend_ntpc
UNION ALL
SELECT 'youth_education_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_education_age_ntpc
UNION ALL
SELECT 'youth_election_turnout_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_election_turnout_age_ntpc
UNION ALL
SELECT 'youth_employee_salary_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_employee_salary_age_tw
UNION ALL
SELECT 'youth_employment_structure_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_employment_structure_age_tw
UNION ALL
SELECT 'youth_fertility_rate_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_fertility_rate_age_ntpc
UNION ALL
SELECT 'youth_fraud_victim_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_fraud_victim_age_ntpc
UNION ALL
SELECT 'youth_home_loan_annual_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_home_loan_annual_tw
UNION ALL
SELECT 'youth_household_income_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_household_income_tw
UNION ALL
SELECT 'youth_housing_burden_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_housing_burden_ntpc
UNION ALL
SELECT 'youth_housing_price_index_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_housing_price_index_ntpc
UNION ALL
SELECT 'youth_income_tax_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_income_tax_ntpc
UNION ALL
SELECT 'youth_indigenous_education_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_indigenous_education_age_ntpc
UNION ALL
SELECT 'youth_indigenous_population_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_indigenous_population_age_ntpc
UNION ALL
SELECT 'youth_jcic_credit_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_jcic_credit_age_tw
UNION ALL
SELECT 'youth_job_vacancy_industry'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_job_vacancy_industry
UNION ALL
SELECT 'youth_labor_participation_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_labor_participation_age_tw
UNION ALL
SELECT 'youth_labor_statistics_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_labor_statistics_age_tw
UNION ALL
SELECT 'youth_marital_status_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_marital_status_age_ntpc
UNION ALL
SELECT 'youth_marriage_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_marriage_age_ntpc
UNION ALL
SELECT 'youth_mental_health_antidepressant_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_mental_health_antidepressant_tw
UNION ALL
SELECT 'youth_minimum_wage_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_minimum_wage_tw
UNION ALL
SELECT 'youth_moi_indigenous_single_age'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_moi_indigenous_single_age
UNION ALL
SELECT 'youth_moi_population_single_age'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_moi_population_single_age
UNION ALL
SELECT 'youth_naturalized_population_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_naturalized_population_age_ntpc
UNION ALL
SELECT 'youth_oas_age_structure_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_oas_age_structure_ntpc
UNION ALL
SELECT 'youth_oas_education_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_oas_education_age_ntpc
UNION ALL
SELECT 'youth_oas_household_dynamics_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_oas_household_dynamics_ntpc
UNION ALL
SELECT 'youth_oas_marital_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_oas_marital_age_ntpc
UNION ALL
SELECT 'youth_oas_population_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_oas_population_age_ntpc
UNION ALL
SELECT 'youth_occupational_injury_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_occupational_injury_age_ntpc
UNION ALL
SELECT 'youth_pop_single_age'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_pop_single_age
UNION ALL
SELECT 'youth_rental_market_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_rental_market_ntpc
UNION ALL
SELECT 'youth_salary_initial_by_education'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_salary_initial_by_education
UNION ALL
SELECT 'youth_salary_initial_history_pdf'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_salary_initial_history_pdf
UNION ALL
SELECT 'youth_sexual_assault_perpetrator_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_sexual_assault_perpetrator_age_ntpc
UNION ALL
SELECT 'youth_sexual_assault_victim_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_sexual_assault_victim_age_ntpc
UNION ALL
SELECT 'youth_skill_certification_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_skill_certification_age_tw
UNION ALL
SELECT 'youth_sport_behavior_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_sport_behavior_age_tw
UNION ALL
SELECT 'youth_stat_area_education_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_stat_area_education_age_ntpc
UNION ALL
SELECT 'youth_stat_area_population_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_stat_area_population_age_ntpc
UNION ALL
SELECT 'youth_stat_area_school_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_stat_area_school_age_ntpc
UNION ALL
SELECT 'youth_student_loan_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_student_loan_tw
UNION ALL
SELECT 'youth_suicide_death_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_suicide_death_age_ntpc
UNION ALL
SELECT 'youth_suicide_report_old_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_suicide_report_old_ntpc
UNION ALL
SELECT 'youth_traffic_fatal_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_traffic_fatal_ntpc
UNION ALL
SELECT 'youth_traffic_injury_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_traffic_injury_ntpc
UNION ALL
SELECT 'youth_unemployment_rate_age'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_unemployment_rate_age
UNION ALL
SELECT 'youth_vocational_training_age_tw'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_vocational_training_age_tw
UNION ALL
SELECT 'youth_volunteer_age_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_volunteer_age_ntpc
UNION ALL
SELECT 'youth_youbike_station_ntpc'::text AS dataset_id,
       indicator_id, period_start, period_end, period_type,
       age_lower, age_upper, age_band_raw,
       gender, area_code, area_level,
       breakdown, value, unit, value_type,
       NULL::jsonb AS source_row_ref,
       data_time   AS ingested_at
FROM public.youth_youbike_station_ntpc;

-- CONCURRENTLY 刷新需要唯一索引
CREATE UNIQUE INDEX youth_fact_uniq_idx
    ON public.youth_fact (dataset_id, indicator_id, period_start, period_end,
                          COALESCE(age_lower, -1), COALESCE(age_upper, -1),
                          COALESCE(age_band_raw, ''), COALESCE(gender, ''),
                          COALESCE(area_code, ''), breakdown, value_type, unit);

CREATE INDEX youth_fact_indicator_period_idx
    ON public.youth_fact (indicator_id, period_start, period_end);
CREATE INDEX youth_fact_age_idx      ON public.youth_fact (age_lower, age_upper);
CREATE INDEX youth_fact_area_idx     ON public.youth_fact (area_code, area_level);
CREATE INDEX youth_fact_breakdown_idx ON public.youth_fact USING gin (breakdown);
CREATE INDEX youth_fact_dataset_idx  ON public.youth_fact (dataset_id);

ALTER MATERIALIZED VIEW public.youth_fact OWNER TO airflow;

-- 重建 18-35 視圖（定義與 20260905 版完全相同，只是來源改為 matview）
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

ALTER VIEW public.youth_fact_18_35 OWNER TO airflow;

COMMIT;
