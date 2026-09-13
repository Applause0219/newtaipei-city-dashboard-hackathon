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
const { createDashboard, editCurrentDashboard } = contentStore;
const { chatData, compToDashIndexMap } = storeToRefs(chatStore);
const { editDashboard } = storeToRefs(contentStore);
const { user } = storeToRefs(authStore);

const userMessage = ref("");
const chatAreaRef = ref(null);
const dashboardCreationLoading = ref(false);
const depthLevel = ref(1);
const depthOptions = [
	{ label: "低", value: 1 },
	{ label: "中", value: 3 },
	{ label: "高", value: 5 },
];

const clearChat = () => {
	sessionStorage.removeItem("chatData");
	chatData.value.splice(1);
};

const qaBtnHandler = async (text, relations, chat) => {
	if (text === "建立儀表板") {
		if (dashboardCreationLoading.value || chat?.dashboardCreated) return;
		dashboardCreationLoading.value = true;
		try {
			const response = await http.get(`/dashboard/`);
			const personalDashboards = response.data?.data?.personal ?? [];
			if (personalDashboards.length > 20) {
				addChatData({
					role: "bot",
					content:
						"您的個人儀表板已超出限制 20 個，請先移除既有儀表板後，重新執行本功能！",
				});
				return;
			}
			const components = Array.from(new Set(relations.map((r) => r.id))).map(
				(id) => ({ id }),
			);

			if (user.value.user_id) {
				const existingDash = personalDashboards.find((d) => d.name.startsWith("推薦儀表板"));
				if (existingDash) {
					const existingIds = new Set(
						(existingDash.components ?? []).map(Number),
					);
					const merged = [
						...existingDash.components.map((id) => ({ id })),
						...components.filter((c) => !existingIds.has(Number(c.id))),
					];
					editDashboard.value = {
						index: existingDash.index,
						name: existingDash.name,
						icon: existingDash.icon || "star",
						components: merged,
					};
					await editCurrentDashboard();
				} else {
					editDashboard.value = {
						index: "",
						name: "推薦儀表板",
						icon: "star",
						components: components,
					};
					await createDashboard();
				}
				if (chat) chat.dashboardCreated = true;
				saveChatLog("建立儀表板", "使用者成功建立儀表板!");
			} else {
				addChatData({
					role: "bot",
					content: "請先登入會員以使用此功能喔！",
				});
			}
		} catch (error) {
			console.error("createDashboard error:", error);
			addChatData({
				role: "bot",
				content: "建立儀表板失敗，請稍後再試！",
			});
		} finally {
			dashboardCreationLoading.value = false;
		}
	}
};

const sendBtnHandler = (text) => {
	if (!text.trim()) return;
	addQueryData({
		role: "user",
		content: text,
	}, depthLevel.value);
	userMessage.value = "";
};

watch(
	chatData,
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
      <h3>青年城市儀表板小幫手</h3>
      <div class="header-actions">
        <button
          class="clear-btn"
          title="清除對話紀錄"
          @click="clearChat"
        >
          &#x21bb;
        </button>
        <button
          class="close-btn"
          aria-label="關閉小幫手"
          @click="emit('close')"
        >
          ×
        </button>
      </div>
    </div>

    <!-- 聊天區 -->
    <div
      ref="chatAreaRef"
      class="chat-area scrollbar-custom"
    >
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
            <!-- 思考過程 -->
            <details
              v-if="chat.thinkingSteps && chat.thinkingSteps.length"
              class="thinking-section"
              :open="!chat.thinkingDone"
            >
              <summary>
                {{ chat.thinkingDone ? `思考過程（${chat.thinkingSteps.length} 步）` : `思考中...（${chat.thinkingSteps.length} 步）` }}
              </summary>
              <div class="thinking-timeline">
                <div
                  v-for="(step, idx) in chat.thinkingSteps"
                  :key="idx"
                  :class="['thinking-step', `step-${step.type}`, { 'has-detail': step.sql }]"
                >
                  <span class="step-icon">
                    {{ step.type === 'reasoning' ? '💭' : step.type === 'call' ? '🔧' : step.type === 'result' ? '📄' : '💬' }}
                  </span>
                  <details
                    v-if="step.sql"
                    class="step-sql"
                  >
                    <summary class="step-label">
                      {{ step.label }}
                    </summary>
                    <pre><code>{{ step.sql }}</code></pre>
                  </details>
                  <span
                    v-else
                    class="step-label"
                  >{{ step.label }}</span>
                </div>
              </div>
            </details>
            <!-- 洞察卡片 -->
            <div
              v-if="chat.insightDetails && chat.insightDetails.length"
              class="insight-list"
            >
              <div
                v-for="(ins, idx) in chat.insightDetails"
                :key="idx"
                class="insight-card"
              >
                <div class="insight-title">
                  {{ idx + 1 }}. {{ ins.title || '(洞察)' }}
                </div>
                <div
                  v-if="ins.claim"
                  class="insight-register"
                >
                  <span class="register-label">Data Fact</span>
                  <p class="insight-claim">
                    {{ ins.claim }}
                  </p>
                </div>
                <div
                  v-if="ins.narrative"
                  class="insight-register"
                >
                  <span class="register-label">Analytical Insight</span>
                  <p class="insight-narrative">
                    {{ ins.narrative }}
                  </p>
                </div>
                <div
                  v-if="ins.hypothesis"
                  class="insight-register is-hypothesis"
                >
                  <span class="register-label">Hypothesis・未經驗證</span>
                  <p class="insight-hypothesis">
                    {{ ins.hypothesis }}
                  </p>
                </div>
                <details
                  v-if="ins.source_sql"
                  class="insight-sql"
                >
                  <summary>📋 SQL</summary>
                  <pre><code>{{ ins.source_sql }}</code></pre>
                </details>
              </div>
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
                :disabled="dashboardCreationLoading || chat.dashboardCreated"
                @click="qaBtnHandler(btn.text, chat.relations, chat)"
              >
                {{ chat.dashboardCreated ? "已建立儀表板" : btn.text }}
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

    <!-- 思考深度 -->
    <div class="depth-bar">
      <span class="depth-label">思考深度</span>
      <div class="depth-segments">
        <button
          v-for="opt in depthOptions"
          :key="opt.value"
          :class="{ active: depthLevel === opt.value }"
          @click="depthLevel = opt.value"
        >
          {{ opt.label }}
        </button>
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

		.header-actions {
			display: flex;
			align-items: center;
			gap: 0.25rem;
		}

		.clear-btn {
			background: none;
			border: none;
			color: $white;
			cursor: pointer;
			opacity: 0.6;
			padding: 4px;
			font-size: 20px;
			line-height: 1;
			&:hover { opacity: 1; }
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
			background: none;
			border: none;
			cursor: pointer;
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

					.thinking-section {
						border: 1px solid #555;
						border-radius: 8px;
						background: #1a1c1e;
						overflow: hidden;

						summary {
							cursor: pointer;
							padding: 10px 14px;
							color: #ccc;
							font-size: 14px;
							font-weight: 600;
							user-select: none;
							&:hover { color: #fff; }
						}

						.thinking-timeline {
							padding: 0 14px 8px;
							max-height: 220px;
							overflow-y: auto;

							&::-webkit-scrollbar {
								width: 4px;
							}
							&::-webkit-scrollbar-thumb {
								background: #666;
								border-radius: 4px;
							}

							.thinking-step {
								display: flex;
								align-items: center;
								gap: 8px;
								height: 32px;
								border-bottom: 1px solid #2a2c2e;
								&:last-child { border-bottom: none; }

								.step-icon {
									flex-shrink: 0;
									font-size: 13px;
									width: 18px;
									text-align: center;
								}

								.step-label {
									color: #ccc;
									font-size: 13px;
									white-space: nowrap;
									overflow: hidden;
									text-overflow: ellipsis;
								}

								&.step-reasoning {
									height: auto;
									align-items: flex-start;
									padding: 8px 0;

									.step-label {
										flex: 1;
										min-width: 0;
										line-height: 1.5;
										white-space: pre-wrap;
										overflow: visible;
										text-overflow: clip;
										overflow-wrap: anywhere;
									}
								}

								&.has-detail {
									height: auto;
									align-items: flex-start;
									padding: 8px 0;
								}

								.step-sql {
									flex: 1;
									min-width: 0;

									summary { cursor: pointer; }

									pre {
										margin: 6px 0 0;
										padding: 8px;
										max-height: 220px;
										overflow: auto;
										background: #111;
										border-radius: 4px;
									}

									code {
										color: #8be9fd;
										font: 11px/1.45 monospace;
										white-space: pre-wrap;
										word-break: break-word;
									}
								}

								&.step-call .step-label { color: #6fb3ff; }
								&.step-result .step-label { color: #7dd98b; }
							}
						}
					}

					.insight-list {
						display: flex;
						flex-direction: column;
						gap: 8px;

						.insight-card {
							border: 1px solid #5a9cf8;
							border-radius: 8px;
							background: $card-bg;
							padding: 10px 14px;

							.insight-title {
								font-weight: 700;
								font-size: 14px;
								color: #5a9cf8;
								margin-bottom: 4px;
							}

							.insight-claim {
								color: $white;
								font-size: 14px;
								margin: 4px 0;
								line-height: 1.5;
							}

							.insight-narrative {
								color: #bbb;
								font-size: 13px;
								margin: 4px 0;
								line-height: 1.4;
							}

							.insight-register {
								margin-top: 6px;

								.register-label {
									display: block;
									font-size: 11px;
									letter-spacing: 0.04em;
									color: #888;
								}

								&.is-hypothesis {
									border-left: 2px dashed #d9a441;
									padding-left: 8px;

									.register-label { color: #d9a441; }
								}
							}

							.insight-hypothesis {
								color: #d8c9a8;
								font-size: 13px;
								font-style: italic;
								margin: 4px 0;
								line-height: 1.4;
							}

							.insight-sql {
								margin-top: 6px;

								summary {
									cursor: pointer;
									color: #888;
									font-size: 12px;
									user-select: none;
									&:hover { color: #aaa; }
								}

								pre {
									background: #1a1a1a;
									border-radius: 4px;
									padding: 8px;
									margin: 4px 0 0;
									overflow: auto;
									max-height: 300px;

									code {
										color: #8be9fd;
										font-size: 11px;
										font-family: monospace;
										white-space: pre-wrap;
										word-break: break-word;
									}
								}
							}
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

	.depth-bar {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.4rem 1.125rem 0;
		background: $panel-bg;

		.depth-label {
			color: #999;
			font-size: 12px;
			flex-shrink: 0;
		}

		.depth-segments {
			display: flex;
			border: 1px solid #666;
			border-radius: 6px;
			overflow: hidden;

			button {
				background: transparent;
				color: #999;
				border: none;
				padding: 2px 12px;
				font-size: 12px;
				cursor: pointer;
				transition: background 0.15s, color 0.15s;

				&:not(:last-child) {
					border-right: 1px solid #666;
				}

				&.active {
					background: #5a9cf8;
					color: $white;
				}

				&:hover:not(.active) {
					background: rgba(255, 255, 255, 0.08);
				}
			}
		}
	}

	.input-area {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.5rem;
		padding: 0.75rem 1.125rem 1.5rem;
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
