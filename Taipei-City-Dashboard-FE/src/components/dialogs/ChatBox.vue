<script setup>
import { ref, watch, nextTick } from "vue";
import { storeToRefs } from "pinia";
import SendIcon from "../icons/SendIcon.vue";
import BotLogo from "../icons/BotLogo.vue";
import UserLogo from "../icons/UserLogo.vue";

import { useChatStore } from "../../store/chatStore";
import { useContentStore } from "../../store/contentStore";
import { useAuthStore } from "../../store/authStore";
import http from "../../router/axios";

const chatStore = useChatStore();
const contentStore = useContentStore();
const authStore = useAuthStore();
const { addChatData, addQueryData, saveChatLog } = chatStore;
const { createDashboard } = contentStore;
const { chatData, compToDashIndexMap } = storeToRefs(chatStore);
const { editDashboard } = storeToRefs(contentStore);
const { user } = storeToRefs(authStore);

const userMessage = ref("");
const chatAreaRef = ref(null);
const isStickyOpen = ref(false);
const dashboardCreationLoading = ref(false);
const componentGenLoading = ref(false);
const agentLoading = ref(false);

// 工具代號 → 人話。使用者看到的是「正在做什麼」，不是函式名。
const TOOL_LABEL = {
	list_domains: "盤點資料主題",
	search_indicators: "搜尋相關指標",
	inspect_indicator: "檢查指標能不能用",
	query_indicator: "查詢實際數值",
	list_official_components: "比對官方既有組件",
	build_component: "建立組件",
};

const qaBtnHandler = async (text, relations, chat) => {
	// ── Agent：模型自己決定查什麼、查幾次 ──
	//
	// 與「幫我建立組件」的差別是誰決定步驟：
	//   幫我建立組件  一次呼叫 → spec → 圖。快，可重現。
	//   深入分析      模型搜尋、檢視、查數、建圖，自己修錯。慢，會探索。
	//
	// 兩顆按鈕都留著，因為它們回答的是不同種類的問題。
	if (text === "深入分析") {
		if (agentLoading.value) return;
		agentLoading.value = true;
		addChatData({ role: "bot", content: "讓我查一下資料…" });
		try {
			const res = await http.post(
				"/agent",
				{ question: chat?.question || "" },
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

			addChatData({ role: "bot", content: r.text || "（沒有回應內容）" });

			if (mounted.length) {
				addChatData({
					role: "bot",
					content: `📊 已把 ${mounted.map((n) => `「${n}」`).join("、")} 加到左側儀表板最上方 👈\n重新整理頁面就會移除。`,
				});
			}
			saveChatLog(chat?.question || "深入分析", r.text || "");
		} catch (e) {
			addChatData({
				role: "bot",
				content: `分析時出錯了：${e?.response?.data?.message || e.message}`,
			});
		} finally {
			agentLoading.value = false;
		}
		return;
	}

	// 向量檢索找不到現成組件時，改用資料目錄即時生成一個。
	// 模型只挑表挑欄位（ComponentSpec），SQL 由程式編譯、數值由資料庫算，
	// 所以這裡拿到的每個數字都來自真實查詢，不是模型寫出來的。
	if (text === "幫我建立組件") {
		if (componentGenLoading.value) return;
		componentGenLoading.value = true;
		addChatData({ role: "bot", content: "正在從資料目錄挑選欄位並產生查詢…" });
		try {
			const res = await http.post(
				"/component/generate",
				{ question: chat?.question || "" },
				{ headers: { "Content-Type": "application/json" } },
			);
			const r = res.data?.data || {};

			if (!r.ok) {
				addChatData({
					role: "bot",
					content: `目前做不出來：${(r.errors || ["未知原因"])[0]}`,
				});
			} else if (r.chartable === false) {
				// 資料太少不值得畫圖，但不代表沒答案——直接給數字
				const lines = r.facts.slice(0, 10)
					.map((f) => `• ${f.label}：${f.value}${f.unit}`).join("\n");
				addChatData({
					role: "bot",
					content: `這個問題的資料量不適合畫成圖（${r.reasons.join("、")}），直接給您數字：\n\n${lines}`,
				});
			} else {
				const s = r.spec;
				const added = mountGenerated(r);

				// 官方已經有同一批資料做的組件嗎？有就先講清楚差在哪個視角，
				// 不要讓使用者以為我們憑空多做了一個重複的東西。
				const rel = r.related || {};
				let relText = "";
				if (rel.known === false) {
					relText = "\n\n（目前連不上官方組件清單，無法確認是否已有類似組件）";
				} else if (rel.items?.length) {
					const dup = rel.items.filter((x) => x.duplicate);
					relText = "\n\n📎 同一批資料，官方已經有這些組件：\n" +
						rel.items.map((x) => {
							const cut = x.their_series?.length
								? `：${x.their_series.join("、")}`
								: "";
							return `• ${x.name}（${x.view}${cut}）`;
						}).join("\n");

					if (dup.length) {
						relText += `\n\n⚠ 「${dup[0].name}」與這次生成的視角相同，` +
							`而且都包含「${(dup[0].overlap || []).join("、")}」，可能重複。`;
					} else {
						relText += `\n\n這次生成的是「${VIEW_LABEL[s.query_type] || s.query_type}」，` +
							`切分方式是「${s.series.map((x) => x.label).join("、")}」——` +
							`與上面各組件的切面都不同，補足了既有組件沒涵蓋的角度。`;
					}
				}

				addChatData({
					role: "bot",
					content:
						`已產生組件「${s.name}」 ✨\n\n` +
						`• 資料來源：${s.table}\n` +
						`• 圖表：${s.chart.types.join(" / ")}\n` +
						`• 結果：${r.stats.categories} 個分類 × ${r.stats.series} 個數列\n` +
						`• 產生方式：${r.model}\n\n` +
						`${s.long_desc}` +
						relText +
						"\n\n" +
						(added
							? `已加到左側儀表板最上方，可直接切換圖表類型查看 👈\n重新整理頁面就會移除。`
							: `（無法加到目前的儀表板：這個組件屬於 ${s.city}，與目前檢視的城市不同）`),
					generated: r,
				});
			}
		} catch (err) {
			console.error("component generate error:", err);
			addChatData({ role: "bot", content: "產生組件時發生錯誤，請稍後再試。" });
		}
		componentGenLoading.value = false;
		return;
	}

	if (text === "建立儀表板") {
		if (dashboardCreationLoading.value === true) return;
		dashboardCreationLoading.value = true;
		// 確認個人儀表板是否超過20個
		const response = await http.get(`/dashboard/`);
		if (response.data?.data?.personal?.length > 20) {
			addChatData({
				role: "bot",
				content:
					"您的個人儀表板已超出限制 20 個，請先移除既有儀表板後，重新執行本功能！",
			});
			dashboardCreationLoading.value = false;
			return;
		}
		const components = Array.from(new Set(relations.map((r) => r.id))).map(
			(id) => ({ id }),
		);

		if (user.value.user_id) {
			editDashboard.value = {
				index: "",
				name: "推薦儀表板",
				icon: "star",
				components: components,
			};
			await createDashboard();
			saveChatLog("建立儀表板", "使用者成功建立儀表板!");
		} else {
			addChatData({
				role: "bot",
				content: "請先登入會員以使用此功能喔！",
			});
		}
		dashboardCreationLoading.value = false;
	}
};

// 把剛產生的組件掛到目前的儀表板上。
//
// 官方渲染組件只看兩個東西：config.chart_data（數列）與
// chart_config.categories（分類）——contentStore.js:328-336 就是這樣填的。
// 我們的產生器輸出的格式跟後端編譯出來的一模一樣，所以直接組一個
// 組件物件推進去就會畫出來，不需要先寫進資料庫。
//
// 只存在記憶體，重新整理就沒了。要永久保留得走 db/ 那條路。
// query_type 說人話。跟 official.js 的 VIEW 對照表一致。
const VIEW_LABEL = {
	time: "時間趨勢",
	two_d: "分項比較",
	three_d: "分項比較（多數列）",
	percent: "占比",
};

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
	if (!text.trim()) return;
	addQueryData({
		role: "user",
		content: text,
	});
	userMessage.value = "";
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
</script>

<template>
  <div class="chat-widget">
    <!-- 標題 -->
    <div class="header">
      <h3>臺北城市儀表板小幫手</h3>
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
            <!-- 表格區 -->
            <div
              v-if="chat.relations"
              v-horizontal-wheel
              class="relation-area"
            >
              <table class="relation-table">
                <thead>
                  <tr>
                    <th>排名</th>
                    <th>城市</th>
                    <th>主題</th>
                    <th>組件</th>
                    <th>關聯性</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(item, index) in chat.relations"
                    :key="index"
                  >
                    <td>{{ index + 1 }}</td>
                    <td>
                      {{
                        item.city === "taipei"
                          ? "臺北"
                          : "雙北"
                      }}
                    </td>
                    <td>
                      <template
                        v-for="dash in (
                          compToDashIndexMap[
                            item.id
                          ] || []
                        ).filter(
                          (d) => d.city === item.city,
                        )"
                        :key="dash.index"
                      >
                        <a
                          class="theme-btn"
                          :href="`https://citydashboard.taipei/dashboard?index=${dash.index}&city=${item.city}`"
                        >
                          {{ dash.name }}
                        </a>
                      </template>
                    </td>
                    <td>{{ item.name }}</td>
                    <td>{{ item.score }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div
              v-if="chat.button"
              v-horizontal-wheel
              class="message--button scrollbar-x-hide"
            >
              <button
                v-for="btn in chat.button"
                :key="btn.id"
                @click="qaBtnHandler(btn.text, chat.relations, chat)"
              >
                {{ btn.text }}
              </button>
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

    <!-- 輸入區 -->
    <div class="input-area">
      <input
        v-model="userMessage"
        type="text"
        placeholder="輸入訊息..."
        @keyup.enter="sendBtnHandler(userMessage)"
      >
      <button @click="sendBtnHandler(userMessage)">
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

		h3 {
			font-size: 18px;
			font-weight: 700;
			color: $white;
			margin: 0;
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
