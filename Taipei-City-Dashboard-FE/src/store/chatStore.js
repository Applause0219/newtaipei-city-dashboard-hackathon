import { ref, watch } from 'vue'
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
				"您好，我是【新北青年儀表板】的分析助理。\n\n 直接用一句話問我，我會自己決定要查哪些資料、查幾次，\n 然後把查詢過程一步一步攤開給您看，最後附上圖表。\n\n 可以問：\n • 單一主題 —— 各區的租金中位數、各區 18 到 35 歲的人口\n • 跨領域比較 —— 青年多的區租金是不是也比較高\n • 時間變化 —— 過去五年青年人口流失最多的是哪幾區\n\n 查不到的資料我會直說，不會硬掰。下面有幾個範例問題可以直接點。\n\n 📩 聯絡信箱：tuic@gov.taipei \n 🏢 臺北大數據中心 \n\n",
		},
  	];

	// 儲存組件對應主題
	const compToDashIndexMap = ref({});

  	// 從 sessionStorage 讀取
  	const savedChatData = JSON.parse(sessionStorage.getItem('chatData')) || [];

  	// 拼接預設訊息 + sessionStorage 的聊天紀錄
  	const chatData = ref([...defaultChatData, ...savedChatData]);

	// 初始化時重建 compToDashIndexMap
	const rebuildCompToDashIndexMap = async () => {
		// 收集所有 relations 裡的組件
		const allComps = savedChatData.flatMap((msg) => msg.relations || []);

		if (allComps.length === 0) return;

		try {
			const dashboards = await http.get(`/dashboard/`);
			const dashMap = dashboards.data.data;

			allComps.forEach((comp) => {
				const matched = [];

				Object.entries(dashMap).forEach(([city,cityDashboards]) => {
					cityDashboards.forEach((dash) => {
						if (dash.components.includes(comp.id)) {
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

  	// 監聽 chatData 的變化，自動同步到 sessionStorage
  	watch(
    	chatData,
    	(newVal) => {
      	// 只存使用者與機器人的聊天訊息，不存重複的預設訊息
      	const userBotMessages = newVal.filter((item) => !item.isDefault)
      	sessionStorage.setItem('chatData', JSON.stringify(userBotMessages))
    	},
    	{ deep: true }
  	);

  	const addChatData = (newChatData) => {
    	chatData.value.push({ id: chatData.value.length + 1, isDefault: false, ...newChatData });
  	};

  	// 只負責把使用者的問題推進對話。
  	//
  	// 原本這裡會先打一次向量檢索推薦既有組件，再讓使用者從三顆按鈕裡挑一條路。
  	// 拿掉的原因不是它不準，是它**永遠會回東西**——實測問
  	// 「香蕉的價格波動與月球引力的關聯」也回 10 個組件，分數 0.843~0.848，
  	// 全部高於 0.8 門檻。一個永遠說「有」的推薦，使用者無從判斷何時該信。
  	//
  	// 現在只有一條路：問題直接交給 agent，由它決定要查什麼、查幾次。
  	// 該用哪條管線是系統的責任，不該變成使用者要做的選擇題。
  	const addQueryData = async (newChatData) => {
    	chatData.value.push({ id: chatData.value.length + 1, isDefault: false, ...newChatData });
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
