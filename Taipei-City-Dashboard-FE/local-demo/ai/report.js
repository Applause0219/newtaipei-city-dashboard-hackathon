// InsightArtifact → Markdown 報告
//
// 這支檔案**不做任何計算**。它只是把 artifact 排版出來。
// 這是刻意的：只要 renderer 算一次數字，就會出現「報告寫 6.2%、圖上 5.8%」
// 這種沒人抓得到的錯。所有數值都必須已經在 artifact 裡，這裡只負責搬。
//
// 本檔真正在執行的一條規則是**語域分離**：
//
//   Data Fact           25-34 歲人口於 2024-2025 年下降 6.2%。      ← 資料說的
//   Analytical Insight  下降速度較前期明顯加快，形成趨勢轉折。      ← 我們從資料讀出的
//   Hypothesis          可能與居住成本有關，仍需其他資料驗證。      ← 還沒被證明的
//
// 三者在報告上必須一眼分得出來。做法是結構性的——三個獨立欄位、三個獨立標題，
// 而不是「希望寫的人措辭謹慎」。措辭會鬆，結構不會。
//
// ── 數字的措辭不在這裡改 ──
// 找「為什麼轉折前印成『接近持平』而不是『每年 +79.12』」的人請往 insight.js：
// 敘述字串全部在 buildNarrative() 產生，本檔拿到的 narrative.data_fact 已經是
// 成品字串。可忽略變化率的判斷（describeRate / flatBand）與數字格式
// （decimalsFor）都在那裡，因為那是統計判斷，不是排版。
// 在這裡用正則去改寫已經組好的句子，只會讓兩邊的規則各走各的。

import { POLICY_AGE_LABEL, findCausalMarker } from "./insight.js";

const isStr = (v) => typeof v === "string" && v.length > 0;

/** 三個語域的標題。標題本身就是聲明強度，所以連中英文都固定不改。 */
const REGISTER = Object.freeze({
	fact: "**事實（Data Fact）**",
	insight: "**分析判讀（Analytical Insight）**",
	hypothesis: "**假設（Hypothesis，尚未驗證）**",
});

/** 年齡範圍不同時的統一用語。不讓每則報告各自發明說法。 */
const AGE_MISMATCH_NOTE = "兩資料年齡範圍不同，僅能視為青年族群層級的同步現象。";

const bullets = (items) => items.map((t) => `- ${t}`).join("\n");

/**
 * 把一段文字依因果語氣分流。
 *
 * 為什麼不是直接刪掉或直接印出：
 * 相關不等於因果，但寫的人很容易順手寫成因果。與其信任措辭，
 * 不如在這裡結構性地把它降級——含因果語氣的句子一律移到「假設」區，
 * 永遠不會以事實的身分出現在報告上。
 */
function splitByRegister(text) {
	const marker = findCausalMarker(text);
	if (!marker) return { safe: text, demoted: null };
	return { safe: null, demoted: `${text}（原敘述含因果語氣「${marker}」，本系統無法以此資料證明因果，已降級為假設）` };
}

/**
 * 單則洞察 → Markdown。
 * @param {object} artifact  InsightArtifact
 * @param {number} index     報告中的序號，從 1 開始
 */
export function renderInsight(artifact, index = 1) {
	if (!artifact || typeof artifact !== "object") throw new Error("renderInsight 需要一個 InsightArtifact");
	const s = artifact.scope || {};
	const ev = artifact.evidence || {};
	const n = artifact.narrative || {};
	const out = [];

	out.push(`### Insight ${index}｜${artifact.title}`);
	out.push("");

	// claim 由 buildInsightArtifact 直接從 facts 組成，這裡照印即可。
	// 但仍然檢查一次：renderer 是最後一道關，讓因果句在這裡漏出去就沒有第二次機會了。
	const claim = splitByRegister(artifact.claim);
	const demoted = [];
	if (claim.safe) out.push(claim.safe);
	else { out.push("（本則結論的敘述未通過因果語氣檢查，詳見下方假設區）"); demoted.push(claim.demoted); }
	out.push("");

	// 兩份資料年齡範圍不同時，敘述強度降級。
	// 放在 claim 正下方而不是限制區——限制在最後，讀者往往已經把結論記成因果了。
	if (artifact.claim_strength === "group_level") {
		out.push(`> ${AGE_MISMATCH_NOTE}`);
		out.push("");
	}

	out.push(`**重要程度**：${artifact.validation?.score ?? 0} / 100`);
	out.push("");

	out.push("**分析範圍**");
	out.push(bullets([
		// 法定青年永遠印出來當對照基準，即使實際資料就是 18-35。
		// 少了這一行，讀者無從判斷「25-34」離法定定義有多遠。
		`法定青年：${s.policy_age || POLICY_AGE_LABEL} 歲`,
		`實際資料：${s.actual_age} 歲`,
		`時間：${s.time}`,
		`地區：${s.geography}`,
	]));
	out.push("");

	// ── 語域一：事實 ──
	const facts = (Array.isArray(n.data_fact) ? n.data_fact : (isStr(n.data_fact) ? [n.data_fact] : []))
		.map(splitByRegister);
	const safeFacts = facts.filter((f) => f.safe).map((f) => f.safe);
	facts.filter((f) => f.demoted).forEach((f) => demoted.push(f.demoted));
	if (safeFacts.length) {
		out.push(REGISTER.fact);
		out.push(bullets(safeFacts));
		out.push("");
	}

	// ── 語域二：分析判讀 ──
	if (isStr(n.analytical_insight)) {
		const ins = splitByRegister(n.analytical_insight);
		if (ins.safe) {
			out.push(REGISTER.insight);
			out.push(bullets([ins.safe]));
			out.push("");
		} else {
			demoted.push(ins.demoted);
		}
	}

	// ── 語域三：假設 ──
	// 只要有假設就一定帶「尚未驗證」的標題。假設區可以空，但不能偽裝成前兩區。
	const hypos = [];
	if (isStr(n.hypothesis)) hypos.push(n.hypothesis);
	hypos.push(...demoted);
	if (hypos.length) {
		out.push(REGISTER.hypothesis);
		out.push(bullets(hypos));
		out.push("");
	}

	out.push("**分析方法**");
	const methods = Array.isArray(ev.methods) && ev.methods.length
		? ev.methods
		: (isStr(ev.method) ? ev.method.split(/\s*\+\s*/) : ["（未記錄）"]);
	out.push(bullets(methods));
	out.push("");

	out.push("**資料來源**");
	const sources = [
		...(Array.isArray(ev.dataset_ids) ? ev.dataset_ids : []),
		...(Array.isArray(ev.source_urls) ? ev.source_urls : []),
	];
	// 快照時間屬於來源的一部分：同一張開放資料表下個月就不是這些數字了，
	// 沒有時間戳的「資料來源」無法重現。
	if (isStr(ev.snapshot_at)) sources.push(`資料快照：${ev.snapshot_at}`);
	if (isStr(ev.query_id)) sources.push(`查詢代號：${ev.query_id}`);
	out.push(bullets(sources.length ? sources : ["（未記錄）"]));
	out.push("");

	// 限制區永遠印出來。非 A 級年齡卻沒有限制的 artifact 根本建不出來
	// （validateArtifact 會擋），所以這裡只會在 A 級時看到「無」。
	out.push("**限制**");
	const lims = Array.isArray(artifact.limitations) ? artifact.limitations : [];
	out.push(bullets(lims.length ? lims : ["無額外限制：本結果涵蓋完整法定青年範圍。"]));

	return out.join("\n");
}

/**
 * 整份報告 → Markdown。
 *
 * @param {object[]} artifacts  已排序的 InsightArtifact
 * @param {object|string} [summary]  runFunnel() 的回傳，或已經組好的一行字
 */
export function renderReport(artifacts, summary) {
	const list = Array.isArray(artifacts) ? artifacts : [];
	const out = [];

	out.push("# 青年人口洞察報告");
	out.push("");

	// 漏斗數字放最前面。只給 5 則結論而不講掃了幾個，讀者無法分辨
	// 「隨便挑 5 個」與「擋掉 115 個之後剩 5 個」——那是這份報告全部的可信度來源。
	const line = funnelLine(summary);
	if (line) { out.push(line); out.push(""); }

	out.push(`**法定青年定義**：${POLICY_AGE_LABEL} 歲（《青年基本法》）。`
		+ "本報告所有結論一律以此為對照基準；實際資料涵蓋範圍不同時，於各則「分析範圍」與「限制」中標示。");
	out.push("");

	if (list.length === 0) {
		// 沒有結論是一個合法且必須說得出口的結果。
		// 為了湊滿版面而放行沒通過檢查的候選，正是這整套系統要防的事。
		out.push("本次分析沒有任何候選通過驗證，因此不提出結論。各關卡的拒絕原因見附錄。");
		return out.join("\n");
	}

	out.push("---");
	out.push("");
	list.forEach((a, i) => {
		out.push(renderInsight(a, i + 1));
		out.push("");
		if (i < list.length - 1) { out.push("---"); out.push(""); }
	});

	const rejected = summary && Array.isArray(summary.rejected) ? summary.rejected : [];
	if (rejected.length) {
		out.push("---");
		out.push("");
		out.push("### 附錄｜被拒絕的候選");
		out.push("");
		// 印出「哪一關、什麼理由」而不只是數量：被追問時要能當場交代，
		// 而不是說「驗證器覺得不行」。
		out.push(bullets(rejected.map((r) => `[${r.gate}｜${r.label}] ${r.metric ?? ""} ${r.type ?? ""}：${r.reason}`.replace(/\s+/g, " ").trim())));
	}

	return out.join("\n").replace(/\n+$/, "");
}

/** 把 summary 整理成漏斗那一行。接受字串、runFunnel 回傳、或什麼都沒有。 */
function funnelLine(summary) {
	if (isStr(summary)) return summary;
	if (!summary || typeof summary !== "object") return "";
	if (isStr(summary.line)) return summary.line;
	if (typeof summary.scanned !== "number") return "";
	return `掃描 ${summary.scanned} 個候選 → 通過定義與資料檢查 ${summary.passed_definition} 個`
		+ ` → 通過統計檢查 ${summary.passed_statistical} 個 → 推薦 Top ${summary.recommended}`;
}
