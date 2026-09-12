<script setup>
import { ref, watch, nextTick } from "vue";
import { storeToRefs } from "pinia";
import SendIcon from "../icons/SendIcon.vue";
import BotLogo from "../icons/BotLogo.vue";
import UserLogo from "../icons/UserLogo.vue";

import { useChatStore } from "../../store/chatStore";
import { useContentStore } from "../../store/contentStore";
import http from "../../router/axios";

const chatStore = useChatStore();
const contentStore = useContentStore();
const { addChatData, addQueryData, saveChatLog } = chatStore;
const { chatData } = storeToRefs(chatStore);

const userMessage = ref("");
const chatAreaRef = ref(null);
const inputRef = ref(null);
const isStickyOpen = ref(false);
const agentLoading = ref(false);

// 工具代號 → 人話。使用者看到的是「正在做什麼」，不是函式名。
const TOOL_LABEL = {
	list_domains: "盤點資料主題",
	search_indicators: "搜尋相關指標",
	inspect_indicator: "檢查指標能不能用",
	query_indicator: "查詢實際數值",
	correlate_indicators: "計算相關係數",
	compare_indicators: "計算比例與排名",
	list_official_components: "比對官方既有組件",
	build_component: "建立組件",
};

// 系統建議的問題。點了只**填入輸入框**，不直接送出——
// 使用者要先看清楚自己在問什麼，才有辦法判斷答案對不對。
//
// 這三題都經過 SQL 人工對照驗證（2026-09-12），
// 分別示範跨領域相關、比例排名、時間變化三種能力。
const SUGGESTED = [
	{ label: "青年人口 vs 租金", question: "青年人口多的區，租金是不是也比較高？" },
	{ label: "租金負擔排名", question: "新北市各區的租金中位數佔平均所得的比例是多少？哪一區負擔最重？" },
	{ label: "五年人口流失", question: "過去五年青年人口流失最多的是哪幾區？" },
];

function useSuggestion(q) {
	userMessage.value = q;
	nextTick(() => inputRef.value?.focus());
}

// 問題送出後直接跑 agent。
//
// 原本使用者要先看一份向量檢索的推薦清單，再從「幫我建立組件」與「深入分析」
// 之間選一顆按鈕。那是把內部有幾條管線這件事洩漏到介面上——
// 該走哪條路是系統的責任。現在只有一條路：agent 自己決定查什麼、查幾次、
// 要不要建圖，數字一律由既有的決定性管線算。
async function runAgent(question) {
	if (!question?.trim() || agentLoading.value) return;
	agentLoading.value = true;

	// 把先前的問答一起送過去。
	//
	// 只挑兩種訊息：使用者說的話，以及 agent 的**正式回答**（isAnswer）。
	// 「讓我查一下資料…」「🔍 查詢過程」「📊 已把…加到」是介面鷹架，
	// 送過去只會佔位置又誤導模型，所以刻意排除。
	//
	// 沒有這段的時候，模型問「要不要做成圖表？」使用者回「好」，
	// 它收到的就只有一個「好」，於是回了一段自我介紹。
	const history = chatData.value
		.filter((m) => !m.isDefault && (m.role === "user" || m.isAnswer))
		.map((m) => ({ role: m.role === "user" ? "user" : "assistant", content: m.content }));

	addChatData({ role: "bot", content: "讓我查一下資料…" });
	try {
		const res = await http.post(
			"/agent",
			{ question, history },
			{ headers: { "Content-Type": "application/json" } },
		);
		const r = res.data?.data || {};

		// 先把「做了哪些事」攤開。這是使用者唯一能檢查它有沒有亂編的地方——
		// 每一步都對應一次真實的資料庫查詢。
		const steps = (r.trace || []).map((t, i) => {
			const label = TOOL_LABEL[t.name] || t.name;
			const arg = Object.values(t.args || {}).filter(Boolean).join("、");
			return `${i + 1}. ${label}${arg ? `（${arg}）` : ""} → ${t.summary}`;
		});
		if (steps.length) {
			addChatData({
				role: "bot",
				content: `🔍 查詢過程（${steps.length} 步，${(r.ms / 1000).toFixed(1)} 秒）\n\n${steps.join("\n")}`,
			});
		}

		// 有建組件就掛上去
		const mounted = [];
		for (const c of r.components || []) {
			if (mountGenerated({ ...c, model: r.model })) mounted.push(c.spec.name);
		}

		// isAnswer 標記：只有這種訊息會被當成對話脈絡送回給模型
		addChatData({ role: "bot", isAnswer: true, content: r.text || "（沒有回應內容）" });

		if (mounted.length) {
			addChatData({
				role: "bot",
				content: `📊 已把 ${mounted.map((n) => `「${n}」`).join("、")} 加到左側儀表板最上方 👈\n重新整理頁面就會移除。`,
			});
		}
		saveChatLog(question, r.text || "");
	} catch (e) {
		addChatData({
			role: "bot",
			content: `分析時出錯了：${e?.response?.data?.message || e.message}`,
		});
	} finally {
		agentLoading.value = false;
	}
}

const PALETTE = ["#5a9cf8", "#56B96D", "#F8CF58", "#F5AD4A", "#E170A6", "#ED6A45", "#AF4137", "#10294A"];
let genSeq = 0;

function mountGenerated(r) {
	const s = r.spec;
	const cur = contentStore.currentDashboard;
	const city = contentStore.cityDashboard;
	if (!cur || !city || !Array.isArray(city.components)) return false;

	// 官方會依 city 過濾組件（contentStore.js:685-695），
	// 城市對不上就算推進去也不會顯示，不如誠實說做不到
	if (cur.city && s.city !== cur.city) return false;

	// 要推進 cityDashboard 而不是 currentDashboard：
	// 儀表板有定期自動更新，updateCurrentDashboardAllChartData 結束時會呼叫
	// filterCurrentDashboardContent()，那個函式是「從 cityDashboard.components
	// 重建 currentDashboard.components」。只推後者的話，下一次更新就被洗掉。
	const id = 90001 + genSeq++;
	city.components.unshift({
		id,
		// 標記給 contentStore 的定期更新看：這個組件的資料已經在 chart_data 裡，
		// 不要拿 id 去後端重抓（抓不到，會被清空成全暗的 0）。
		generated: true,
		index: s.index,
		name: s.name,
		city: s.city,
		query_type: s.query_type,
		source: `自動生成 · ${r.model}`,
		short_desc: s.short_desc || "",
		long_desc: s.long_desc || "",
		use_case: "",
		links: [],
		contributors: [],
		history_config: null,
		map_config: [null],
		map_filter: null,
		time_from: "static",
		time_to: null,
		update_freq: null,
		update_freq_unit: null,
		updated_at: new Date().toISOString(),
		chart_config: {
			index: s.index,
			color: PALETTE.slice(0, Math.max(r.chart.data.length, 1)),
			types: s.chart.types,
			unit: s.chart.unit || "",
			// two_d 沒有 categories，圖表元件也不會去讀它
			...(r.chart.categories ? { categories: r.chart.categories } : {}),
		},
		chart_data: r.chart.data,
	});
	contentStore.filterCurrentDashboardContent();
	return true;
}

const sendBtnHandler = (text) => {
	if (!text.trim() || agentLoading.value) return;
	addQueryData({
		role: "user",
		content: text,
	});
	userMessage.value = "";
	runAgent(text);
};

const toggleSticky = () => {
	isStickyOpen.value = !isStickyOpen.value;
};

watch(
	() => chatData.value.length,
	async () => {
		await nextTick();
		const chat = chatAreaRef.value;
		if (!chat) return;
		chat.scrollTop = chat.scrollHeight - chat.clientHeight;
	},
	{ deep: true },
);

// 側邊欄形式需要面板自己有個關閉入口——展開時視線在右側，
// 不會回到左下角那顆浮動按鈕去找。
//
// 刻意宣告在 script 區塊最後：另一條 AI 生成分支改的是前半段，
// 兩邊 merge 時不會落在同一個 hunk。
const emit = defineEmits(["close"]);
</script>

<template>
  <div class="chat-widget">
    <!-- 標題 -->
    <div class="header">
      <h3>臺北城市儀表板小幫手</h3>
      <button
        class="close-btn"
        aria-label="關閉小幫手"
        @click="emit('close')"
      >
        ×
      </button>
    </div>

    <!-- 聊天區 -->
    <div
      ref="chatAreaRef"
      class="chat-area scrollbar-custom"
    >
      <!-- 置頂訊息 -->
      <div class="chat-message sticky-message">
        <div
          class="sticky-header"
          @click="toggleSticky"
        >
          <span>置頂公告：小幫手使用須知</span>
          <button class="toggle-btn">
            {{ isStickyOpen ? "-" : "+" }}
          </button>
        </div>
        <div
          v-show="isStickyOpen"
          class="sticky-body"
        >
          <span>小幫手會依據您輸入的內容，自動檢索本站臺的組件資料庫，並回傳相似度較高的組件清單，協助您快速找到符合需求的元件或資訊。<br><br>
            目前小幫手僅提供組件比對與分析服務，不支援一般聊天功能。如造成不便，敬請見諒！</span>
        </div>
      </div>
      <div
        v-for="chat in chatData"
        :key="chat.id"
        class="message"
      >
        <!-- 機器人訊息 -->
        <div
          v-if="chat.role === 'bot'"
          class="bot"
        >
          <div class="avatar">
            <BotLogo />
          </div>
          <div class="content">
            <div
              v-if="chat.content"
              class="message--bubble"
            >
              <p>{{ chat.content }}</p>
            </div>
          </div>
        </div>
        <!-- 使用者訊息 -->
        <div
          v-else
          class="user"
        >
          <div class="avatar">
            <UserLogo />
          </div>
          <div
            v-if="chat.content"
            class="content"
          >
            <div class="message--bubble">
              <p>{{ chat.content }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 建議問題：點了只填入輸入框，送不送由使用者決定 -->
    <div class="suggest-area">
      <span class="suggest-label">試試看</span>
      <div
        v-horizontal-wheel
        class="suggest-chips scrollbar-x-hide"
      >
        <button
          v-for="s in SUGGESTED"
          :key="s.label"
          class="suggest-chip"
          :title="s.question"
          :disabled="agentLoading"
          @click="useSuggestion(s.question)"
        >
          {{ s.label }}
        </button>
      </div>
    </div>

    <!-- 輸入區 -->
    <div class="input-area">
      <input
        ref="inputRef"
        v-model="userMessage"
        type="text"
        :placeholder="agentLoading ? '分析中，請稍候…' : '輸入訊息...'"
        :disabled="agentLoading"
        @keyup.enter="sendBtnHandler(userMessage)"
      >
      <button
        :disabled="agentLoading"
        @click="sendBtnHandler(userMessage)"
      >
        <SendIcon />
      </button>
    </div>
  </div>
</template>

<style lang="scss" scoped>
/* === 變數設定 === */
$bg-dark: #090909;
$panel-bg: #494b4e;
$card-bg: #282a2c;
$border-color: #888787;
$input-bg: #d9d9d9;
$white: #ffffff;
$scroll-thumb-hover: #ababab;
$radius-10: 10px;
$radius-15: 15px;
$radius-20: 20px;

/* === Scrollbar === */
.scrollbar-x-hide {
	scrollbar-width: none;

	&::-webkit-scrollbar {
		display: none;
	}
}

.scrollbar-custom {
	&::-webkit-scrollbar {
		width: 2px;
		background: transparent;
	}

	&::-webkit-scrollbar-thumb {
		background: $white;
		border-radius: 8px;
	}

	&::-webkit-scrollbar-thumb:hover {
		background: $scroll-thumb-hover;
	}
}

/* === 主要樣式 === */
.chat-widget {
	width: 400px;
	border-radius: $radius-20;
	overflow: hidden;
	background: $bg-dark;
	border: 1px solid $border-color;
	display: flex;
	flex-direction: column;

	.header {
		padding: 1rem;
		background: $panel-bg;
		border-bottom: 3px solid $border-color;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;

		h3 {
			font-size: 18px;
			font-weight: 700;
			color: $white;
			margin: 0;
		}

		.close-btn {
			flex-shrink: 0;
			width: 28px;
			height: 28px;
			display: flex;
			align-items: center;
			justify-content: center;
			font-size: 22px;
			line-height: 1;
			color: $white;
			border-radius: 50%;
			opacity: 0.7;
			transition: opacity 0.2s, background-color 0.2s;

			&:hover {
				opacity: 1;
				background-color: rgba(255, 255, 255, 0.15);
			}
		}
	}

	.chat-area {
		flex: 1;
		margin: 0.25rem;
		padding: 0.75rem;
		overflow-y: auto;
		background: $bg-dark;

		.chat-message {
			padding: 4px 10px;
			margin: 0px 8px;
			border-radius: 8px;
			background-color: $bg-dark;
		}

		// 置頂訊息
		.sticky-message {
			border: 1px solid #ffffff;
			position: sticky;
			top: 0;
			z-index: 10;

			.sticky-header {
				display: flex;
				font-weight: bold;
				justify-content: space-between;
				align-items: center;
				cursor: pointer;
				padding: 8px 12px;
			}

			.sticky-body {
				padding: 8px 12px;
				font-weight: 400;
				font-size: 14px;
			}

			.toggle-btn {
				background: none;
				border: none;
				font-size: 14px;
				cursor: pointer;
				color: #ffffff;
			}
		}

		.message {
			padding: 8px;

			.bot,
			.user {
				display: flex;
				gap: 0.5rem;
				align-items: flex-start;

				&.user {
					flex-direction: row-reverse;
				}

				.avatar {
					width: 40px;
					height: 40px;
					display: flex;
					align-items: center;
					justify-content: center;
					flex-shrink: 0;

					svg {
						width: 100%;
						height: auto;
					}
				}

				.content {
					display: flex;
					flex-direction: column;
					gap: 0.5rem;

					.relation-area {
						width: 100%;
						display: flex;
						align-items: center;
						margin-top: 8px;
						margin-bottom: 8px;

						.relation-table {
							min-width: max-content;
							font-size: 13px;
						}

						.relation-table th,
						.relation-table td {
							border: 1px solid #ccc;
							text-align: left;
							padding: 0px 8px;
							line-height: 1.1;
							vertical-align: middle;
						}

						.relation-table td {
							height: 2.5rem;
						}

						.relation-table th {
							font-weight: bold;
							text-align: center;
						}
					}

					.message--bubble {
						border: 1px solid $white;
						border-radius: $radius-10;
						background: $card-bg;

						p {
							color: $white;
							white-space: pre-line;
							margin: 0;
							padding-top: 8px;
							padding-bottom: 8px;
							padding-left: 16px;
							padding-right: 16px;
							font-size: 16px;
						}
					}

					.message--button {
						display: flex;
						gap: 0.5rem;
						overflow-x: auto;

						button {
							flex-shrink: 0;
							background: $panel-bg;
							color: $white;
							font-size: 14px;
							padding: 0.5rem 1rem;
							border-radius: $radius-15;
							border: none;
							cursor: pointer;
							white-space: nowrap;

							&:hover {
								filter: brightness(0.5);
							}
						}
					}
				}
			}
		}
	}

	// 建議問題：一排可橫向捲動的 chip。
	// 用短標籤而不是整句問題——側邊欄只有 400px，
	// 三整句會佔掉超過一百像素的垂直空間，那是每次對話都要付的成本。
	// 完整問題在 title 提示，點下去也會填進輸入框讓使用者自己看。
	.suggest-area {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.75rem 0.5rem;

		.suggest-label {
			flex-shrink: 0;
			font-size: 12px;
			color: $border-color;
			user-select: none;
		}

		.suggest-chips {
			display: flex;
			gap: 0.375rem;
			overflow-x: auto;
			min-width: 0;
		}

		.suggest-chip {
			flex-shrink: 0;
			padding: 0.25rem 0.625rem;
			font-size: 12px;
			white-space: nowrap;
			color: $white;
			background: $card-bg;
			border: 1px solid $border-color;
			border-radius: 999px;
			transition: background-color 0.2s, border-color 0.2s;

			&:hover:not(:disabled) {
				background: $panel-bg;
				border-color: $white;
			}

			&:disabled {
				opacity: 0.4;
				cursor: not-allowed;
			}
		}
	}

	.input-area {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.5rem;
		padding: 1.5rem 1.125rem;
		background: $panel-bg;

		input[type="text"] {
			background: $white;
			height: 35px;
			width: 100%;
			border-radius: 20px;
			padding: 0 1rem;
			border: none;
			outline: none;
			color: black;
		}

		button {
			height: 35px;
			display: flex;
			align-items: center;
			justify-content: center;
			background: transparent;
			border: none;
			cursor: pointer;

			&:hover {
				filter: brightness(0.5);
			}
		}
	}
}

.theme-btn {
	padding: 0.2rem;
	margin-right: 0.1rem;
	background-color: #5a9cf8;
	border-radius: 2px;
	&:hover {
		background-color: #3a7fd6;
	}
}

@media (max-width: 600px) {
	.chat-widget {
		.chat-area {
			padding-left: 0.5rem;
			padding-right: 0.5rem;
		}
	}
}
</style>
