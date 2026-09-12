// age.js 自我測試。零相依、零框架：`node age.test.mjs` 就跑。
//
// 為什麼不用 node:test / vitest：比賽當天在別人的機器上跑，
// 多一個安裝步驟就多一個會失敗的地方。斷言本來就只是 if + throw。
//
// 這份檔案同時是驗收清單——每一條 case 對應一個「如果錯了，報告會說謊」
// 的具體情境，而不是為了覆蓋率而寫的測試。

import {
	POLICY_YOUTH,
	OPERATIONAL_YOUTH,
	AGGREGATION_RULE,
	validateAgeScope,
} from "./schema.js";
import {
	parseAgeLabel,
	binsFromCatalog,
	partitionBins,
	classifyAgeScope,
	selectYouthBins,
	canAggregate,
	compareAgeScopes,
	describeScope,
	scopeLimitations,
	AgeScopeError,
} from "./age.js";

// ─────────────────────────────────────────────────────────────
// 測試工具
// ─────────────────────────────────────────────────────────────

let pass = 0;
let fail = 0;
const lines = [];

function group(name) {
	lines.push("");
	lines.push(name);
}

function t(name, fn) {
	try {
		fn();
		pass++;
		lines.push(`  [ok]   ${name}`);
	} catch (err) {
		fail++;
		lines.push(`  [FAIL] ${name}`);
		lines.push(`         ${err.message}`);
	}
}

function eq(actual, expected, what = "值") {
	if (actual !== expected) {
		throw new Error(`${what} 應為 ${JSON.stringify(expected)}，實際是 ${JSON.stringify(actual)}`);
	}
}

function ok(cond, msg) {
	if (!cond) throw new Error(msg || "期望為真，實際為假");
}

function has(text, needle, what = "訊息") {
	if (!String(text).includes(needle)) {
		throw new Error(`${what}應包含「${needle}」，實際是：${text}`);
	}
}

/** 期望 fn 丟出 AgeScopeError，且訊息包含指定字串。 */
function refuses(fn, needles, what = "拒絕理由") {
	let thrown = null;
	let returned;
	try {
		returned = fn();
	} catch (err) {
		thrown = err;
	}
	if (!thrown) {
		throw new Error(`應該拒絕，卻回傳了 ${JSON.stringify(returned)}——`
			+ `這正是零推估要擋的：靜靜給出一個沒有來源的數字`);
	}
	ok(thrown instanceof AgeScopeError, `應丟出 AgeScopeError，實際是 ${thrown.name}: ${thrown.message}`);
	for (const n of [].concat(needles)) has(thrown.message, n, what);
}

// ─────────────────────────────────────────────────────────────
// 測試資料
// ─────────────────────────────────────────────────────────────

/**
 * 真實的 catalog.yaml 欄位（city_age_distribution_newtaipei /
 * youth_population_district_newtaipei 共用同一份）。
 *
 * 這裡刻意把 percent24（0-14歲）、percent26（15-64歲）、percent28（65歲以上）
 * 一起放進來：它們的 label 解析得出年齡，但與細組距**重疊**，
 * 是「同一批人的另一種切法」。binsFromCatalog 必須把它們剔掉，
 * 否則 15-19 會被算兩次。
 */
const TABLE_META = {
	label: "新北市現住人口年齡分配",
	fields: {
		年份: { type: "year", label: "年份（西元）" },
		區域別: { type: "category", label: "行政區" },
		統計類型: { type: "category", label: "性別" },
		percent2: { type: "count", label: "總人口" },
		percent3: { type: "count", label: "0-4歲人口" },
		percent4: { type: "count", label: "5-9歲人口" },
		percent5: { type: "count", label: "10-14歲人口" },
		percent6: { type: "count", label: "15-19歲人口" },
		percent7: { type: "count", label: "20-24歲人口" },
		percent8: { type: "count", label: "25-29歲人口" },
		percent9: { type: "count", label: "30-34歲人口" },
		percent10: { type: "count", label: "35-39歲人口" },
		percent11: { type: "count", label: "40-44歲人口" },
		percent12: { type: "count", label: "45-49歲人口" },
		percent13: { type: "count", label: "50-54歲人口" },
		percent14: { type: "count", label: "55-59歲人口" },
		percent15: { type: "count", label: "60-64歲人口" },
		percent16: { type: "count", label: "65-69歲人口" },
		percent17: { type: "count", label: "70-74歲人口" },
		percent18: { type: "count", label: "75-79歲人口" },
		percent19: { type: "count", label: "80-84歲人口" },
		percent20: { type: "count", label: "85-89歲人口" },
		percent21: { type: "count", label: "90-94歲人口" },
		percent22: { type: "count", label: "95-99歲人口" },
		percent23: { type: "count", label: "100歲以上人口" },
		percent24: { type: "count", label: "0-14歲人口" },
		percent25: { type: "ratio", label: "幼年人口佔比(%)" },
		percent26: { type: "count", label: "15-64歲人口" },
		percent27: { type: "ratio", label: "工作年齡人口佔比(%)" },
		percent28: { type: "count", label: "65歲以上人口" },
		percent29: { type: "ratio", label: "老年人口佔比(%)" },
		percent30: { type: "ratio", label: "老年扶養比" },
		percent31: { type: "ratio", label: "幼年扶養比" },
		percent32: { type: "ratio", label: "扶養比" },
		percent33: { type: "ratio", label: "老化指數" },
	},
};

const BINS = binsFromCatalog(TABLE_META);
/** 取出指定範圍的官方組距（測試用；不做任何切割） */
const pick = (lower, upper) => BINS.filter((b) => b.lower >= lower && b.upper <= upper);

const COUNT_METRIC = { column: "percent7", label: "人口數", type: "count" };
const RATIO_METRIC = { column: "unemployment_rate", label: "失業率", type: "ratio" };

// ─────────────────────────────────────────────────────────────
group("標籤解析");
// ─────────────────────────────────────────────────────────────

t("parseAgeLabel 認得 ASCII hyphen、en-dash 與各種尾綴", () => {
	const a = parseAgeLabel("20-24歲人口");
	eq(a.lower, 20, "20-24 的 lower"); eq(a.upper, 24, "20-24 的 upper");
	// U+2013 en-dash：本專案兩種破折號都出現過，只認一種會安靜地少抓組距
	const b = parseAgeLabel("25–29歲");
	ok(b, "25–29歲（en-dash）應解析得出來");
	eq(b.lower, 25, "en-dash 的 lower"); eq(b.upper, 29, "en-dash 的 upper");
	const c = parseAgeLabel("15-19歲人口數");
	eq(c.lower, 15, "15-19歲人口數 的 lower"); eq(c.upper, 19, "15-19歲人口數 的 upper");
});

t("parseAgeLabel 把開放組距標為 Infinity，且它必然在可用邊界外", () => {
	const a = parseAgeLabel("85歲以上");
	eq(a.lower, 85, "85歲以上 的 lower");
	eq(a.upper, Infinity, "85歲以上 的 upper");
	ok(a.upper > OPERATIONAL_YOUTH.upper, "開放組距不可能落在 15-40 內");
	eq(parseAgeLabel("100歲以上人口").upper, Infinity, "100歲以上人口 的 upper");
});

t("parseAgeLabel 不把非年齡欄位或年份當成組距", () => {
	for (const label of ["總人口", "老化指數", "扶養比", "行政區", "年份（西元）", "幼年人口佔比(%)"]) {
		eq(parseAgeLabel(label), null, `「${label}」`);
	}
	// 本專案年份涵蓋 2000-2024，少了「歲」與上界兩道保險就會被當成組距
	eq(parseAgeLabel("2000-2024年"), null, "「2000-2024年」");
});

// ─────────────────────────────────────────────────────────────
group("從 catalog 建組距");
// ─────────────────────────────────────────────────────────────

t("binsFromCatalog 取出 21 個五歲組距，並剔除重疊的粗組距", () => {
	eq(BINS.length, 21, "組距數量");
	eq(BINS[0].label, "0-4歲人口", "第一個組距");
	eq(BINS[3].column, "percent6", "15-19 對應的欄位");
	eq(BINS[4].column, "percent7", "20-24 對應的欄位");
	// 0-14 / 15-64 / 65以上 是同一批人的另一種切法，留著會重複計算
	const composites = BINS.filter((b) => ["percent24", "percent26", "percent28"].includes(b.column));
	eq(composites.length, 0, "重疊的粗組距應被剔除，實際留下的數量");
	const withComposites = binsFromCatalog(TABLE_META, { keepComposites: true });
	const { dropped } = partitionBins(withComposites);
	eq(dropped.length, 3, "被剔除的重疊組距數量");
	has(dropped[0].reason, "重複計算", "剔除理由");
});

// ─────────────────────────────────────────────────────────────
group("年齡分級（blueprint §7.3）");
// ─────────────────────────────────────────────────────────────

t("① 15-39 可用，B 級（五歲組距在 15-40 內能拿到的最寬範圍）", () => {
	const scope = selectYouthBins(BINS);
	eq(scope.label, "15-39", "預設選到的範圍");
	eq(scope.classification, "B", "分級");
	eq(scope.usable, true, "usable");
	eq(scope.bins.length, 5, "使用的組距數量");
	eq(scope.bins.map((b) => b.column).join(","), "percent6,percent7,percent8,percent9,percent10", "使用的欄位");
	eq(scope.is_exact, true, "is_exact");
	const v = validateAgeScope(scope);
	ok(v.ok, `應通過 schema.js 契約，錯誤：${v.errors.join("；")}`);
	// 40-44 不是被「裁掉」，是整組沒有被選——裁切就是推估
	ok(!scope.bins.some((b) => b.column === "percent11"), "40-44 不得出現在青年範圍裡");
});

t("② 15-44 → D 級，拒絕（含 41-44，不得合法裁切）", () => {
	const scope = classifyAgeScope(pick(15, 44));
	eq(scope.label, "15-44", "範圍");
	eq(scope.classification, "D", "分級");
	eq(scope.usable, false, "usable");
	has(scope.rejection, "41-44", "拒絕理由");
	has(scope.rejection, "不可用比例推估", "拒絕理由");
	// 直接指定 15-44 為目標也要被擋，不能因為「組距湊得出來」就放行
	refuses(() => selectYouthBins(BINS, { target: "15-44" }), ["超出可用邊界"]);
});

t("③ 10-19 → D 級，拒絕（含 10-14）", () => {
	const scope = classifyAgeScope(pick(10, 19));
	eq(scope.label, "10-19", "範圍");
	eq(scope.classification, "D", "分級");
	eq(scope.usable, false, "usable");
	has(scope.rejection, "10-14", "拒絕理由");
});

t("④ 35-44 → D 級，拒絕（含 40-44）", () => {
	const scope = classifyAgeScope(pick(35, 44));
	eq(scope.label, "35-44", "範圍");
	eq(scope.classification, "D", "分級");
	eq(scope.usable, false, "usable");
	has(scope.rejection, "41-44", "拒絕理由");
});

t("⑤ 18-35 → A 級（假想的單歲別資料來源；本專案的五歲組距做不到）", () => {
	const exact = [{ label: "18-35歲人口", lower: 18, upper: 35, column: "youth_exact" }];
	const scope = classifyAgeScope(exact);
	eq(scope.classification, "A", "分級");
	eq(scope.label, "18-35", "範圍");
	eq(scope.lower, POLICY_YOUTH.lower, "lower 應等於法定下界");
	eq(scope.upper, POLICY_YOUTH.upper, "upper 應等於法定上界");
});

t("⑥ 20-34 → C 級（完整落在 15-40 內，但只是子區段）", () => {
	const scope = classifyAgeScope(pick(20, 34));
	eq(scope.label, "20-34", "範圍");
	eq(scope.classification, "C", "分級");
	eq(scope.usable, true, "usable");
	// 上界差了 35 這一歲，就不能算「涵蓋青年」的近似
	eq(classifyAgeScope(pick(20, 39)).classification, "B", "20-39 的分級");
	eq(classifyAgeScope(pick(25, 29)).classification, "C", "25-29 的分級");
});

// ─────────────────────────────────────────────────────────────
group("範圍相容性（blueprint §7.6）");
// ─────────────────────────────────────────────────────────────

t("⑦ 25-34 vs 25-34 → exact", () => {
	const r = compareAgeScopes(pick(25, 34), pick(25, 34));
	eq(r.status, "exact", "狀態");
	has(r.reason, "25-34", "理由");
	// 即使 exact，只要不是 A 級就仍須提醒「這不是法定青年」
	ok(r.warnings.some((w) => w.includes("18-35")), "應提醒實際範圍不等於法定青年");
});

t("⑧ 25-34 vs 20-39 → proxy（不得自動把 20-39 收斂成 25-34）", () => {
	const r = compareAgeScopes(pick(25, 34), pick(20, 39));
	eq(r.status, "proxy", "狀態");
	ok(r.warnings.some((w) => w.includes("下修")), "應要求下修結論強度");
	ok(r.warnings.some((w) => w.includes("25-34")), "應指出可收斂的共同完整組距範圍");
});

t("⑨ 組距切法不同但範圍相同 → 仍是 exact（完整組距聚合是合法的）", () => {
	const coarse = [{ label: "20-34歲人口", lower: 20, upper: 34, column: "youth_20_34" }];
	const r = compareAgeScopes(pick(20, 34), coarse);
	eq(r.status, "exact", "狀態");
	ok(r.warnings.some((w) => w.includes("切法不同")), "應說明兩邊組距切法不同但可精確聚合");
});

t("⑩ 要對齊就得切割組距 → incompatible", () => {
	// 15-39（五歲組距）對上 18-35（單一組距）：18 與 35 都在五歲組距中間
	const exact = [{ label: "18-35歲人口", lower: 18, upper: 35, column: "youth_exact" }];
	const r = compareAgeScopes(pick(15, 39), exact);
	eq(r.status, "incompatible", "狀態");
	has(r.reason, "拆開", "理由");
});

t("⑪ 完全不重疊的兩個範圍 → incompatible", () => {
	const r = compareAgeScopes(pick(15, 19), pick(30, 34));
	eq(r.status, "incompatible", "狀態");
	has(r.reason, "沒有重疊", "理由");
});

t("⑫ 只要有一邊是 D 級就 incompatible", () => {
	const r = compareAgeScopes(pick(15, 39), pick(15, 44));
	eq(r.status, "incompatible", "狀態");
	has(r.reason, "可用邊界", "理由");
});

// ─────────────────────────────────────────────────────────────
group("零推估（blueprint §7.2）");
// ─────────────────────────────────────────────────────────────

t("⑬ 想從 15-19 組距切出 18-19 → 必須拒絕，絕不回傳數字", () => {
	refuses(
		() => selectYouthBins(BINS, { target: "18-19" }),
		["15-19", "按比例", "禁止"]);
	// 法定青年本身同理：18 與 35 都落在五歲組距中間，官方資料沒有這個數字
	refuses(
		() => selectYouthBins(BINS, { target: { lower: 18, upper: 35 } }),
		["起點 18", "推估"]);
});

t("⑭ 不連續的組距不得構成 scope（宣稱 20-34 卻少算 25-29 就是說謊）", () => {
	const gapped = [BINS.find((b) => b.column === "percent7"), BINS.find((b) => b.column === "percent9")];
	refuses(() => classifyAgeScope(gapped), ["不連續", "25-29"]);
});

t("⑮ 開放組距（85歲以上）無法構成可驗證的 scope", () => {
	const open = BINS.find((b) => b.column === "percent23");
	eq(open.upper, Infinity, "100歲以上的 upper");
	refuses(() => classifyAgeScope([open]), ["開放組距", "可用邊界"]);
});

// ─────────────────────────────────────────────────────────────
group("指標聚合規則（blueprint §7.5）");
// ─────────────────────────────────────────────────────────────

t("⑯ 20-24 + 25-29 + 30-34 的人口數 → 允許加總", () => {
	const r = canAggregate(pick(20, 34), COUNT_METRIC);
	eq(r.ok, true, `應允許，理由：${r.reason}`);
	eq(r.rule, AGGREGATION_RULE.SUM, "聚合規則");
	has(r.reason, "20-34", "理由應說明涵蓋範圍");
	// 呼叫端傳進來的順序不保證（例如來自 Object.entries 或使用者手寫），
	// 先排序再算範圍，否則理由裡會出現錯誤的年齡區間
	const shuffled = canAggregate([...pick(20, 34)].reverse(), COUNT_METRIC);
	eq(shuffled.ok, true, `亂序輸入也應允許，理由：${shuffled.reason}`);
	has(shuffled.reason, "20-34", "亂序輸入的理由");
});

t("⑰ 三個組距的失業率取平均 → 拒絕，並指名需要分子與分母", () => {
	const r = canAggregate(pick(15, 29), RATIO_METRIC);
	eq(r.ok, false, "應拒絕");
	eq(r.rule, AGGREGATION_RULE.REQUIRE_DENOMINATOR, "聚合規則");
	has(r.reason, "失業率", "理由");
	has(r.reason, "比例", "理由");
	has(r.reason, "分子", "理由");
	has(r.reason, "分母", "理由");
});

t("⑱ 不連續的組距（20-24 + 30-34，跳過 25-29）→ 拒絕加總", () => {
	const gapped = [BINS.find((b) => b.column === "percent7"), BINS.find((b) => b.column === "percent9")];
	const r = canAggregate(gapped, COUNT_METRIC);
	eq(r.ok, false, "應拒絕");
	has(r.reason, "不連續", "理由");
	has(r.reason, "25-29", "理由應指出缺了哪一段");
});

// ─────────────────────────────────────────────────────────────
group("對外敘述");
// ─────────────────────────────────────────────────────────────

t("⑲ describeScope 同時說出實際範圍與法定定義，且不宣稱近似值就是 18-35", () => {
	const scope = selectYouthBins(BINS);
	const s = describeScope(scope);
	has(s, "15-39", "敘述");
	has(s, "近似", "敘述");
	has(s, "法定青年定義仍為 18-35 歲", "敘述");
	// 絕不能出現「使用官方資料提供之 18-35 歲統計」這種宣稱
	ok(!s.includes("使用官方資料提供之 18-35"), `B 級不得宣稱使用的是 18-35：${s}`);
	// D 級必須明白說不能用
	has(describeScope(classifyAgeScope(pick(15, 44))), "不得作為青年分析", "D 級敘述");
	// A 級才可以說「完全一致」
	has(describeScope([{ label: "18-35歲人口", lower: 18, upper: 35, column: "x" }]), "完全一致", "A 級敘述");
});

t("⑳ scopeLimitations 交代實際欄位，滿足 artifact 對非 A 級必填限制的要求", () => {
	const limits = scopeLimitations(selectYouthBins(BINS));
	ok(limits.length >= 2, `非 A 級至少要兩條限制，實際 ${limits.length} 條`);
	ok(limits.some((l) => l.includes("percent6")), "應列出實際使用的欄位");
	ok(limits.some((l) => l.includes("36-39")), "應說明比法定青年多涵蓋的年齡");
	ok(limits.some((l) => l.includes("未做任何組距內的比例拆分")), "應聲明沒有做組距內拆分");
});

// ─────────────────────────────────────────────────────────────
// 輸出
// ─────────────────────────────────────────────────────────────

console.log(lines.join("\n"));
console.log("");
console.log(`age.js: ${pass}/${pass + fail} 通過`);
process.exit(fail === 0 ? 0 : 1);
