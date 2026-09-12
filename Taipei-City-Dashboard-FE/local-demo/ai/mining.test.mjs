// mining.js 自我測試
//
// 跑法：node mining.test.mjs
// 沒有測試框架、沒有相依套件——比賽現場只保證有 node，任何 npm install
// 都是一個會在最糟的時刻失敗的步驟。
//
// 這裡的每一條測試都對應 mining.js 裡一個真的會出事的地方，
// 不是為了覆蓋率。失敗時直接印出「預期 vs 實際」，不用進 debugger。

import {
	detectTrend,
	detectChangePoint,
	detectOutlier,
	detectGroupDifference,
	detectRankChange,
	mineAll,
} from "./mining.js";
import { validateCandidate } from "./schema.js";

// ─────────────────────────────────────────────────────────────
// 迷你測試框架
// ─────────────────────────────────────────────────────────────

let passed = 0;
const failures = [];

function test(name, fn) {
	try {
		fn();
		passed++;
		console.log(`  ✓ ${name}`);
	} catch (err) {
		failures.push({ name, message: err && err.message ? err.message : String(err) });
		console.log(`  ✗ ${name}\n      ${err && err.message}`);
	}
}
function ok(cond, msg) {
	if (!cond) throw new Error(msg || "斷言失敗");
}
function eq(actual, expected, msg) {
	if (actual !== expected) throw new Error(`${msg || "值不符"}：預期 ${JSON.stringify(expected)}，實際 ${JSON.stringify(actual)}`);
}
function near(actual, expected, tol, msg) {
	if (!(Math.abs(actual - expected) <= tol)) {
		throw new Error(`${msg || "數值不符"}：預期 ${expected} ±${tol}，實際 ${actual}`);
	}
}
/** 深掃：任何一個 NaN / Infinity / null / undefined 都要被抓出來 */
function scanBad(node, path, out = []) {
	if (node === undefined) out.push(`${path}=undefined`);
	else if (node === null) out.push(`${path}=null`);
	else if (typeof node === "number") { if (!Number.isFinite(node)) out.push(`${path}=${node}`); }
	else if (Array.isArray(node)) node.forEach((v, i) => scanBad(v, `${path}[${i}]`, out));
	else if (typeof node === "object") for (const k of Object.keys(node)) scanBad(node[k], `${path}.${k}`, out);
	return out;
}
function deepEqual(a, b, path = "$") {
	if (typeof a !== typeof b) throw new Error(`${path} 型別不同：${typeof a} vs ${typeof b}`);
	if (a && b && typeof a === "object") {
		const ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
		if (ka.join(",") !== kb.join(",")) throw new Error(`${path} 欄位不同：${ka} vs ${kb}`);
		ka.forEach((k) => deepEqual(a[k], b[k], `${path}.${k}`));
		return true;
	}
	if (!Object.is(a, b)) throw new Error(`${path} 不相等：${JSON.stringify(a)} vs ${JSON.stringify(b)}`);
	return true;
}

// ─────────────────────────────────────────────────────────────
// 測試資料
// ─────────────────────────────────────────────────────────────

const ts = (key, fn, from = 2000, to = 2024, label) => ({
	key,
	label: label || key,
	unit: "人",
	points: Array.from({ length: to - from + 1 }, (_, i) => ({ x: from + i, y: fn(from + i, i) })),
});

/** 決定性偽亂數（整數運算，不用 Math.random 也不用 Math.sin，跨機器結果一致） */
function prand(seed) {
	let x = (Math.imul(seed, 1103515245) + 12345) >>> 0;
	x ^= x << 13; x >>>= 0;
	x ^= x >>> 17;
	x ^= x << 5; x >>>= 0;
	return x / 4294967296;
}

const DISTRICTS = [
	"板橋區", "新莊區", "中和區", "三重區", "新店區", "土城區", "永和區", "蘆洲區", "汐止區", "樹林區",
	"淡水區", "三峽區", "林口區", "五股區", "泰山區", "鶯歌區", "深坑區", "八里區", "瑞芳區", "三芝區",
	"金山區", "萬里區", "石碇區", "坪林區", "石門區", "雙溪區", "貢寮區", "烏來區", "平溪區",
];

/** 29 區 × 25 年，量級照真實人口分布（板橋最大、平溪最小），雜訊是決定性的 */
function realisticDataset() {
	return DISTRICTS.map((name, d) => {
		const base = Math.round(60000 * Math.pow(0.82, d)) + 300;
		const drift = (prand(d * 7 + 1) - 0.5) * 0.02;           // 每年 ±1% 的長期趨勢
		return ts(name, (year, i) => {
			const noise = (prand(d * 1000 + i) - 0.5) * 0.03;
			return Math.round(base * (1 + drift * i) * (1 + noise));
		});
	});
}

console.log("mining.js 自我測試");
console.log("─".repeat(52));

// ─────────────────────────────────────────────────────────────
// 1. 趨勢
// ─────────────────────────────────────────────────────────────

test("完美線性遞增 → 報趨勢，R² ≈ 1，方向 increasing", () => {
	const s = ts("線性上升", (y) => 1000 + 200 * (y - 2000));
	const [c] = detectTrend(s);
	ok(c, "應該要報一個趨勢候選");
	eq(c.type, "trend");
	near(c.facts.r2, 1, 1e-9, "R²");
	near(c.facts.slope, 200, 1e-9, "斜率");
	eq(c.facts.direction, "increasing");
	near(c.facts.pct_change, 480, 1e-6, "25 年成長率 %");   // (1000+4800-1000)/1000
	near(c.statistical_strength, 1, 1e-9, "調整後 R²");
	ok(c.effect_size > 0.9, `效果大小應該接近 1，實際 ${c.effect_size}`);
});

test("完美線性遞減 → 方向 decreasing，效果大小一樣正規化到 0..1", () => {
	const [c] = detectTrend(ts("線性下降", (y) => 20000 - 400 * (y - 2000)));
	ok(c, "應該要報趨勢");
	eq(c.facts.direction, "decreasing");
	ok(c.effect_size >= 0 && c.effect_size <= 1, "效果大小越界");
	ok(c.facts.rel_change < 0, "相對變化應為負");
});

test("完全水平 → 不報趨勢（不是報一個效果 0 的候選）", () => {
	eq(detectTrend(ts("水平", () => 5000)).length, 0, "水平線不該有候選");
});

test("25 年只動 0.3% 的完美直線 → R² = 1 但不值得報", () => {
	// 這關擋的是「統計上完美、實務上沒意義」的結論：
	// 只看 R² 的話它會被排在所有真實趨勢前面。
	const [c] = detectTrend(ts("幾乎不動", (y) => 10000 + 0.12 * (y - 2000)));
	eq(c, undefined, "相對變化 0.3% 不該報");
});

test("缺年（稀疏資料）→ 依 x 排序、不假設等距，斜率仍正確", () => {
	const s = {
		key: "稀疏",
		label: "稀疏",
		// 故意亂序 + 缺 2003、2005、2008
		points: [2010, 2000, 2004, 2002, 2006, 2001, 2007, 2009].map((y) => ({ x: y, y: 100 + 10 * (y - 2000) })),
	};
	const before = s.points.map((p) => p.x).join(",");
	const [c] = detectTrend(s);
	ok(c, "應該要報趨勢");
	near(c.facts.slope, 10, 1e-9, "斜率不該被亂序或缺年影響");
	eq(c.facts.first_x, 2000, "x_range 起點");
	eq(c.facts.last_x, 2010, "x_range 終點");
	eq(s.points.map((p) => p.x).join(","), before, "偵測器不得就地排序呼叫端的 points");
});

// ─────────────────────────────────────────────────────────────
// 2. 變點
// ─────────────────────────────────────────────────────────────

test("先升後降 → 變點落在真正的轉折年（2012）", () => {
	const peak = 2012;
	const s = ts("先升後降", (y) => (y <= peak ? 1000 + 50 * (y - 2000) : 1600 - 80 * (y - peak)));
	const [c] = detectChangePoint(s);
	ok(c, "應該要報變點");
	eq(c.facts.year, peak, "轉折年");
	near(c.facts.slope_before, 50, 1e-6, "轉折前斜率");
	near(c.facts.slope_after, -80, 1e-6, "轉折後斜率");
	near(c.facts.improvement, 1, 1e-6, "誤差改善率");
	ok(c.effect_size > 0.9, `完全反轉的效果大小應接近 1，實際 ${c.effect_size}`);
});

test("階梯式跳動（兩段都水平）→ 斜率差是 0/0，改用平均落差當效果大小", () => {
	const s = ts("階梯", (y) => (y < 2015 ? 1000 : 3000));
	const [c] = detectChangePoint(s);
	ok(c, "階梯也是變點");
	eq(c.facts.year, 2014, "最後一個仍屬舊水準的年份");
	ok(Number.isFinite(c.effect_size) && c.effect_size > 0, "效果大小不得是 NaN 或 0");
	near(c.facts.mean_before, 1000, 1e-6);
	near(c.facts.mean_after, 3000, 1e-6);
});

test("純直線 → 不報變點（單一直線已完美貼合，improvement 是 0/0）", () => {
	eq(detectChangePoint(ts("直線", (y) => 500 + 30 * (y - 2000))).length, 0);
});

test("點數不足以切兩段 → 不報變點、不丟例外", () => {
	eq(detectChangePoint(ts("短", (y) => y - 2000, 2000, 2004)).length, 0, "5 點但 minSegment=3 切不出來");
});

// ─────────────────────────────────────────────────────────────
// 3. 異常值
// ─────────────────────────────────────────────────────────────

test("一個極端區混在 10 個相近區裡 → 抓得到，而且只抓它", () => {
	const values = [50000, 51200, 49300, 50800, 48700, 52100, 49900, 50400, 51500, 240000];
	const s = {
		key: "youth_2024",
		label: "20-24歲人口（2024）",
		points: DISTRICTS.slice(0, 10).map((d, i) => ({ x: d, y: values[i] })),
	};
	const found = detectOutlier(s);
	eq(found.length, 1, "只有一個異常值");
	eq(found[0].facts.x, DISTRICTS[9], "抓到的應該是那個極端區");
	ok(Math.abs(found[0].facts.robust_z) > 3.5, "robust z 應遠超門檻");
	eq(found[0].facts.detrended, false, "分區比較沒有趨勢可去");
	ok(found[0].effect_size > 0.5, "偏離水準超過一半");
});

test("平均值+標準差會漏掉的異常值，MAD 抓得到（這就是不用 σ 的理由）", () => {
	// 板橋級的巨大值把 σ 撐大，讓真正的第二個異常（0 人）躲在 ±2σ 內。
	const values = [50000, 51200, 49300, 50800, 48700, 52100, 49900, 50400, 240000, 0];
	const pts = DISTRICTS.slice(0, 10).map((d, i) => ({ x: d, y: values[i] }));
	const mu = values.reduce((a, b) => a + b, 0) / values.length;
	const sd = Math.sqrt(values.reduce((a, b) => a + (b - mu) ** 2, 0) / values.length);
	const zClassic = Math.abs(0 - mu) / sd;
	ok(zClassic < 2, `古典 z 應該看不到那個 0（實際 ${zClassic.toFixed(2)}）`);
	const keys = detectOutlier({ key: "s", label: "s", points: pts }).map((c) => c.facts.x);
	ok(keys.includes(DISTRICTS[9]), "MAD 應該要抓到那個 0 人的區");
});

test("MAD = 0（過半數值完全相同）→ 改用平均絕對離差，不除以 0", () => {
	const pts = DISTRICTS.slice(0, 10).map((d, i) => ({ x: d, y: i === 9 ? 900 : 500 }));
	const found = detectOutlier({ key: "mad0", label: "mad0", points: pts });
	eq(found.length, 1, "MAD=0 時仍要抓得到那個不同的值");
	eq(found[0].facts.method, "MeanAD", "應該切換到備援尺度");
	eq(scanBad(found[0], "c").join(","), "", "不得出現 Infinity");
});

test("強趨勢下的真實異常年 → 去趨勢才抓得到（不去趨勢會被趨勢自身的變異蓋掉）", () => {
	const shock = 2012;
	const s = ts("人口成長中的一次暴跌", (y) => (y === shock ? 500 : 1000 + 40 * (y - 2000)));
	const withDetrend = detectOutlier(s);
	const withoutDetrend = detectOutlier(s, { detrend: false });
	ok(withDetrend.some((c) => c.facts.x === shock), "去趨勢後應抓到 2012");
	eq(withoutDetrend.length, 0, "不去趨勢時這個真實事件會被整條趨勢的變異蓋掉");
	const c = withDetrend.find((x) => x.facts.x === shock);
	eq(c.facts.detrended, true);
	ok(c.facts.expected > 1400, "expected 應該是趨勢線預測值，不是整體中位數");
});

test("平穩序列的頭尾不會被誤報成異常值", () => {
	// MAD 在均勻分布上最大只到 ~1.35 個 robust z，遠低於 3.5 門檻。
	const found = detectOutlier(ts("平穩成長", (y) => 1000 + 40 * (y - 2000)), { detrend: false });
	eq(found.length, 0, "單調直線不該有任何一年是異常值");
});

// ─────────────────────────────────────────────────────────────
// 4. 組間差異 / 名次變動
// ─────────────────────────────────────────────────────────────

test("組間差異 → 找出最高與最低，效果大小正規化", () => {
	const list = [
		ts("板橋區", () => 50000),
		ts("中和區", () => 30000),
		ts("平溪區", () => 400),
	];
	const [c] = detectGroupDifference(list);
	ok(c, "應該要報組間差異");
	eq(c.facts.high_key, "板橋區");
	eq(c.facts.low_key, "平溪區");
	near(c.facts.gap, 49600, 1e-6, "最大差距");
	ok(c.effect_size > 0.9 && c.effect_size <= 1, "相對差距應接近 1");
	near(c.facts.eta_squared, 1, 1e-9, "組內零變異 → η² = 1");
});

test("組間完全相同 → 不報差異", () => {
	eq(detectGroupDifference([ts("A", () => 100), ts("B", () => 100)]).length, 0);
});

test("沒有共同年份的兩條序列 → 大聲失敗，不硬比", () => {
	let threw = false;
	try {
		detectGroupDifference([ts("A", () => 100, 2000, 2010), ts("B", () => 200, 2015, 2024)]);
	} catch (e) { threw = /共同的 x/.test(e.message); }
	ok(threw, "不同時間範圍必須丟例外");
});

test("一個區在頭尾之間超車另一個區 → 報名次變動與方向", () => {
	const list = [
		ts("領先區", () => 2000),
		ts("持平區", () => 1000),
		ts("追上區", (y) => 800 + 16 * (y - 2000)),   // 2024 時是 1184，超過持平區
		ts("墊底區", () => 300),
	];
	const found = detectRankChange(list);
	ok(found.length >= 1, "應該要報名次變動");
	const up = found.find((c) => c.facts.key === "追上區");
	ok(up, "追上區應該在名次變動裡");
	eq(up.facts.direction, "up");
	eq(up.facts.rank_first, 3);
	eq(up.facts.rank_last, 2);
	eq(up.facts.delta_ranks, 1);
	ok(up.facts.crossed_keys.includes("持平區"), "被超越的對象要具名");
	// 容差是 1e-6 而不是 1e-9：mining.js 刻意把所有輸出收到小數 6 位，
	// 讓報告文字與圖表標籤不會出現 0.3333333333333333 這種尾巴。
	near(up.effect_size, 1 / 3, 1e-6, "4 條數列裡移動 1 名 = 1/3");
	eq(up.statistical_strength, 1, "頭尾差距都遠大於 2%，強度應該滿分");
});

test("差距只有 0.05% 的名次互換 → 報得出來但統計強度極低", () => {
	const list = [
		ts("甲區", (y) => (y === 2000 ? 10000 : 10000)),
		ts("乙區", (y) => (y === 2000 ? 9995 : 10005)),
	];
	const found = detectRankChange(list);
	ok(found.length >= 1, "名次確實換了");
	ok(found[0].statistical_strength < 0.2, `四捨五入等級的互換強度應該很低，實際 ${found[0].statistical_strength}`);
});

// ─────────────────────────────────────────────────────────────
// 5. 退化輸入
// ─────────────────────────────────────────────────────────────

test("2 點序列 → 所有偵測器回 []，不丟例外、不產生 NaN", () => {
	const s = { key: "兩點", label: "兩點", points: [{ x: 2000, y: 100 }, { x: 2001, y: 200 }] };
	eq(detectTrend(s).length, 0, "趨勢");
	eq(detectChangePoint(s).length, 0, "變點");
	eq(detectOutlier(s).length, 0, "異常值");
	const all = mineAll([s], { metric: "percent7" });
	eq(all.length, 0, "mineAll 不該生出任何候選");
	eq(all.errors.length, 0, "也不該有錯誤");
});

test("所有值完全相同 → 無候選、無 NaN（R² 與 MAD 在這裡都是 0/0）", () => {
	const list = DISTRICTS.slice(0, 6).map((d) => ts(d, () => 1234));
	const out = mineAll(list, { metric: "percent7", metric_label: "20-24歲人口" });
	eq(out.length, 0, `零變異不該產生任何候選，實際 ${JSON.stringify(out.map((c) => c.type))}`);
	eq(out.errors.length, 0, "不該有錯誤");
});

test("年份重複 → 大聲失敗；mineAll 收成 errors 而不是整批陣亡", () => {
	const dup = { key: "重複年", label: "重複年", points: [2000, 2000, 2001, 2002, 2003].map((y) => ({ x: y, y: 10 })) };
	let threw = false;
	try { detectTrend(dup); } catch (e) { threw = /重複出現/.test(e.message); }
	ok(threw, "偵測器必須丟例外");

	const good = ts("正常區", (y) => 1000 + 100 * (y - 2000));
	const out = mineAll([dup, good], { metric: "percent7" });
	ok(out.length > 0, "另一條正常序列的結論不該跟著消失");
	ok(out.errors.length > 0, "錯誤要被記下來");
	ok(out.errors.every((e) => e.scope === "重複年" || e.scope === "*"), "錯誤要指得出是哪一條");
});

test("y 是 NaN / 缺欄位 → validateSeries 擋下，不安靜地算", () => {
	let n = 0;
	for (const bad of [
		{ key: "壞", label: "壞", points: [{ x: 2000, y: NaN }, { x: 2001, y: 1 }, { x: 2002, y: 2 }] },
		{ key: "", label: "沒 key", points: [{ x: 2000, y: 1 }] },
		{ key: "沒點", label: "沒點", points: [] },
	]) {
		try { detectTrend(bad); } catch { n++; }
	}
	eq(n, 3, "三種壞輸入都要丟例外");
});

// ─────────────────────────────────────────────────────────────
// 6. 契約與決定性（真實規模）
// ─────────────────────────────────────────────────────────────

test("29 區 × 25 年：每一個候選都通過 validateCandidate", () => {
	const out = mineAll(realisticDataset(), { metric: "percent7", metric_label: "20-24歲人口", unit: "人" });
	ok(out.length > 0, "真實規模的資料應該挖得到東西");
	out.forEach((c, i) => {
		const v = validateCandidate(c);
		ok(v.ok, `候選 #${i}（${c.type}）不合契約：${v.errors.join("；")}`);
	});
	eq(out.errors.length, 0, `不該有偵測器失敗：${JSON.stringify(out.errors)}`);
});

test("29 區 × 25 年：輸出不含 NaN / Infinity / null / undefined", () => {
	const out = mineAll(realisticDataset(), { metric: "percent7", metric_label: "20-24歲人口" });
	const bad = scanBad([...out], "out");
	eq(bad.join(", "), "", "輸出裡出現非有限數值");
	out.forEach((c) => {
		ok(c.effect_size >= 0 && c.effect_size <= 1, `effect_size 越界：${c.effect_size}`);
		ok(c.statistical_strength >= 0 && c.statistical_strength <= 1, `statistical_strength 越界：${c.statistical_strength}`);
		ok(Array.isArray(c.evidence.series_keys) && c.evidence.series_keys.length > 0, "evidence.series_keys");
		ok(c.evidence.x_range.length === 2 && Number.isInteger(c.evidence.n), "evidence.x_range / n");
	});
});

test("輸出不得含任何自然語言欄位（敘述是 Report Renderer 的工作）", () => {
	const out = mineAll(realisticDataset(), { metric: "percent7" });
	const banned = ["description", "summary", "text", "narrative", "conclusion", "claim", "title"];
	out.forEach((c) => banned.forEach((k) => {
		ok(!(k in c), `候選頂層出現 ${k}`);
		ok(!(k in c.facts), `facts 出現 ${k}`);
	}));
});

test("決定性：同一份輸入跑兩次，結果深度相等", () => {
	const meta = { metric: "percent7", metric_label: "20-24歲人口", unit: "人" };
	const a = mineAll(realisticDataset(), meta);
	const b = mineAll(realisticDataset(), meta);
	eq(a.length, b.length, "候選數量");
	deepEqual([...a], [...b]);
	eq(JSON.stringify([...a]), JSON.stringify([...b]), "JSON 也必須一模一樣");
});

test("mineAll 不修改輸入資料", () => {
	const list = realisticDataset();
	const before = JSON.stringify(list);
	mineAll(list, { metric: "percent7" });
	eq(JSON.stringify(list), before, "輸入被就地改動了——下一次呼叫的結果就會不同");
});

// ─────────────────────────────────────────────────────────────

console.log("─".repeat(52));
const total = passed + failures.length;
if (failures.length) {
	console.log(`\n失敗 ${failures.length} 項：`);
	failures.forEach((f) => console.log(`  • ${f.name}：${f.message}`));
}
console.log(`mining.js: ${passed}/${total} 通過`);
process.exit(failures.length ? 1 : 0);
