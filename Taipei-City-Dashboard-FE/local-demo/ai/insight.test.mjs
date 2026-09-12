// insight.js / report.js 的自我測試
//
// 跑法：node insight.test.mjs
// 刻意不用任何測試框架：比賽現場多一個 npm 安裝步驟，就多一個會壞掉的東西。
//
// 這裡測的不是「函式會不會動」，而是**那些擋下錯誤結論的規則還在不在**。
// 每一條測試對應一種「報告會說謊」的具體方式，註解直接寫出那是哪一種。

import { validateArtifact } from "./schema.js";
import {
	EFFECT_SIZE_FLOOR,
	buildInsightArtifact,
	evaluateCandidate,
	formatFunnel,
	rankInsights,
	runFunnel,
	runGates,
} from "./insight.js";
import { renderInsight, renderReport } from "./report.js";

// ─────────────────────────────────────────────────────────────
// 迷你測試工具
// ─────────────────────────────────────────────────────────────

let pass = 0;
let fail = 0;
const failures = [];

function test(name, fn) {
	try {
		fn();
		pass += 1;
		console.log(`  ✓ ${name}`);
	} catch (err) {
		fail += 1;
		failures.push(`${name}：${err.message}`);
		console.log(`  ✗ ${name}\n      ${err.message}`);
	}
}
function assert(cond, msg) {
	if (!cond) throw new Error(msg || "斷言失敗");
}
function assertEq(actual, expected, msg) {
	if (actual !== expected) throw new Error(`${msg || "值不符"}：預期 ${JSON.stringify(expected)}，實際 ${JSON.stringify(actual)}`);
}
function assertIncludes(haystack, needle, msg) {
	if (!String(haystack).includes(needle)) throw new Error(`${msg || "缺少內容"}：找不到「${needle}」`);
}

// ─────────────────────────────────────────────────────────────
// 測試資料
// ─────────────────────────────────────────────────────────────

/** C 級：只涵蓋 25-34，是法定青年 18-35 的子區段 */
const scopeC = {
	bins: [
		{ label: "25-29歲人口", lower: 25, upper: 29, column: "percent8" },
		{ label: "30-34歲人口", lower: 30, upper: 34, column: "percent9" },
	],
	lower: 25, upper: 34, classification: "C", label: "25-34", is_exact: true,
};

/** B 級：15-39，完整落在可用邊界 15-40 內，是青年近似 */
const scopeB = {
	bins: [
		{ label: "15-19歲人口", lower: 15, upper: 19, column: "percent6" },
		{ label: "20-24歲人口", lower: 20, upper: 24, column: "percent7" },
		{ label: "25-29歲人口", lower: 25, upper: 29, column: "percent8" },
		{ label: "30-34歲人口", lower: 30, upper: 34, column: "percent9" },
		{ label: "35-39歲人口", lower: 35, upper: 39, column: "percent10" },
	],
	lower: 15, upper: 39, classification: "B", label: "15-39", is_exact: true,
};

/** D 級：40-49，整段在可用邊界之外，不是青年 */
const scopeD = {
	bins: [
		{ label: "40-44歲人口", lower: 40, upper: 44, column: "percent11" },
		{ label: "45-49歲人口", lower: 45, upper: 49, column: "percent12" },
	],
	lower: 40, upper: 49, classification: "D", label: "40-49", is_exact: true,
};

const metricDefCount = { column: "percent8", label: "青年人口", type: "count", aggregation: "SUM", unit: "人" };
const metricDefRatio = { column: "percent29", label: "老年人口佔比(%)", type: "ratio", aggregation: "REQUIRE_DENOMINATOR", denominator: "percent2", unit: "%" };

const SQL = "SELECT 年份 AS x_axis, (percent8 + percent9) AS data FROM public.city_age_distribution_newtaipei WHERE 區域別 = '新北市'";
const SNAPSHOT = "2026-09-12T02:00:00.000Z";

function trendCandidate(over = {}) {
	return {
		type: "trend",
		metric: "percent8_9",
		metric_label: "青年人口",
		facts: { direction: "down", change_pct: 6.2, start_x: 2019, end_x: 2025, start_y: 320145, end_y: 300302 },
		effect_size: 0.72,
		statistical_strength: 0.88,
		evidence: { series_keys: ["新北市"], x_range: [2019, 2025], n: 7 },
		...over,
	};
}

function input(over = {}) {
	return {
		candidate: trendCandidate(),
		ageScope: scopeC,
		geography: "新北市",
		timeRange: "2019-2025",
		datasetIds: ["city_age_distribution_newtaipei"],
		sql: SQL,
		queryId: "q_test_001",
		metricDef: metricDefCount,
		snapshotAt: SNAPSHOT,
		...over,
	};
}

// ─────────────────────────────────────────────────────────────
// 閘門
// ─────────────────────────────────────────────────────────────

console.log("\ninsight.js / report.js 自我測試\n");
console.log("閘門（硬拒絕）");

// 擋的是：拿 40-49 歲的資料，用青年政策的語言講出去。
test("D 級年齡被 age_scope 閘門擋下，且拒絕理由指得出是哪一關", () => {
	const r = runGates(input({ ageScope: scopeD }));
	assertEq(r.ok, false, "D 級竟然通過");
	assertEq(r.gate, "age_scope", "擋下的閘門名稱不對");
	assertIncludes(r.reason, "D", "理由沒說是 D 級");
	assertIncludes(r.reason, "40-49", "理由沒指出實際範圍");

	const ev = evaluateCandidate(input({ ageScope: scopeD }));
	assertEq(ev.status, "rejected");
	assertIncludes(ev.reject_reasons[0], "[age_scope]", "拒絕理由沒有標明閘門名稱");
});

// 擋的是：把 0.4% 的波動寫成「明顯下降」。
test("效果量低於地板被擋下，理由寫明是微小波動", () => {
	const weak = trendCandidate({ effect_size: 0.04, facts: { direction: "down", change_pct: 0.4, start_x: 2019, end_x: 2025 } });
	const r = runGates(input({ candidate: weak }));
	assertEq(r.ok, false, "微小波動竟然通過");
	assertEq(r.gate, "effect_size_floor");
	assertEq(r.stage, "statistical", "效果量屬於統計層，漏斗分段才算得對");
	assertIncludes(r.reason, "微小波動");
	assertIncludes(r.reason, String(EFFECT_SIZE_FLOOR));
});

// 擋的是：一個講得出結論、卻沒人能驗算的數字。
test("沒有 SQL 的候選被擋下（無法重現）", () => {
	const r = runGates(input({ sql: undefined }));
	assertEq(r.ok, false, "沒有 SQL 竟然通過");
	assertEq(r.gate, "evidence_reproducible");
	assertIncludes(r.reason, "重現");
});

// 擋的是：把人數跟百分比放進同一句結論，數字看起來合理但不對應任何真實的量。
test("跨資料集但指標定義不相容被擋下", () => {
	const r = runGates(input({
		datasetIds: ["city_age_distribution_newtaipei", "city_age_distribution_taipei"],
		metricDef: [metricDefCount, metricDefRatio],
	}));
	assertEq(r.ok, false, "count 對 ratio 竟然通過");
	assertEq(r.gate, "metric_compatibility");
	assertIncludes(r.reason, "型別不相容");
});

// 擋的是：兩三個點畫出來的完美趨勢線。
test("資料點不足被擋下", () => {
	const r = runGates(input({ candidate: trendCandidate({ evidence: { series_keys: ["新北市"], x_range: [2024, 2025], n: 2 } }) }));
	assertEq(r.ok, false, "2 個點竟然可以做趨勢");
	assertEq(r.gate, "min_data_points");
	assertIncludes(r.reason, "2");
});

// ─────────────────────────────────────────────────────────────
// InsightArtifact
// ─────────────────────────────────────────────────────────────

console.log("\nInsightArtifact");

test("合法的 C 級候選產出的 artifact 通過 validateArtifact，且 limitations 不為空", () => {
	const a = buildInsightArtifact(input());
	const v = validateArtifact(a);
	assert(v.ok, `artifact 不合契約：${v.errors.join("；")}`);
	assert(a.limitations.length > 0, "C 級竟然沒有任何限制說明");
	assertIncludes(a.limitations.join("\n"), "僅代表 25-34 歲青年區段");
	assertIncludes(a.limitations.join("\n"), "不代表完整 18-35");
	assertEq(a.validation.status, "validated");
	assert(a.validation.score > 0 && a.validation.score <= 100, `分數超出 0..100：${a.validation.score}`);
});

test("法定定義不會被改寫：實際資料 15-39 時 policy_age 仍是 18-35", () => {
	const a = buildInsightArtifact(input({ ageScope: scopeB }));
	assertEq(a.scope.policy_age, "18-35", "法定青年定義被資料帶著跑了");
	assertEq(a.scope.actual_age, "15-39");
	assertEq(a.scope.age_classification, "B");
	assert(a.limitations.length > 0, "B 級也必須標示近似的限制");
});

test("claim 的數字直接來自 facts，且不含因果語氣", () => {
	const a = buildInsightArtifact(input());
	assertIncludes(a.claim, "6.2%", "claim 沒有用 facts 裡的數字");
	assertIncludes(a.claim, "2019-2025");
	assertIncludes(a.claim, "新北市");
	["導致", "造成", "因為", "由於"].forEach((w) => {
		assert(!a.claim.includes(w), `claim 出現因果詞「${w}」`);
	});
});

// 擋的是：「轉折前 1.2%、轉折後 5.9%」被讀成兩段都在成長，實際上後段是 -5.9%。
test("單獨列出的變化量帶正負號，方向不會遺失", () => {
	const a = buildInsightArtifact(input({
		candidate: {
			type: "change_point", metric: "percent9", metric_label: "青年人口",
			facts: { direction: "down", change_pct: 6.2, change_year: 2023, before_change_pct: 1.2, after_change_pct: -5.9 },
			effect_size: 0.71, statistical_strength: 0.86,
			evidence: { series_keys: ["新北市"], x_range: [2019, 2025], n: 7 },
		},
	}));
	const joined = a.narrative.data_fact.join("\n");
	assertIncludes(joined, "轉折前變化：+1.2%");
	assertIncludes(joined, "轉折後變化：-5.9%");
});

// 擋的是：mining.js 用 pct_change / first_x / decreasing，本模組卻只認
// change_pct / start_x / down——欄位名對不上時不會報錯，只會靜靜地掉進
// 「facts 未提供可敘述的數值」的 fallback，整份報告變成沒有數字的空話。
// 這裡用 mining.js **實際使用的欄位名**當 fixture（不 import 它，避免耦合）。
test("讀得懂 mining.js 實際的 facts 欄位名，不會掉進 fallback", () => {
	const real = {
		trend: {
			type: "trend", metric: "percent8", metric_label: "青年人口",
			facts: {
				slope: -3307.5, intercept: 1, r2: 0.94, adj_r2: 0.93, direction: "decreasing",
				rel_change: -0.062, pct_change: -6.2,
				first_x: 2019, last_x: 2025, first_y: 320145, last_y: 300302, mean_y: 310000,
			},
			effect_size: 0.72, statistical_strength: 0.88,
			evidence: { series_keys: ["新北市"], x_range: [2019, 2025], n: 7, unit: "人" },
		},
		change_point: {
			type: "change_point", metric: "percent8", metric_label: "青年人口",
			facts: {
				year: 2022, split_index: 3, slope_before: 1200.5, slope_after: -5400.25,
				delta_slope: -6600.75, n_before: 3, n_after: 4,
				mean_before: 318000, mean_after: 305000, gap_at_year: 900,
			},
			effect_size: 0.66, statistical_strength: 0.81,
			evidence: { series_keys: ["新北市"], x_range: [2019, 2025], n: 7, unit: "人" },
		},
		rank_change: {
			type: "rank_change", metric: "percent8", metric_label: "青年人口",
			facts: {
				key: "三重區", label: "三重區", rank_first: 3, rank_last: 6, delta_ranks: -3,
				direction: "down", x_first: 2019, x_last: 2025,
				value_first: 51000, value_last: 46000, crossed_keys: ["中和區", "新莊區"], k: 29,
			},
			effect_size: 0.5, statistical_strength: 0.7,
			evidence: { series_keys: ["三重區"], x_range: [2019, 2025], n: 58, unit: "人" },
		},
		outlier: {
			type: "outlier", metric: "percent8", metric_label: "青年人口",
			facts: {
				x: 2021, value: 250000, expected: 310000, deviation: -60000,
				robust_z: -4.2, threshold: 3.5, median_value: 310000, mad: 8000, scale: 1.4826, method: "mad",
			},
			effect_size: 0.8, statistical_strength: 0.9,
			evidence: { series_keys: ["新北市"], x_range: [2019, 2025], n: 7, unit: "人" },
		},
		group_difference: {
			type: "group_difference", metric: "percent8", metric_label: "青年人口",
			facts: {
				high_key: "板橋區", high_label: "板橋區", high_value: 92000,
				low_key: "平溪區", low_label: "平溪區", low_value: 380,
				gap: 91620, rel_gap: 0.9918, ratio: 242.1, grand_mean: 20000, eta_squared: 0.62, k: 29,
			},
			effect_size: 0.9, statistical_strength: 0.95,
			evidence: { series_keys: ["板橋區", "平溪區"], x_range: [2025, 2025], n: 29, unit: "人" },
		},
	};

	for (const [type, candidate] of Object.entries(real)) {
		const a = buildInsightArtifact(input({ candidate }));
		const joined = a.narrative.data_fact.join("\n");
		assert(!joined.includes("facts 未提供可敘述的數值"), `${type} 掉進了 fallback，欄位名沒對上`);
		assert(/\d/.test(a.claim), `${type} 的 claim 沒有任何數字`);
		assert(a.narrative.data_fact.length >= 2, `${type} 只生出 ${a.narrative.data_fact.length} 條事實`);
		assert(!joined.includes("NaN") && !joined.includes("undefined"), `${type} 的敘述含 NaN／undefined`);
	}

	// 方向詞正規化：decreasing 要被讀成「下降」。
	const t = buildInsightArtifact(input({ candidate: real.trend }));
	assertIncludes(t.claim, "下降", "decreasing 沒有被正規化成下降");
	assertIncludes(t.claim, "6.2%", "pct_change 沒有被讀到");
	assertIncludes(t.narrative.data_fact.join("\n"), "2019-2025", "first_x/last_x 沒有被讀到");

	// rel_gap 不是「差了幾 %」，標籤必須把算式寫出來，否則會被當成成長率。
	const g = buildInsightArtifact(input({ candidate: real.group_difference }));
	assertIncludes(g.narrative.data_fact.join("\n"), "相對差距（差距 ÷ 兩端總和）");
});

// ── 可忽略的變化率不得宣稱方向 ──
//
// 來自實際整合：新莊區 change_point 印出「轉折前：每年 +79.12」。
// 基期 171,342 人，+79/年 是 0.046%，與持平無法區分；而且那個正號是
// 最小平方法的產物——頭尾兩點算出來是 -77，方向相反。
// 印在「事實」標題底下，等於宣稱 2013 年前人口在成長。它沒有。
function changePointOf(over) {
	return {
		type: "change_point", metric: "percent8", metric_label: "青年人口",
		facts: { year: 2013, direction: "decreasing", ...over },
		effect_size: 0.6, statistical_strength: 0.8,
		evidence: { series_keys: ["新莊區"], x_range: [2000, 2024], n: 25, unit: "人" },
	};
}

test("可忽略的斜率（大基期）以「接近持平」呈現，不印出會翻面的正負號", () => {
	const a = buildInsightArtifact(input({
		candidate: changePointOf({ slope_before: 79.12, slope_after: -3861.0, mean_before: 171342, mean_after: 149966 }),
	}));
	const joined = a.narrative.data_fact.join("\n");

	assertIncludes(joined, "轉折前：接近持平");
	assert(!/轉折前：每年/.test(joined), "仍然把可忽略的斜率印成帶方向的每年變化");
	assert(!joined.includes("79"), `+79 仍然出現在敘述裡：\n${joined}`);
	// 真實的那一段必須照常印出來——這條規則是抑制雜訊，不是一律噤聲。
	assertIncludes(joined, "轉折後：每年 -3,861人");
	// 判讀語句也不得宣稱「方向反轉」（那同樣是在說轉折前在成長）。
	assert(!a.narrative.analytical_insight.includes("反轉"), "判讀仍宣稱方向反轉");
	assertIncludes(a.narrative.analytical_insight, "由接近持平轉為下降");
	// 精確值必須原封不動留在 evidence.facts 供人查證。
	assertEq(a.evidence.facts.slope_before, 79.12, "evidence.facts 的精確值被動到了");
	assertEq(a.evidence.facts.slope_after, -3861.0);
});

test("同樣的絕對斜率落在小基期上仍然是真實變化", () => {
	// 平溪區：基期約 1,400 人，79 人/年 是 5.6%，非常真實。
	const a = buildInsightArtifact(input({
		candidate: changePointOf({ slope_before: 79.12, slope_after: -120, mean_before: 1400, mean_after: 1100 }),
	}));
	const joined = a.narrative.data_fact.join("\n");

	assertIncludes(joined, "轉折前：每年 +79");
	assert(!joined.includes("轉折前：接近持平"), "小基期上的真實變化被誤判成持平");
	assertEq(a.evidence.facts.slope_before, 79.12, "evidence.facts 的精確值被動到了");
});

test("統計強度越低，宣稱方向的門檻越嚴", () => {
	// 0.3% 的年變化：強度高時算真實，強度低（資料上下擺盪）時不宣稱方向。
	const f = { slope_before: 300, slope_after: -3861, mean_before: 100000, mean_after: 90000 };
	const strong = buildInsightArtifact(input({ candidate: { ...changePointOf(f), statistical_strength: 1.0 } }));
	const weak = buildInsightArtifact(input({ candidate: { ...changePointOf(f), statistical_strength: 0.1 } }));

	assertIncludes(strong.narrative.data_fact.join("\n"), "轉折前：每年 +300");
	assertIncludes(weak.narrative.data_fact.join("\n"), "轉折前：接近持平");
});

test("同一個事實區塊裡的數字格式一致", () => {
	// 擋的是：同一串清單同時出現「+79.12」與「-3,861」，
	// 讀者無從判斷那兩位小數是精確度還是排版差異。
	const a = buildInsightArtifact(input({
		candidate: changePointOf({ slope_before: 1200.5, slope_after: -3861.0, mean_before: 171342, mean_after: 149966 }),
	}));
	const joined = a.narrative.data_fact.join("\n");
	const nums = [...joined.matchAll(/每年 [+-]([\d,]+(?:\.(\d+))?)/g)];
	assert(nums.length >= 2, `抓不到足夠的每年變化數字：\n${joined}`);
	const decimals = new Set(nums.map((m) => (m[2] || "").length));
	assertEq(decimals.size, 1, `同一區塊的每年變化用了 ${decimals.size} 種小數位數：${joined}`);
	assertEq([...decimals][0], 0, "人口數量級不應出現小數");
	// 人口這種大數一律帶千分位，不會一個有、一個沒有。
	assertIncludes(joined, "轉折前：每年 +1,201人");
	assertIncludes(joined, "轉折後：每年 -3,861人");
});

test("chart_specs 依類型對應，change_point 會標註轉折年", () => {
	const a = buildInsightArtifact(input());
	assertEq(a.chart_specs[0].chart_type, "line", "trend 應該對應 line");

	const cp = buildInsightArtifact(input({
		candidate: {
			type: "change_point", metric: "percent9", metric_label: "青年人口",
			facts: { direction: "down", change_pct: 6.2, change_year: 2022, before_change_pct: 1.1, after_change_pct: -5.4 },
			effect_size: 0.66, statistical_strength: 0.81,
			evidence: { series_keys: ["新北市"], x_range: [2019, 2025], n: 7 },
		},
	}));
	assertEq(cp.chart_specs[0].chart_type, "line");
	assertEq(cp.chart_specs[0].annotations.length, 1, "change_point 沒有標註轉折點");
	assertEq(cp.chart_specs[0].annotations[0].x, 2022);

	const gd = buildInsightArtifact(input({
		candidate: {
			type: "group_difference", metric: "percent8", metric_label: "青年人口",
			facts: { high_key: "板橋區", high_value: 92000, low_key: "平溪區", low_value: 380, gap_pct: 99.6 },
			effect_size: 0.9, statistical_strength: 0.95,
			evidence: { series_keys: ["板橋區", "平溪區"], x_range: [2025, 2025], n: 29 },
		},
	}));
	assertEq(gd.chart_specs[0].chart_type, "bar", "group_difference 應該對應 bar");
});

// ─────────────────────────────────────────────────────────────
// 排序與漏斗
// ─────────────────────────────────────────────────────────────

console.log("\n排序與漏斗");

test("排序把高效果高強度的結論排在勉強過關的前面", () => {
	const strong = buildInsightArtifact(input());
	const marginal = buildInsightArtifact(input({
		candidate: trendCandidate({
			metric: "percent10", metric_label: "青年人口",
			effect_size: 0.15, statistical_strength: 0.2,
			facts: { direction: "down", change_pct: 1.1, start_x: 2019, end_x: 2025, start_y: 100, end_y: 99 },
		}),
	}));
	assert(strong.validation.score > marginal.validation.score,
		`強結論 ${strong.validation.score} 沒有高於勉強結論 ${marginal.validation.score}`);

	const ranked = rankInsights([marginal, strong]);
	assertEq(ranked[0].insight_id, strong.insight_id, "排序沒有把強結論放第一");
	assertEq(rankInsights([marginal, strong], { topN: 1 }).length, 1, "topN 沒有生效");
});

test("漏斗計數前後相符", () => {
	const inputs = [
		input(),
		input({ candidate: trendCandidate({ metric: "percent10", effect_size: 0.55, statistical_strength: 0.6 }) }),
		input({ ageScope: scopeD }),                                        // 定義層擋下
		input({ sql: undefined }),                                          // 定義層擋下
		input({ candidate: trendCandidate({ metric: "percent6", effect_size: 0.03 }) }), // 統計層擋下
	];
	const f = runFunnel(inputs, { topN: 5 });

	const defRejects = f.rejected.filter((r) => r.stage === "definition").length;
	const statRejects = f.rejected.filter((r) => r.stage === "statistical").length;

	assertEq(f.scanned, 5, "掃描數不對");
	assertEq(f.scanned, f.passed_definition + defRejects, "掃描數 ≠ 通過定義檢查 + 定義層拒絕");
	assertEq(f.passed_definition - f.passed_statistical, statRejects, "定義層與統計層之間的差 ≠ 統計層拒絕數");
	assertEq(f.passed_definition, 3);
	assertEq(f.passed_statistical, 2);
	assertEq(f.recommended, 2);
	assertEq(f.artifacts.length, f.recommended, "recommended 與實際 artifact 數量不一致");
	assertIncludes(formatFunnel(f), "掃描 5 個候選");
	assertIncludes(formatFunnel(f), "推薦 Top 2");
});

// ─────────────────────────────────────────────────────────────
// Markdown 報告
// ─────────────────────────────────────────────────────────────

console.log("\nMarkdown 報告");

test("報告含法定青年、實際資料、限制與重要程度分數", () => {
	const a = buildInsightArtifact(input());
	const md = renderInsight(a, 1);
	assertIncludes(md, "### Insight 1｜");
	assertIncludes(md, "法定青年：18-35 歲");
	assertIncludes(md, "實際資料：25-34 歲");
	assertIncludes(md, "**限制**");
	assertIncludes(md, `**重要程度**：${a.validation.score} / 100`);
	assertIncludes(md, "**分析方法**");
	assertIncludes(md, "Trend Detection");
	assertIncludes(md, "city_age_distribution_newtaipei");
	assertIncludes(md, "僅代表 25-34 歲青年區段");
});

test("假設絕不出現在事實區塊底下", () => {
	const hypothesis = "可能與居住成本有關，仍需其他資料驗證。";
	const a = buildInsightArtifact(input({ hypothesis }));
	const md = renderInsight(a, 1);

	assertIncludes(md, "**事實（Data Fact）**");
	assertIncludes(md, "**假設（Hypothesis，尚未驗證）**");

	// 取出「事實」標題到下一個粗體標題之間的內容，假設的字句不得落在裡面。
	const lines = md.split("\n");
	const start = lines.findIndex((l) => l.startsWith("**事實（Data Fact）**"));
	assert(start >= 0, "找不到事實區塊");
	let end = lines.length;
	for (let i = start + 1; i < lines.length; i++) {
		if (lines[i].startsWith("**")) { end = i; break; }
	}
	const factBlock = lines.slice(start, end).join("\n");
	assert(!factBlock.includes(hypothesis), "假設被印在事實區塊裡");
	assert(!factBlock.includes("可能"), "事實區塊出現推測語氣的「可能」");

	// 假設本身必須在假設區塊裡，而且帶著「尚未驗證」的標題。
	const hStart = lines.findIndex((l) => l.startsWith("**假設"));
	assert(lines.slice(hStart).join("\n").includes(hypothesis), "假設沒有出現在假設區塊");
});

test("兩份資料年齡範圍不同時，敘述強度降級且不出現因果", () => {
	const a = buildInsightArtifact(input({
		datasetIds: ["city_age_distribution_newtaipei", "youth_population_district_newtaipei"],
		metricDef: [metricDefCount, { ...metricDefCount, column: "percent9" }],
		compareAgeScopes: [scopeB],
	}));
	assertEq(a.claim_strength, "group_level", "年齡範圍不同卻沒有降級");
	const md = renderInsight(a, 1);
	assertIncludes(md, "兩資料年齡範圍不同，僅能視為青年族群層級的同步現象。");
	["導致", "造成", "因為", "由於", "使得"].forEach((w) => {
		const factLines = md.split("\n").filter((l) => l.startsWith("- ") || l.startsWith("新北市"));
		assert(!factLines.join("\n").includes(w) || md.includes("已降級為假設"),
			`報告出現未降級的因果詞「${w}」`);
	});
});

test("渲染是決定性的：同一個 artifact render 兩次字串完全相同", () => {
	const a = buildInsightArtifact(input());
	assertEq(renderInsight(a, 1), renderInsight(a, 1), "同一個 artifact 兩次 render 結果不同");

	const f = runFunnel([input(), input({ candidate: trendCandidate({ metric: "percent10", effect_size: 0.5 }) })], { topN: 5 });
	assertEq(renderReport(f.artifacts, f), renderReport(f.artifacts, f), "整份報告兩次 render 結果不同");
	assertIncludes(renderReport(f.artifacts, f), "掃描 2 個候選");
	assertIncludes(renderReport(f.artifacts, f), "《青年基本法》");
});

// ─────────────────────────────────────────────────────────────

const total = pass + fail;
if (fail > 0) {
	console.log("\n失敗項目：");
	failures.forEach((f) => console.log(`  - ${f}`));
}
console.log(`\ninsight.js: ${pass}/${total} 通過\n`);
process.exit(fail > 0 ? 1 : 0);
