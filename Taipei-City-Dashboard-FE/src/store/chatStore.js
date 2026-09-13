import { reactive, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import http from "../router/axios";

export const useChatStore = defineStore('chat', () => {
  	// 預設訊息
  	const defaultChatData = [
    	{
      		id: 1,
      		role: 'bot',
	  		isDefault: true,
      		content:
				"您好，我是【青年城市儀表板】小幫手，很高興為您服務！\n 您可以： \n\n • 點擊左側既有的儀表板主題，快速查看各主題內容 \n • 輸入您感興趣的主題描述，我會推薦相關組件，並協助組建最適合的儀表板 \n • 點擊推薦組件清單中的主題按鈕，即可前往查看該主題儀表板  \n\n 如果有想了解的內容，歡迎直接告訴我，我會盡力協助！\n\n 📩 聯絡信箱：tuic@gov.taipei \n 🏢 臺北大數據中心 \n\n",
		},
  	];

	const recommendComponents = ref(null)

	// 儲存組件對應主題
	const compToDashIndexMap = ref({});

  	// 從 sessionStorage 讀取
  	const savedChatData = JSON.parse(sessionStorage.getItem('chatData')) || [];

  	// 拼接預設訊息 + sessionStorage 的聊天紀錄
  	const chatData = ref([...defaultChatData, ...savedChatData]);

	// 初始化時重建 compToDashIndexMap
	const rebuildCompToDashIndexMap = async (messages = savedChatData) => {
		// 收集所有 relations 裡的組件
		const allComps = messages.flatMap((msg) => msg.relations || []);

		if (allComps.length === 0) return;

		try {
			const dashboards = await http.get(`/dashboard/`);
			const dashMap = dashboards.data.data;

			allComps.forEach((comp) => {
				const matched = [];

				Object.entries(dashMap).forEach(([city,cityDashboards]) => {
					cityDashboards.forEach((dash) => {
						if (dash.components && dash.components.includes(comp.id)) {
							if (!matched.some((m) => m.index === dash.index && m.city === city)) {
								matched.push({
									name: dash.name,
									index: dash.index,
									city: city
								});
							}
						}
					});
				});

				compToDashIndexMap.value[comp.id] = matched;
			});
		} catch (error) {
			console.error("rebuildCompToDashIndexMap error:", error);
		}
	};

	// 有舊資料才重建
	if (savedChatData.length > 0) {
  		rebuildCompToDashIndexMap();
	}

	// 伺服器上的共用對話紀錄（local-demo/inject.js 存成 JSON 檔），
	// 讓其他電腦打開也看得到同一份對話
	const chatHistoryUrl = `${import.meta.env.VITE_API_URL === '/api/dev' ? '/api/dev' : '/api'}/chat-history`;

	const loadServerChatHistory = async () => {
		try {
			const r = await fetch(chatHistoryUrl, { cache: 'no-store' });
			if (!r.ok) return;
			const messages = (await r.json()).data || [];
			if (messages.length === 0) return;
			chatData.value = [...defaultChatData, ...messages];
			rebuildCompToDashIndexMap(messages);
		} catch (error) {
			console.error("loadServerChatHistory error:", error);
		}
	};

	let saveTimer = null;
	const saveServerChatHistory = (messages) => {
		// Agent 串流時每個事件都會觸發，合併成一次寫入
		clearTimeout(saveTimer);
		saveTimer = setTimeout(() => {
			fetch(chatHistoryUrl, {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ data: messages }),
			}).catch((error) => console.error("saveServerChatHistory error:", error));
		}, 1500);
	};

	loadServerChatHistory();

  	// 監聽 chatData 的變化，自動同步到 sessionStorage 與伺服器
  	watch(
    	chatData,
    	(newVal) => {
      	// 只存使用者與機器人的聊天訊息，不存重複的預設訊息
      	const userBotMessages = newVal.filter((item) => !item.isDefault)
      	sessionStorage.setItem('chatData', JSON.stringify(userBotMessages))
      	saveServerChatHistory(userBotMessages)
    	},
    	{ deep: true }
  	);

  	const addChatData = (newChatData) => {
    	chatData.value.push({ id: chatData.value.length + 1, isDefault: false, ...newChatData });
  	};

	const addQueryData = async (newChatData, maxInsights = 3) => {

    	chatData.value.push({ id: chatData.value.length + 1, isDefault: false, ...newChatData });

		recommendComponents.value = [];
		let topK = null;

		const statusMsg = { id: chatData.value.length + 1, role: 'bot', isDefault: false, content: '🔍 正在搜尋青年資料庫...' };
		chatData.value.push(statusMsg);

		try {
			const response = await http.post(
  				"/vector/component",
  				new URLSearchParams({
    				query: newChatData.content,
    				limit: 10,
    				score: 0.8,
  				}),
  				{
    				headers: {
      					"Content-Type": "application/x-www-form-urlencoded",
    				},
  				}
			);

			const dashboards = await http.get(`/dashboard/`);

			const compList = response.data.data;
			const dashMap = dashboards.data.data;

			compList.forEach((comp) => {
				const matched = [];

				Object.entries(dashMap).forEach(([city,cityDashboards]) => {
					cityDashboards.forEach((dash) => {
						if (dash.components && dash.components.includes(comp.id)) {
							// 避免同名主題重複加入
							if (!matched.some((m) => m.index === dash.index && m.city === city)) {
								matched.push({
									name: dash.name,
									index: dash.index,
									city: city
								});
							}
						}
					});
				});

				compToDashIndexMap.value[comp.id] = matched;
			});

			if (response.data?.data?.length > 0) {
				recommendComponents.value = response.data.data;
			}

			// 去除重複項目存到 result
			const result = Array.from(
  				recommendComponents.value.reduce((map, item) => {
    				const key = item.index
    				const exist = map.get(key)

    				// 如果還沒放過，直接放
    				if (!exist) {
      					map.set(key, item)
      					return map
    				}

    				// 如果已存在，但現在的是 metrotaipei，就覆蓋
    				if (item.city === 'metrotaipei') {
      					map.set(key, item)
    				}

    				return map
  				}, new Map()).values()
			)
			// 把 result 蓋回去 recommendComponents
			recommendComponents.value = result

		} catch (error) {
			console.error("VectorAnalysisError :", error);
		}

		const statusIdx = chatData.value.indexOf(statusMsg);
		if (statusIdx !== -1) chatData.value.splice(statusIdx, 1);

		if (recommendComponents.value && recommendComponents.value?.length > 0) {
			topK = [...recommendComponents.value].sort((a, b) => b.score - a.score);
			chatData.value.push({ id: chatData.value.length + 1, role: 'bot', isDefault: false, content: `找到 ${topK.length} 份相關資料集，Agent 正在建立洞察組件。\n`, relations: topK });
			startAgentAnalysis(newChatData.content, maxInsights);
		} else {
			chatData.value.push({ id: chatData.value.length + 1, role: 'bot', isDefault: false, content: `很抱歉，您提供的描述沒有相似組件，請繼續提問 ! ` });
		}

		// 分析結束後紀錄問答log
		saveChatLog(newChatData.content, recommendComponents.value);
  	};

	const _toolLabel = (name) => {
		const labels = { execute_sql: '執行 SQL 查詢', publish_component: '發佈組件' };
		return labels[name] || name;
	};

	const startAgentAnalysis = (question, maxInsights = 3) => {
		const depthLabel = { 1: '低', 3: '中', 5: '高' }[maxInsights] || '中';
		const agentMsg = reactive({
			id: chatData.value.length + 1, role: 'bot', isDefault: false,
			content: `🤖 Agent 正在深度分析（${depthLabel}）...`,
			thinkingSteps: [],
			thinkingDone: false,
		});
		chatData.value.push(agentMsg);

		const baseUrl = import.meta.env.VITE_API_URL === '/api/dev'
			? '/api/dev' : '/api';
		const url = `${baseUrl}/agent/stream?question=${encodeURIComponent(question)}&max_insights=${maxInsights}`;
		const es = new EventSource(url);

		es.onmessage = async (e) => {
			if (e.data === '[DONE]') {
				es.close();
				agentMsg.thinkingDone = true;
				return;
			}
			try {
				const ev = JSON.parse(e.data);
				if (ev.type === 'reasoning' && ev.content) {
					const lastStep = agentMsg.thinkingSteps[agentMsg.thinkingSteps.length - 1];
					if (ev.append && lastStep?.type === 'reasoning' && lastStep.partIndex === ev.part_index) {
						lastStep.label += ev.content;
					} else {
						agentMsg.thinkingSteps.push({
							type: 'reasoning',
							partIndex: ev.part_index,
							label: ev.content,
						});
					}
					agentMsg.content = '🤖 Agent 正在思考...';
				} else if (ev.type === 'tool_call') {
					const sql = ev.tool === 'execute_sql' && typeof ev.args?.query === 'string'
						? ev.args.query : '';
					let suffix = '';
					if (sql) {
						const m = sql.match(/FROM\s+([\w."]+)/i);
						suffix = m ? m[1].replace(/"/g, '') : '';
					} else if (ev.tool === 'publish_component' && ev.args?.name) {
						suffix = ev.args.name;
					}
					agentMsg.thinkingSteps.push({
						type: 'call',
						label: `${_toolLabel(ev.tool)}${suffix ? ' → ' + suffix : ''}`,
						sql,
					});
					agentMsg.content = `🤖 Agent 呼叫工具：${_toolLabel(ev.tool)}`;
				} else if (ev.type === 'tool_result') {
					const s = typeof ev.content === 'string' ? ev.content : '';
					const isFailure = /violations|SQL blocked|Query execution failed|success=False/i.test(s)
						|| s.startsWith('Insight emitted:');
					if (isFailure) {
						agentMsg.thinkingSteps.push({
							type: 'retry',
							label: '自動重試',
							hidden: true,
						});
						for (let i = agentMsg.thinkingSteps.length - 2; i >= 0; i--) {
							if (agentMsg.thinkingSteps[i].type === 'call') {
								agentMsg.thinkingSteps[i].hidden = true;
								break;
							}
						}
					} else {
						const rowMatch = s.match(/row_count=(\d+)/);
						const hint = rowMatch ? `${rowMatch[1]} 筆` : '完成';
						agentMsg.thinkingSteps.push({
							type: 'result',
							label: `取得結果（${hint}）`,
						});
						agentMsg.content = `🤖 Agent 取得結果，繼續分析...`;
					}
				} else if (ev.type === 'insight') {
					const ins = ev.data || {};
					if (!agentMsg.insightDetails) agentMsg.insightDetails = [];
					agentMsg.insightDetails.push(ins);
					agentMsg.thinkingSteps.push({
						type: 'status',
						label: `💡 洞察：${ins.title || ''}`,
					});
					agentMsg.content = `🤖 Agent 已發現 ${agentMsg.insightDetails.length} 項洞察，繼續分析...`;
				} else if (ev.type === 'status') {
					agentMsg.thinkingSteps.push({ type: 'status', label: ev.message });
					agentMsg.content = `🤖 ${ev.message}`;
				} else if (ev.type === 'result') {
					const data = ev.data || {};
					const insights = data.insights || [];
					const published = data.published || [];
					const streamedCount = (agentMsg.insightDetails || []).length;
					if (streamedCount > 0) {
						agentMsg.content = `📊 Agent 分析完成！共 ${streamedCount} 項洞察：`;
					} else if (insights.length > 0) {
						agentMsg.content = `📊 Agent 分析完成！發現 ${insights.length} 項洞察：`;
						agentMsg.insightDetails = insights;
					} else {
						agentMsg.content = `📊 Agent 分析完成。`;
					}
					if (data.report_markdown) {
						agentMsg.reportMarkdown = data.report_markdown;
					}
					if (published.length > 0) {
						const responses = await Promise.all(published.map((index) =>
							http.get('/component/', { params: { searchbyindex: index } })
						));
						agentMsg.relations = responses
							.flatMap((response) => response.data?.data || [])
							.filter((component) => published.includes(component.index))
							.map((component) => ({ ...component, score: 1 }));
						if (agentMsg.relations.length > 0) {
							agentMsg.button = [{ id: 1, text: '建立儀表板' }];
						}
					}
				} else if (ev.type === 'error') {
					agentMsg.content = `⚠️ Agent 分析遇到問題：${ev.detail}`;
				}
			} catch { /* ignore parse errors */ }
		};

		es.onerror = () => {
			agentMsg.content += '\n(串流已中斷)';
			agentMsg.thinkingDone = true;
			es.close();
		};
	};

	const saveChatLog = async(question, answer) => {
		try {
        	const formData = new FormData();
        	const d = new Date();
        	const todayId =
          		d.getFullYear() +
          		String(d.getMonth() + 1).padStart(2, "0") +
          		String(d.getDate()).padStart(2, "0");

        	formData.append("session", "session_" + todayId);
        	formData.append("question", question);
        	formData.append("answer", JSON.stringify(answer));

        	await http.post("/chatlog/", formData, {
          		headers: {
            		"Content-Type": "multipart/form-data",
          		},
        	});
      	} catch (error) {
        	console.error("saveChatLog error:", error);
      	}
	};

	return { chatData, compToDashIndexMap, addChatData, addQueryData, saveChatLog }
})
