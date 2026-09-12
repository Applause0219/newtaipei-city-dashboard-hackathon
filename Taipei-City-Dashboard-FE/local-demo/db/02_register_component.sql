-- ═══ 青年人口分區：組件註冊 ═══
--
-- 對應影片「七、建立一個新組件 3.0」講的三個步驟：
--   1. components        建組件本體（id 與 index）
--   2. query_charts      每個城市一筆設定（含取數的 SQL）
--   3. component_charts  圖表樣式，跨城市共用只有一筆
--
-- id 9001 為避開官方既有編號而選；正式併入時請改為主辦方指派的編號，
-- 並同步修改下方所有出現 9001 的地方與 dashboards.components 陣列。
--
-- ── 年齡範圍：15-39，不是 18-35 ──
-- 《青年基本法》的青年是 18-35 歲，但公開統計採 5 歲組距，18 與 35 都落在
-- 組距中間。要精確切出 18-35 就必須推估組距內的單一年齡人口——那會產生
-- 「看起來很精準但實際不存在」的數字，是本專案明文禁止的事。
--
-- 因此改用五個完整組距相加：
--   15-39 = percent6 + percent7 + percent8 + percent9 + percent10
--         = 15-19  + 20-24  + 25-29  + 30-34  + 35-39
-- 每一個都是官方原始數字，零內插、零拆分。
--
-- 這是 Exact Youth Proxy（B 級）。先前版本用 20-24/25-29/30-34 三個組距，
-- 只是 Youth Segment（C 級）——合法，但涵蓋範圍比可用的窄。
-- 本檔由 scratchpad/gen-sql.mjs 產生，行政區順序向資料庫查得。

-- ── 1. 組件本體 ──
INSERT INTO public.components (id, index, name) VALUES
  (9001, 'youth_population_district', '青年人口分區')
ON CONFLICT (id) DO UPDATE SET index = EXCLUDED.index, name = EXCLUDED.name;

-- ── 2. 各城市設定 ──
-- 注意：影片明確提醒，若只建 metrotaipei 而沒有 taipei，組件在畫面上會出問題，
--       所以兩筆都要建。
INSERT INTO public.query_charts (
  index, history_config, map_config_ids, map_filter,
  time_from, time_to, update_freq, update_freq_unit,
  source, short_desc, long_desc, use_case, links, contributors,
  query_type, query_chart, city
) VALUES (
  'youth_population_district', NULL, '{}', '{}',
  'static', NULL, 1, 'year',
  '新北主計處',
  '新北市青年近似人口（15-39 歲）的行政區分布',
  '依《青年基本法》定義之青年年齡範圍為 18 至 35 歲。公開統計採 5 歲組距，18 與 35 都落在組距中間，無法在不推估的前提下精確切出；本組件因此採用 15-19、20-24、25-29、30-34、35-39 五個完整組距合計之 15-39 歲，作為青年近似族群（Exact Youth Proxy）觀察新北市 29 個行政區的分布。五個組距皆為官方原始數字之加總，未做任何內插、拆分或估算。法定青年定義仍為 18-35 歲，本組件不等同該範圍。',
  '用於掌握青年人口在新北市各行政區的聚集情形，可作為青年職涯據點、青創基地、租金補貼資源配置的參考依據。五個組距分列呈現，可直接看出各區是以「剛畢業」或「成家階段」族群為主，兩者的政策需求不同。',
  '{https://data.ntpc.gov.tw/datasets/8308ab58-62d1-424e-8314-24b65b7ab492}',
  '{ntpc}',
  'three_d',
  $q$
  SELECT x_axis, y_axis, data FROM (
    SELECT "區域別" AS x_axis, '15_19歲人口數' AS y_axis,
           round(percent6 / 1000.0) AS data
    FROM public.youth_population_district_newtaipei
    WHERE "統計類型" = '計' AND "區域別" <> '新北市'
      AND "年份" = (SELECT max("年份") FROM public.youth_population_district_newtaipei)
    UNION ALL
    SELECT "區域別", '20_24歲人口數', round(percent7 / 1000.0)
    FROM public.youth_population_district_newtaipei
    WHERE "統計類型" = '計' AND "區域別" <> '新北市'
      AND "年份" = (SELECT max("年份") FROM public.youth_population_district_newtaipei)
    UNION ALL
    SELECT "區域別", '25_29歲人口數', round(percent8 / 1000.0)
    FROM public.youth_population_district_newtaipei
    WHERE "統計類型" = '計' AND "區域別" <> '新北市'
      AND "年份" = (SELECT max("年份") FROM public.youth_population_district_newtaipei)
    UNION ALL
    SELECT "區域別", '30_34歲人口數', round(percent9 / 1000.0)
    FROM public.youth_population_district_newtaipei
    WHERE "統計類型" = '計' AND "區域別" <> '新北市'
      AND "年份" = (SELECT max("年份") FROM public.youth_population_district_newtaipei)
    UNION ALL
    SELECT "區域別", '35_39歲人口數', round(percent10 / 1000.0)
    FROM public.youth_population_district_newtaipei
    WHERE "統計類型" = '計' AND "區域別" <> '新北市'
      AND "年份" = (SELECT max("年份") FROM public.youth_population_district_newtaipei)
  ) t
  -- 官方要求 three_d 需對 x_axis 與 y_axis 排序，且各系列的 x_axis 順序必須一致
  -- （/back-end/component-data-apis）。若改用 ORDER BY data DESC，五個年齡層會各自
  -- 排出不同的行政區順序，後端編譯 three_d 時是按列序 append、不檢查數值屬於哪個
  -- 分類（componentData.go:301-325），數字會安靜地對到錯的行政區。
  --
  -- 下列陣列由資料庫查出（依 15-39 歲合計人口降冪），不是手寫的。
  -- chart_9001.json 用的是同一份順序，兩條路徑因此不會分岔。
  ORDER BY
    ARRAY_POSITION(ARRAY[
      '板橋區','新莊區','中和區','三重區','新店區','土城區','蘆洲區','汐止區',
      '淡水區','樹林區','永和區','林口區','三峽區','五股區','鶯歌區','泰山區',
      '八里區','瑞芳區','深坑區','三芝區','金山區','萬里區','石門區','貢寮區',
      '烏來區','石碇區','雙溪區','坪林區','平溪區'
    ]::varchar[], t.x_axis),
    ARRAY_POSITION(ARRAY[
      '15_19歲人口數','20_24歲人口數','25_29歲人口數','30_34歲人口數','35_39歲人口數'
    ]::varchar[], t.y_axis)
  $q$,
  'metrotaipei'
)
-- 冪等：改了 SQL 或說明之後要能直接重跑。
-- 原本沒有這段，第二次執行會撞 query_charts_pkey，只能先手動 DELETE——
-- 而「先手動刪一下」正是比賽現場最容易漏做、漏做又不會有人發現的一步。
ON CONFLICT (index, city) DO UPDATE SET
  source = EXCLUDED.source,
  short_desc = EXCLUDED.short_desc,
  long_desc = EXCLUDED.long_desc,
  use_case = EXCLUDED.use_case,
  links = EXCLUDED.links,
  contributors = EXCLUDED.contributors,
  query_type = EXCLUDED.query_type,
  query_chart = EXCLUDED.query_chart;

INSERT INTO public.query_charts (
  index, history_config, map_config_ids, map_filter,
  time_from, time_to, update_freq, update_freq_unit,
  source, short_desc, long_desc, use_case, links, contributors,
  query_type, query_chart, city
) VALUES (
  'youth_population_district', NULL, '{}', '{}',
  'static', NULL, 1, 'year',
  '臺北主計處',
  '臺北市青年近似人口（15-39 歲）的行政區分布',
  '依《青年基本法》定義之青年年齡範圍為 18 至 35 歲。公開統計採 5 歲組距，18 與 35 都落在組距中間，無法在不推估的前提下精確切出；本組件因此採用 15-19、20-24、25-29、30-34、35-39 五個完整組距合計之 15-39 歲，作為青年近似族群（Exact Youth Proxy）觀察臺北市 12 個行政區的分布。五個組距皆為官方原始數字之加總，未做任何內插、拆分或估算。法定青年定義仍為 18-35 歲，本組件不等同該範圍。',
  '用於掌握青年人口在臺北市各行政區的聚集情形，並與新北市對照觀察雙北青年的居住分布差異。',
  '{}',
  '{doit}',
  'three_d',
  $q$
  SELECT x_axis, y_axis, data FROM (
    SELECT "區域別" AS x_axis, '15_19歲人口數' AS y_axis,
           round(percent6 / 1000.0) AS data
    FROM public.city_age_distribution_taipei
    WHERE "統計類型" = '計' AND "區域別" <> '總計'
      AND "年份" = (SELECT max("年份") FROM public.city_age_distribution_taipei)
    UNION ALL
    SELECT "區域別", '20_24歲人口數', round(percent7 / 1000.0)
    FROM public.city_age_distribution_taipei
    WHERE "統計類型" = '計' AND "區域別" <> '總計'
      AND "年份" = (SELECT max("年份") FROM public.city_age_distribution_taipei)
    UNION ALL
    SELECT "區域別", '25_29歲人口數', round(percent8 / 1000.0)
    FROM public.city_age_distribution_taipei
    WHERE "統計類型" = '計' AND "區域別" <> '總計'
      AND "年份" = (SELECT max("年份") FROM public.city_age_distribution_taipei)
    UNION ALL
    SELECT "區域別", '30_34歲人口數', round(percent9 / 1000.0)
    FROM public.city_age_distribution_taipei
    WHERE "統計類型" = '計' AND "區域別" <> '總計'
      AND "年份" = (SELECT max("年份") FROM public.city_age_distribution_taipei)
    UNION ALL
    SELECT "區域別", '35_39歲人口數', round(percent10 / 1000.0)
    FROM public.city_age_distribution_taipei
    WHERE "統計類型" = '計' AND "區域別" <> '總計'
      AND "年份" = (SELECT max("年份") FROM public.city_age_distribution_taipei)
  ) t
  -- 官方要求 three_d 需對 x_axis 與 y_axis 排序，且各系列的 x_axis 順序必須一致
  -- （/back-end/component-data-apis）。若改用 ORDER BY data DESC，五個年齡層會各自
  -- 排出不同的行政區順序，後端編譯 three_d 時是按列序 append、不檢查數值屬於哪個
  -- 分類（componentData.go:301-325），數字會安靜地對到錯的行政區。
  --
  -- 下列陣列由資料庫查出（依 15-39 歲合計人口降冪），不是手寫的。
  -- chart_9001.json 用的是同一份順序，兩條路徑因此不會分岔。
  ORDER BY
    ARRAY_POSITION(ARRAY[
      '內湖區','文山區','士林區','大安區','北投區','中山區','信義區','松山區',
      '萬華區','中正區','大同區','南港區'
    ]::varchar[], t.x_axis),
    ARRAY_POSITION(ARRAY[
      '15_19歲人口數','20_24歲人口數','25_29歲人口數','30_34歲人口數','35_39歲人口數'
    ]::varchar[], t.y_axis)
  $q$,
  'taipei'
)
-- 冪等：改了 SQL 或說明之後要能直接重跑。
-- 原本沒有這段，第二次執行會撞 query_charts_pkey，只能先手動 DELETE——
-- 而「先手動刪一下」正是比賽現場最容易漏做、漏做又不會有人發現的一步。
ON CONFLICT (index, city) DO UPDATE SET
  source = EXCLUDED.source,
  short_desc = EXCLUDED.short_desc,
  long_desc = EXCLUDED.long_desc,
  use_case = EXCLUDED.use_case,
  links = EXCLUDED.links,
  contributors = EXCLUDED.contributors,
  query_type = EXCLUDED.query_type,
  query_chart = EXCLUDED.query_chart;

-- ── 3. 圖表樣式（跨城市共用，只有一筆）──
-- 五個組距是有序資料（由年輕到年長），所以用漸層而不是對比色。
-- 保留原本三色作為錨點（藍／綠／黃），中間補兩色。
INSERT INTO public.component_charts (index, color, types, unit) VALUES
  ('youth_population_district',
   '{#5a9cf8,#4FB3C4,#56B96D,#A8C95F,#F8CF58}',
   '{DistrictChart,ColumnChart}',
   '仟人')
ON CONFLICT (index) DO UPDATE
  SET color = EXCLUDED.color, types = EXCLUDED.types, unit = EXCLUDED.unit;
