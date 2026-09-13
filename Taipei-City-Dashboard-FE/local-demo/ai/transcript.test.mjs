// 對話脈絡整形的測試。
//
// 由來：2026-09-12 使用者實測，模型問「要不要把這兩個指標做成圖表？」，
// 使用者回「好」，模型回了一段自我介紹——因為 runAgentLoop 的
// context.messages 寫死成 []，每次呼叫都是全新的對話。
//
// Bedrock 的 Converse API 對 transcript 很嚴格：必須從 user 開始、
// 角色交替。歷史被截斷或同一邊連續講兩則時都會違反，所以要自己修平。
//
// 跑法：node transcript.test.mjs
import { buildTranscript } from "./agent.js";

let pass = 0, fail = 0;
const out = [];
const ok = (name, cond, detail = "") => {
	if (cond) { pass++; out.push(`  ✓ ${name}`); }
	else { fail++; out.push(`  ✗ ${name}${detail ? "\n      " + detail : ""}`); }
};
const group = (t) => out.push(`\n${t}`);
const roles = (m) => m.map((x) => x.role).join(",");
const text = (m, i) => m[i]?.content?.[0]?.text;

// ────────────────────────────────────────────────────────────
group("A. 基本形狀");

const basic = buildTranscript([
	{ role: "user", content: "青年人口多的區租金是不是也高？" },
	{ role: "bot", content: "強正相關，r=0.665。要不要做成圖表？" },
]);
ok("一問一答變成 user,assistant", roles(basic) === "user,assistant", roles(basic));
ok("內容原封不動帶過去", text(basic, 0) === "青年人口多的區租金是不是也高？", text(basic, 0));
ok("bot 要轉成 assistant", basic[1].role === "assistant");
ok("空歷史回空陣列", buildTranscript([]).length === 0);
ok("undefined 不會炸", buildTranscript(undefined).length === 0);

// ────────────────────────────────────────────────────────────
group("B. Bedrock 的硬性要求：從 user 開始、以 assistant 結尾");

const startsBot = buildTranscript([
	{ role: "bot", content: "（被截斷後剩下的前半段回答）" },
	{ role: "user", content: "那租金呢？" },
	{ role: "bot", content: "租金中位數是…" },
]);
ok("開頭是 assistant 時要丟掉，不能送出去", roles(startsBot) === "user,assistant", roles(startsBot));

const endsUser = buildTranscript([
	{ role: "user", content: "第一題" },
	{ role: "bot", content: "第一題的答案" },
	{ role: "user", content: "第二題（還沒答就中斷）" },
]);
ok("結尾是 user 時要丟掉——新問題會接在後面",
	roles(endsUser) === "user,assistant", roles(endsUser));

ok("只有一則使用者訊息時回空（沒有可接的 assistant）",
	buildTranscript([{ role: "user", content: "嗨" }]).length === 0);

// ────────────────────────────────────────────────────────────
group("C. 同一邊連續講兩則要合併，不可以送出兩則同角色訊息");

const dup = buildTranscript([
	{ role: "user", content: "各區租金" },
	{ role: "bot", content: "正文" },
	{ role: "bot", content: "補充說明" },
	{ role: "user", content: "好" },
	{ role: "bot", content: "圖表建好了" },
]);
ok("連續兩則 assistant 要併成一則", roles(dup) === "user,assistant,user,assistant", roles(dup));
ok("合併後兩段內容都要在",
	/正文/.test(text(dup, 1)) && /補充說明/.test(text(dup, 1)), text(dup, 1));

const dupUser = buildTranscript([
	{ role: "user", content: "問題一" },
	{ role: "user", content: "問題二" },
	{ role: "bot", content: "一起回答" },
]);
ok("連續兩則 user 也要併", roles(dupUser) === "user,assistant", roles(dupUser));

// ────────────────────────────────────────────────────────────
group("D. 長度控制：答案常是整張 markdown 表格");

const long = buildTranscript([
	{ role: "user", content: "x".repeat(5000) },
	{ role: "bot", content: "y".repeat(5000) },
]);
ok("單則訊息要截斷到 1800 字", text(long, 0).length === 1800, String(text(long, 0).length));

const many = [];
for (let i = 0; i < 40; i++) {
	many.push({ role: "user", content: `問題${i}` }, { role: "bot", content: `答案${i}` });
}
const trimmed = buildTranscript(many);
ok("只保留最近 6 輪（12 則）", trimmed.length === 12, `${trimmed.length} 則`);
ok("保留的是最新的那幾輪", /問題34/.test(text(trimmed, 0)), text(trimmed, 0));
ok("截斷後仍然 user 開頭、assistant 結尾",
	trimmed[0].role === "user" && trimmed[trimmed.length - 1].role === "assistant",
	roles(trimmed));

// ────────────────────────────────────────────────────────────
group("E. 髒資料不可以讓整輪失敗");

const dirty = buildTranscript([
	{ role: "user", content: "正常問題" },
	{ role: "bot", content: "" },
	{ role: "bot", content: null },
	{ role: "bot", content: "   " },
	{ role: "bot", content: "正常答案" },
]);
ok("空字串、null、純空白都要略過", roles(dirty) === "user,assistant", roles(dirty));
ok("略過空訊息後內容正確", text(dirty, 1) === "正常答案", text(dirty, 1));

ok("角色欄位缺漏時當成 user",
	buildTranscript([{ content: "沒有 role" }, { role: "bot", content: "答" }])[0].role === "user");

// ────────────────────────────────────────────────────────────
console.log(out.join("\n"));
console.log(`\ntranscript.test.mjs: ${pass}/${pass + fail} 通過`);
if (fail) process.exitCode = 1;
