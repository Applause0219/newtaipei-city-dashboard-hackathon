// 資料層：PostgreSQL → Series[]
//
// Mining Engine 只吃純陣列（{key, label, points:[{x,y}]}），不碰資料庫。
// 這支檔案是唯一的橋。分開的理由不是潔癖，是兩件實際的事：
//
//   1. 分析函式要能單獨測試。接了 DB 的函式沒辦法在沒有資料庫的機器上跑，
//      而比賽當天最可能壞掉的就是資料庫。
//   2. 每一筆 Insight 都必須附上「到底用什麼 SQL 算出來」。所以這裡的每個
//      fetch 都回傳 { series, sql, snapshot_at }——provenance 是順手帶出來的，
//      不是事後補的。事後補的東西一定會有補漏的那一筆。
//
// 走 psql 而不是 pg 套件，與 catalog.js 同樣理由：不增加相依。

import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);
const DB = process.env.DASHBOARD_DB || "dashboard";
const MAX = 32 * 1024 * 1024;

/** SQL 字串常值：單引號成對跳脫 */
const lit = (s) => "'" + String(s).replace(/'/g, "''") + "'";

/**
 * 識別字：只允許中英數與底線，其餘一律**拒絕**（不是跳脫，是拒絕）。
 * 與 component-spec.js 同一套規則。刻意各自持有一份而不共用：
 * 這是安全邊界，讓它跨檔案相依，等於多一個能被改壞而不自覺的地方。
 */
function quoteIdent(name) {
	if (!/^[一-鿿\w]+$/.test(name)) throw new Error(`識別字含不允許的字元：${name}`);
	return '"' + name + '"';
}

async function psqlJSON(sql, db) {
	const wrapped = `SELECT coalesce(json_agg(_row_), '[]'::json) FROM (${sql}) _row_`;
	const { stdout } = await run("psql", ["-d", db || DB, "-tAc", wrapped], { maxBuffer: MAX });
	return JSON.parse(stdout.trim() || "[]");
}

/**
 * 把多個年齡組距欄位加成一個數值。
 *
 * **這裡不檢查加總合不合法**——那是 age.js 的 canAggregate 的責任。
 * 呼叫端必須先問過它。會這樣切是因為「哪些欄位可以加」是統計定義問題
 * （人口數可加、失業率不可加），屬於年齡引擎；「怎麼把它們加起來」才是這裡的事。
 *
 * coalesce 是必要的：任何一欄是 NULL，整個加法就變 NULL，
 * 那一個行政區會從結果裡安靜消失，而不是報錯。
 */
function sumExpr(columns) {
	if (!Array.isArray(columns) || columns.length === 0) throw new Error("至少要一個欄位");
	return columns.map((c) => `coalesce(${quoteIdent(c)}, 0)`).join(" + ");
}

/** 組 WHERE。gender 用 統計類型；預設「計」＝男女合計。 */
function whereParts({ gender, year, districts, excludeTotal, totalLabel, areaCol, yearCol, genderCol }) {
	const parts = [];
	if (gender) parts.push(`${quoteIdent(genderCol)} = ${lit(gender)}`);
	if (year != null) parts.push(`${quoteIdent(yearCol)} = ${Number(year)}`);
	// 合計列的「區域別」是「新北市」（臺北那張表是「總計」）。忘了排除，
	// 合計會被當成一個行政區畫進圖裡，而且它永遠是最大的那一根。
	if (excludeTotal && totalLabel) parts.push(`${quoteIdent(areaCol)} <> ${lit(totalLabel)}`);
	if (Array.isArray(districts) && districts.length) {
		parts.push(`${quoteIdent(areaCol)} IN (${districts.map(lit).join(", ")})`);
	}
	return parts;
}

const DEFAULTS = {
	areaCol: "區域別",
	yearCol: "年份",
	genderCol: "統計類型",
	gender: "計",
	totalLabel: "新北市",
	excludeTotal: true,
};

/**
 * 每個行政區一條時間序列。x = 年份（number）。
 *
 * 用途：Trend、Change Point——兩者都需要同一個對象在時間上的變化。
 *
 * @returns {Promise<{series:Series[], sql:string, snapshot_at:string}>}
 */
export async function fetchDistrictYearSeries(opts) {
	const o = { ...DEFAULTS, ...opts };
	const { table, columns, db } = o;
	const t = `public.${quoteIdent(table)}`;
	const area = quoteIdent(o.areaCol);
	const year = quoteIdent(o.yearCol);

	const w = whereParts(o);
	const sql = [
		`SELECT ${area} AS k, ${year} AS x, (${sumExpr(columns)}) AS y`,
		`FROM ${t}`,
		w.length ? `WHERE ${w.join("\n  AND ")}` : "",
		// 順序固定是正確性問題不是排版問題：下游按列序處理，
		// 同一個 key 的點如果不是照年份排好，斜率會算成別的東西。
		`ORDER BY ${area}, ${year}`,
	].filter(Boolean).join("\n");

	const rows = await psqlJSON(sql, db);
	const byKey = new Map();
	for (const r of rows) {
		if (!byKey.has(r.k)) byKey.set(r.k, { key: r.k, label: r.k, points: [], unit: o.unit });
		byKey.get(r.k).points.push({ x: Number(r.x), y: Number(r.y) });
	}
	return { series: [...byKey.values()], sql, snapshot_at: new Date().toISOString() };
}

/**
 * 單一年度的橫斷面：一條序列，x = 行政區（string）。
 *
 * 用途：Outlier、Group Difference——「哪一區不一樣」而不是「什麼時候變了」。
 *
 * @returns {Promise<{series:Series[], sql:string, snapshot_at:string}>}
 */
export async function fetchDistrictSnapshot(opts) {
	const o = { ...DEFAULTS, ...opts };
	const { table, columns, db, label } = o;
	const t = `public.${quoteIdent(table)}`;
	const area = quoteIdent(o.areaCol);
	const year = quoteIdent(o.yearCol);

	// 沒指定年份就取最新的一年。寫成子查詢而不是先查一次再帶入，
	// 是為了讓回傳的 SQL 本身就能單獨重跑——provenance 要能複製貼上就重現。
	const yearCond = o.year != null
		? `${year} = ${Number(o.year)}`
		: `${year} = (SELECT max(${year}) FROM ${t})`;

	const w = whereParts({ ...o, year: null });
	const sql = [
		`SELECT ${area} AS x, (${sumExpr(columns)}) AS y, ${year} AS yr`,
		`FROM ${t}`,
		`WHERE ${[yearCond, ...w].join("\n  AND ")}`,
		`ORDER BY y DESC, ${area}`,
	].join("\n");

	const rows = await psqlJSON(sql, db);
	const yr = rows.length ? Number(rows[0].yr) : null;
	const series = [{
		key: label || "snapshot",
		label: label || (yr != null ? `${yr} 年` : "snapshot"),
		points: rows.map((r) => ({ x: r.x, y: Number(r.y) })),
		unit: o.unit,
	}];
	return { series, sql, snapshot_at: new Date().toISOString(), year: yr };
}

/**
 * 依某個分類欄位切成多條時間序列（預設切性別）。x = 年份。
 *
 * 用途：Group Difference、Rank Change 的跨群比較。
 *
 * 注意 gender 預設值在這裡要拿掉——整個重點就是不要只取「計」。
 * 這是 ComponentSpec 曾經表達不出來的那個維度（同一欄位、不同篩選值），
 * 在分析層必須一開始就支援，否則「各區女性青年人口」會安靜地回答男女合計。
 */
export async function fetchSplitYearSeries(opts) {
	const o = { ...DEFAULTS, gender: null, ...opts };
	const { table, columns, db, splitCol = o.genderCol, splitValues, district } = o;
	const t = `public.${quoteIdent(table)}`;
	const split = quoteIdent(splitCol);
	const year = quoteIdent(o.yearCol);
	const area = quoteIdent(o.areaCol);

	const parts = [];
	if (Array.isArray(splitValues) && splitValues.length) {
		parts.push(`${split} IN (${splitValues.map(lit).join(", ")})`);
	}
	if (district) parts.push(`${area} = ${lit(district)}`);
	else if (o.excludeTotal && o.totalLabel) parts.push(`${area} <> ${lit(o.totalLabel)}`);

	// 沒指定行政區就跨區加總。人口數可加總，所以這是合法的；
	// 若之後要支援比例型指標，這裡必須改成分子分母各自加總後再相除。
	const agg = district ? `(${sumExpr(columns)})` : `sum(${sumExpr(columns)})`;
	const group = district ? "" : `GROUP BY ${split}, ${year}`;

	const sql = [
		`SELECT ${split} AS k, ${year} AS x, ${agg} AS y`,
		`FROM ${t}`,
		parts.length ? `WHERE ${parts.join("\n  AND ")}` : "",
		group,
		`ORDER BY ${split}, ${year}`,
	].filter(Boolean).join("\n");

	const rows = await psqlJSON(sql, db);
	const byKey = new Map();
	for (const r of rows) {
		if (!byKey.has(r.k)) byKey.set(r.k, { key: r.k, label: r.k, points: [], unit: o.unit });
		byKey.get(r.k).points.push({ x: Number(r.x), y: Number(r.y) });
	}
	return { series: [...byKey.values()], sql, snapshot_at: new Date().toISOString() };
}

/** 連線檢查。比賽當天第一個該跑的東西。 */
export async function ping(db) {
	try {
		const rows = await psqlJSON("SELECT 1 AS ok", db);
		return { ok: rows.length === 1, db: db || DB };
	} catch (err) {
		return { ok: false, db: db || DB, error: String(err.message || err) };
	}
}

export const _internals = { quoteIdent, sumExpr, lit };
