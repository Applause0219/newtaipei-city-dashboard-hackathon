-- ═══ 把組件掛上儀表板 ═══
--
-- 兩種做法擇一：
--   A. 建一個新的「青年儀表板」（下方預設）
--   B. 加進既有儀表板，例如 ltc_care_newtpe

-- ── A. 新建儀表板 ──
INSERT INTO public.dashboards (index, name, components, icon, updated_at, created_at)
VALUES ('youth-newtaipei', '青年儀表板', '{9001}', 'diversity_3', now(), now())
ON CONFLICT (index) DO UPDATE SET components = EXCLUDED.components;

-- 讓它出現在公開群組（groups.id = 1 為 public）
INSERT INTO public.dashboard_groups (dashboard_id, group_id)
SELECT d.id, 1 FROM public.dashboards d
WHERE d.index = 'youth-newtaipei'
ON CONFLICT DO NOTHING;

-- ── B. 或者加進既有儀表板（取消註解使用）──
-- UPDATE public.dashboards
--    SET components = array_append(components, 9001), updated_at = now()
--  WHERE index = 'ltc_care_newtpe' AND NOT (9001 = ANY(components));
