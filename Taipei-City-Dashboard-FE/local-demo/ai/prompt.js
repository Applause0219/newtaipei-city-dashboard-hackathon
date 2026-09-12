// 把組件的設定與真實數據組成給模型的提問。
//
// 這是決定回答品質的關鍵：官方的 AI 摘要只讀組件說明，所以只會複述用途；
// 我們把「實際數字」也餵進去，模型才講得出有內容的話。

/**
 * @param {object} config  組件設定（component_XXXX.json）
 * @param {object} chart   圖表資料（chart_XXXX.json）
 */
export function buildPrompt(config, chart) {
	const cc = config.chart_config || {};
	const unit = cc.unit || "";
	const lines = [];

	lines.push("你是協助新北市青年局解讀公開統計的資料分析師。");
	lines.push("以下是一個城市儀表板組件的設定與實際數據，請寫一段繁體中文分析。");
	lines.push("");
	lines.push("組件代號：" + config.index);
	lines.push("組件名稱：" + config.name);
	lines.push("資料來源：" + (config.source || "未標示"));
	lines.push("單位：" + (unit || "未標示"));
	if (config.short_desc) lines.push("說明：" + config.short_desc);
	lines.push("");
	lines.push("實際數據：");
	lines.push(formatData(config.query_type, chart, unit));
	lines.push("");
	lines.push("請遵守以下要求：");
	lines.push("1. 必須引用具體數字，不要只描述這個組件的用途。");
	lines.push("2. 指出 2 到 3 個值得注意的地方（例如極值、異常、和直覺不符之處）。");
	lines.push("3. 如果數據有限制或不確定性，明講，不要迴避。");
	lines.push("4. 300 字以內，不要用條列，用連貫的段落。");
	lines.push("5. 只根據上面的數據推論，不要引入外部資訊或臆測。");

	return lines.join("\n");
}

function formatData(type, chart, unit) {
	// three_d：categories + 多個系列
	if (type === "three_d" && chart.categories) {
		const out = [];
		const cats = chart.categories;
		const top = rank(chart, cats);
		out.push("（共 " + cats.length + " 個分類，單位：" + unit + "）");
		out.push("");
		out.push("各分類合計，由高到低：");
		top.forEach((r, i) => out.push("  " + (i + 1) + ". " + r.name + "  " + r.total + unit));
		out.push("");
		out.push("各系列總和：");
		(chart.data || []).forEach((s) => {
			const sum = (s.data || []).reduce((a, b) => a + (+b || 0), 0);
			out.push("  " + s.name + "：" + sum + unit);
		});
		return out.join("\n");
	}

	// two_d：一組 {x, y}
	if (type === "two_d") {
		const pts = ((chart.data || [])[0] || {}).data || [];
		return pts.map((p) => "  " + p.x + "：" + p.y + unit).join("\n");
	}

	return JSON.stringify(chart).slice(0, 2000);
}

// 把每個分類的各系列加總後排序
function rank(chart, cats) {
	const rows = cats.map((c, i) => {
		let total = 0;
		(chart.data || []).forEach((s) => { total += +(s.data || [])[i] || 0; });
		return { name: c, total };
	});
	return rows.sort((a, b) => b.total - a.total);
}
