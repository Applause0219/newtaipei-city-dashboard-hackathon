<!-- Developed By Taipei Urban Intelligence Center 2023-2024 -->
<!-- 
Lead Developer:  Igor Ho (Full Stack Engineer)
Data Pipelines:  Iima Yu (Data Scientist)
Design and UX: Roy Lin (Fmr. Consultant), Chu Chen (Researcher)
Systems: Ann Shih (Systems Engineer)
Testing: Jack Huang (Data Scientist), Ian Huang (Data Analysis Intern) 
-->
<!-- Department of Information Technology, Taipei City Government -->

<script setup>
import {
	onBeforeMount,
	onMounted,
	onBeforeUnmount,
	ref,
	computed,
	watch,
} from "vue";
import { useRoute } from "vue-router";
import { useAuthStore } from "./store/authStore";
import { useDialogStore } from "./store/dialogStore";
import { useContentStore } from "./store/contentStore";
import { useMapStore } from "./store/mapStore";

import NavBar from "./components/utilities/bars/NavBar.vue";
import SideBar from "./components/utilities/bars/SideBar.vue";
import AdminSideBar from "./components/utilities/bars/AdminSideBar.vue";
import SettingsBar from "./components/utilities/bars/SettingsBar.vue";
import NotificationBar from "./components/dialogs/NotificationBar.vue";
import InitialWarning from "./components/dialogs/InitialWarning.vue";
import ComponentSideBar from "./components/utilities/bars/ComponentSideBar.vue";
import LogIn from "./components/dialogs/LogIn.vue";
import ChatBox from "./components/dialogs/ChatBox.vue";
import ChatBotIcon from "./components/icons/ChatBotIcon.vue";

const authStore = useAuthStore();
const dialogStore = useDialogStore();
const contentStore = useContentStore();
const timeToUpdate = ref(600);

const mapStore = useMapStore();
const route = useRoute();
const updateBoards =
	import.meta.env.VITE_PERSONAL_BOARD_UPDATE?.split(",") || [];
const boardIndex = ref(null);
const board = ref(null);
const frequency = ref(600);
const isMappedToUpdateBoards = ref(false);
// Chatroom
const isChatBtnShow = ref(true);
const isChatBoxShow = ref(false);
// Timers
let chartTimer = null;
let crowdingTimer = null;
let timeTimer = null;
let mrtTimer = null;
// Update 狀態
let isCrowdingUpdating = false;

const updateBoardsMap = computed(() => {
	let needUpdateBoards = [];
	updateBoards.map((board) => {
		const id = board.split(":")[0];
		const updateSeconds = board.split(":")[1];
		needUpdateBoards.push({ id, frequency: updateSeconds });
	});
	return needUpdateBoards;
});

const formattedTimeToUpdate = computed(() => {
	const minutes = Math.floor(timeToUpdate.value / 60);
	const seconds = timeToUpdate.value % 60;
	return `${minutes}:${seconds < 10 ? "0" : ""}${seconds}`;
});

function reloadChartData() {
	if (!["dashboard", "mapview"].includes(authStore.currentPath)) return;
	contentStore.updateCurrentDashboardAllChartData();
	timeToUpdate.value = frequency.value;

	if (isMappedToUpdateBoards.value) {
		reloadMapData();
	}
}

async function reloadCrowdingChartData() {
	if (!["dashboard", "mapview"].includes(authStore.currentPath)) return;

	if (isCrowdingUpdating) return;

	isCrowdingUpdating = true;
	try {
		await contentStore.updateCurrentDashboardCertainChartData();
	} finally {
		isCrowdingUpdating = false;
	}
}

function updateTimeToUpdate() {
	if (!["dashboard", "mapview"].includes(authStore.currentPath)) return;
	if (timeToUpdate.value <= 0) {
		timeToUpdate.value = 0;
		reloadChartData();
		return;
	}
	timeToUpdate.value -= 5;
}

function reloadMapData() {
	if (!["mapview"].includes(authStore.currentPath)) return;
	mapStore.currentVisibleLayers.forEach((layerName) => {
		mapStore.map.removeLayer(layerName);
		if (mapStore.map.getSource(`${layerName}-source`)) {
			mapStore.map.removeSource(`${layerName}-source`);
		}
		const layerConfig = mapStore.mapConfigs[layerName];

		// 檢查 source
		if (layerConfig.source === "geojson") {
			// 如果 source 是 "geojson"，則使用 fetchLocalGeoJson
			mapStore.fetchLocalGeoJson(layerConfig);
		} else if (layerConfig.source === "raster") {
			// 如果 source 是 "raster"，則使用 addRasterSource
			mapStore.addRasterSource(layerConfig);
		}
	});
}

function reload3DMRTMapData() {
	if (!["mapview"].includes(authStore.currentPath)) return;
	mapStore.currentVisibleLayers.forEach((layerName) => {
		const layerConfig = mapStore.mapConfigs[layerName];
		const lastUpdate = mapStore.layerUpdateTime[layerName];
		const now = Date.now();

		// 只刷新特定組件附屬圖層
		if (
			!layerConfig.title.includes("擁擠程度") ||
			!lastUpdate ||
			now - new Date(lastUpdate).getTime() < 1.5 * 60 * 1000
		) {
			return;
		}

		mapStore.map.removeLayer(layerName);
		if (mapStore.map.getSource(`${layerName}-source`)) {
			mapStore.map.removeSource(`${layerName}-source`);
		}

		// 檢查 source
		if (layerConfig.source === "geojson") {
			// 如果 source 是 "geojson"，則使用 fetchLocalGeoJson
			mapStore.fetchLocalGeoJson(layerConfig);
		} else if (layerConfig.source === "raster") {
			// 如果 source 是 "raster"，則使用 addRasterSource
			mapStore.addRasterSource(layerConfig);
		}
	});
}

// Chatroom 功能顯示隱藏
//
// 側邊欄是「推擠」不是「覆蓋」：展開時 .app-content 變窄，
// 儀表板組件重新排版，所以新生成的組件出現時看得到，不會被面板蓋住。
//
// mapbox 只監聽 window 的 resize，容器自己變窄它不會重畫，
// 地圖會被拉扁。resizeMap() 內建 200ms 延遲，剛好等 CSS transition 跑完。
function chatbotBtnHandler() {
	isChatBoxShow.value = !isChatBoxShow.value;
	mapStore.resizeMap();
}

function hideBtnClickHandler() {
	isChatBtnShow.value = false;
	isChatBoxShow.value = false;
	mapStore.resizeMap();
}

(watch(
	() => route.query,
	(query) => {
		boardIndex.value = query.index;
		board.value = updateBoardsMap.value.find((board) => {
			return board.id === boardIndex.value;
		});
		frequency.value = board.value ? board.value.frequency : 600;
		isMappedToUpdateBoards.value = updateBoardsMap.value.some((board) => {
			return board.id === query.index;
		});
		timeToUpdate.value = frequency.value;
	},
),
{ immediate: true });

onBeforeMount(() => {
	authStore.initialChecks();

	let vh = window.innerHeight * 0.01;
	document.documentElement.style.setProperty("--vh", `${vh}px`);

	window.addEventListener("resize", () => {
		let vh = window.innerHeight * 0.01;
		document.documentElement.style.setProperty("--vh", `${vh}px`);
	});
	// contentStore.wsConnect();
});
onMounted(() => {
	const showInitialWarning = localStorage.getItem("initialWarning");

	if (!showInitialWarning && !window.location.pathname.includes("embed")) {
		dialogStore.showDialog("initialWarning");
	}

	chartTimer = setInterval(reloadChartData, 1000 * frequency.value);
	crowdingTimer = setInterval(reloadCrowdingChartData, 1000 * 60);
	timeTimer = setInterval(updateTimeToUpdate, 1000 * 5);
	mrtTimer = setInterval(reload3DMRTMapData, 1000 * 10);
});
onBeforeUnmount(() => {
	clearInterval(chartTimer);
	clearInterval(crowdingTimer);
	clearInterval(timeTimer);
	clearInterval(mrtTimer);
	// contentStore.wsDisconnect();
});
</script>

<template>
  <div
    class="app-container"
    :class="{ 'chat-open': isChatBoxShow }"
  >
    <NotificationBar />
    <NavBar v-if="authStore.currentPath !== 'embed'" />
    <!-- /mapview, /dashboard layouts -->
    <div
      v-if="
        authStore.currentPath === 'mapview' ||
          authStore.currentPath === 'dashboard'
      "
      class="app-content"
    >
      <SideBar />
      <div class="app-content-main">
        <SettingsBar />
        <RouterView />
      </div>
    </div>
    <!-- /admin layouts -->
    <div
      v-else-if="authStore.currentPath === 'admin'"
      class="app-content"
    >
      <AdminSideBar />
      <div class="app-content-main">
        <RouterView />
      </div>
    </div>
    <!-- /component, /component/:index layouts -->
    <div
      v-else-if="authStore.currentPath.includes('component')"
      class="app-content"
    >
      <ComponentSideBar />
      <div class="app-content-main">
        <RouterView />
      </div>
    </div>
    <div v-else>
      <router-view />
    </div>
    <InitialWarning />
    <LogIn />
    <div
      v-if="
        ['dashboard', 'mapview'].includes(authStore.currentPath) &&
          !authStore.isMobile &&
          !authStore.isNarrowDevice
      "
      class="app-update"
    >
      <p>下次更新：{{ formattedTimeToUpdate }}</p>
    </div>
    <div
      v-if="isChatBoxShow"
      class="chat-overlay"
    />
    <div class="chatbot-container">
      <ChatBox
        v-if="isChatBoxShow"
        class="chatbox"
        @close="chatbotBtnHandler"
      />
      <div
        v-if="isChatBtnShow"
        class="chatbot-btn-area"
      >
        <div class="hide-chat-btn">
          <button @click="hideBtnClickHandler" />
        </div>
        <button
          class="chatbot-btn"
          @click="chatbotBtnHandler"
        >
          <ChatBotIcon />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.app {
	&-container {
		max-width: 100vw;
		max-height: 100vh;
		max-height: calc(var(--vh) * 100);

		// 小幫手側邊欄的兩個尺寸來源，其他元素一律引用它們，不要各自寫死 400px。
		//   --chat-panel-w  面板本身的寬度（永遠是這個值）
		//   --chat-width    它「推擠」掉多少版面（收合時 0、覆蓋模式時也是 0）
		--chat-panel-w: 400px;
		--chat-width: 0px;

		&.chat-open {
			--chat-width: var(--chat-panel-w);
		}
	}

	&-content {
		// 這一行就是「推擠」的全部：內容區變窄，底下的 grid 自己重排。
		width: calc(100vw - var(--chat-width));
		max-width: 100vw;
		height: calc(100vh - 60px);
		height: calc(var(--vh) * 100 - 60px);
		display: flex;
		transition: width 0.18s ease;

		&-main {
			width: 100%;
			display: flex;
			flex-direction: column;
		}
	}

	&-update {
		position: fixed;
		bottom: 0;
		right: calc(20px + var(--chat-width));
		transition: right 0.18s ease;
		color: white;
		opacity: 0.3;
		transition: opacity 0.3s;
		user-select: none;

		p {
			color: var(--color-complement-text);
		}

		&:hover {
			opacity: 1;
		}
	}
}

// Chatroom 樣式
//
// 從「右下角浮動小視窗」改成「右側側邊欄」。
// 面板本身仍然是 position: fixed，版面的讓位交給 .app-content 的 width 去算——
// 這樣三種 layout 分支（dashboard / admin / component）都不必各自改 DOM。
.chatbot-container {
	.chatbox {
		position: fixed;
		top: 61px; // NavBar 的 height: 60px 加上 border-bottom: 1px
		right: 0;
		bottom: 0;
		// border-box：讓左側那道框線算在 400px 之內，
		// 面板左緣才會剛好貼齊 .app-content 的右緣，不會壓到內容 1px
		box-sizing: border-box;
		width: var(--chat-panel-w);
		height: auto;
		margin: 0;
		z-index: 10;

		// 貼齊右緣之後，圓角和外框只留下左邊那道還有意義
		border-radius: 0;
		border-top: none;
		border-right: none;
		border-bottom: none;
	}

	.chatbot-btn-area {
		position: fixed;
		bottom: 1.5rem;
		right: 1.5rem;
		z-index: 11;
		display: flex;
		flex-direction: column;

		.hide-chat-btn {
			margin-left: auto;
			button {
				font-size: 16px;
			}
		}

		.hide-chat-btn button::before {
			content: "–";
			font-weight: bold;
			font-size: 20px;
		}

		.chatbot-btn {
			width: 70px;
			height: 70px;
			display: flex;
			align-items: center;
			justify-content: center;
			border-radius: 50%;
			background-color: #3b82f6; // Tailwind bg-blue-500
			filter: brightness(1.5);
			transition: filter 0.2s;

			&:hover {
				filter: brightness(1);
			}
		}
	}
}

// 面板展開時就不需要浮動按鈕了：面板自己有關閉鈕，
// 按鈕留著只會壓在儀表板組件上面。收合後它才是唯一的開啟入口。
.app-container.chat-open .chatbot-btn-area {
	display: none;
}

.chat-overlay {
	display: none; // 桌機是推擠，不需要遮罩
	position: fixed;
	inset: 0;
	background: rgba(0, 0, 0, 0.8);
	z-index: 9;
}

// 窄螢幕：推擠會把儀表板擠到不能看，退回覆蓋模式。
// --chat-width 歸零，其他所有引用它的地方（內容區寬度、倒數計時器、
// 浮動按鈕位置）都會自己跟著回到原位，不需要逐一覆寫。
@media (max-width: 900px) {
	.app-container.chat-open {
		--chat-width: 0px;
	}
}

// 手機：面板改成從底部升起的全寬視窗，維持官方原本的行為
@media (max-width: 600px) {
	.chat-overlay {
		display: block;
	}

	.chatbot-container {
		.chatbox {
			top: auto;
			left: 0;
			right: 0;
			bottom: 0;
			width: 100%;
			height: 68vh;
			border: 1px solid #888787;
			border-bottom: none;
			border-radius: 15px 15px 0 0;
		}

		.chatbot-btn-area {
			right: 1.5rem;
		}
	}
}
</style>
