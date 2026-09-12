// 查「官方是不是已經有類似的組件了」。
//
// 為什麼需要這一步：向量檢索與組件生成原本是兩條互不知情的路——
// 檢索自動跑、生成靠按鈕，生成那邊完全不知道檢索找到了什麼，
// 也不知道官方早就有同一個指標的組件。結果就是默默生出重複的東西。
//
// 判斷方式刻意「不」用名稱字串比對。實測看過向量檢索把「全市屋齡分布」
// 排在「扶養比及老化指數」前面——屋齡與人口老化字面接近但語意無關，
// 字串比對會犯同樣的錯。改由 catalog.yaml 人工標註關聯，準確且講得出理由。

const UPSTREAM = process.env.TCD_API || "https://citydashboard.taipei/api/v1";
let cache = null;   // 進程內快取，一次執行只抓一次

/** 取得官方組件清單（失敗就回 null，讓上層知道「查不到」而不是「沒有」） */
export async function loadOfficial() {
	if (cache !== null) return cache;
	try {
		const r = await fetch(`${UPSTREAM}/component/?pagesize=500`, { signal: AbortSignal.timeout(15000) });
		if (!r.ok) throw new Error(String(r.status));
		const j = await r.json();
		cache = Array.isArray(j.data) ? j.data : [];
	} catch {
		cache = null;   // 離線或逾時：回 null，不要當成「官方沒有」
	}
	return cache;
}

/** 依 query_type 說人話——使用者要知道的是「這個組件從哪個角度看」 */
const VIEW = {
	time: "時間趨勢",
	two_d: "分項比較",
	three_d: "分項比較（多數列）",
	percent: "占比",
	map_legend: "地圖圖層",
};

/** 取官方組件實際畫出來的數列名稱。判斷「是不是同一個切面」要看這個。 */
const seriesCache = new Map();
async function officialSeries(id, city) {
	const k = `${id}:${city}`;
	if (seriesCache.has(k)) return seriesCache.get(k);
	let names = null;
	try {
		const r = await fetch(`${UPSTREAM}/component/${id}/chart?city=${city}`, { signal: AbortSignal.timeout(15000) });
		if (r.ok) {
			const j = await r.json();
			names = (j.data || []).map((x) => x.name).filter(Boolean);
		}
	} catch { /* 查不到就當作不知道 */ }
	seriesCache.set(k, names);
	return names;
}

/** 把數列名稱正規化後比對：20_24歲人口數 與 20-24歲人口 應視為同一個 */
const norm = (x) => String(x).replace(/[_\-\s－—]/g, "").replace(/人口數?$/, "");

/**
 * 找出與這份 spec 相關的官方組件。
 * @returns {Promise<{known:boolean, items:Array<{name,index,id,view,types}>}>}
 *          known=false 代表查不到官方清單（離線），不等於沒有
 */
export async function findRelated(spec, catalog) {
	const table = catalog.tables?.[spec.table];
	const wanted = table?.related_official_components || [];
	if (!wanted.length) return { known: true, items: [] };

	const all = await loadOfficial();
	if (all === null) return { known: false, items: [] };

	const mine = new Set((spec.series || []).map((x) => norm(x.label)));

	const items = [];
	for (const c of all.filter((c) => wanted.includes(c.index) && c.city === spec.city)) {
		const theirs = await officialSeries(c.id, spec.city);
		// 同視角（query_type 一樣）且數列有重疊，才算真的可能重複。
		// 只比對資料表會太粗：官方「全市年齡分區」是 0-14/15-64/65+ 三個粗分組，
		// 跟我們做的 5 歲細組距是完全不同的切面，不該被說成「已經有了」。
		const overlap = theirs ? theirs.filter((n) => mine.has(norm(n))) : null;
		items.push({
			name: c.name,
			index: c.index,
			id: c.id,
			view: VIEW[c.query_type] || c.query_type,
			types: c.chart_config?.types || [],
			their_series: theirs,
			overlap,
			// 判定為重複：視角相同，而且數列有交集
			duplicate: c.query_type === spec.query_type && !!overlap?.length,
		});
	}

	return { known: true, items };
}
