-- 新北市行政區代碼 → 區名
--
-- youth_fact 的 area_code 是數字代碼（65000010），圖表 x 軸直接顯示
-- 會變成一串數字，沒有人看得懂。
--
-- 這份對照不是自己查的——從 DE 的 12 支 DAG 原始碼抽出來，
-- 交叉比對 12 份完全一致（見 tools/gen-area-lookup.mjs）。
-- 用產生的而不是手打，是為了避免打錯字，以及來源改了能重抽。

BEGIN;

CREATE TABLE IF NOT EXISTS public.ntpc_area_name (
	area_code text PRIMARY KEY,
	area_name text NOT NULL
);

TRUNCATE public.ntpc_area_name;
INSERT INTO public.ntpc_area_name (area_code, area_name) VALUES
	('65000010', '板橋區'),
	('65000020', '三重區'),
	('65000030', '中和區'),
	('65000040', '永和區'),
	('65000050', '新莊區'),
	('65000060', '新店區'),
	('65000070', '樹林區'),
	('65000080', '鶯歌區'),
	('65000090', '三峽區'),
	('65000100', '淡水區'),
	('65000110', '汐止區'),
	('65000120', '瑞芳區'),
	('65000130', '土城區'),
	('65000140', '蘆洲區'),
	('65000150', '五股區'),
	('65000160', '泰山區'),
	('65000170', '林口區'),
	('65000180', '深坑區'),
	('65000190', '石碇區'),
	('65000200', '坪林區'),
	('65000210', '三芝區'),
	('65000220', '石門區'),
	('65000230', '八里區'),
	('65000240', '平溪區'),
	('65000250', '雙溪區'),
	('65000260', '貢寮區'),
	('65000270', '金山區'),
	('65000280', '萬里區'),
	('65000290', '烏來區');

-- youth_fact 帶區名的版本。
-- 用 LEFT JOIN：對不到的代碼（全國 TW、統計區 A6501-01）保留原值，
-- 不要因為查不到名字就讓整列消失。
DROP VIEW IF EXISTS public.youth_fact_named;
CREATE VIEW public.youth_fact_named AS
SELECT f.*,
       coalesce(n.area_name, f.area_code) AS area_name
FROM public.youth_fact f
LEFT JOIN public.ntpc_area_name n ON n.area_code = f.area_code;

COMMIT;
