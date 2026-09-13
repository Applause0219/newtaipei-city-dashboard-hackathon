import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import viteCompression from "vite-plugin-compression";
// local-demo 的假後端：攔截 /api 的少數幾條路由（組件生成、洞察、
// AI 摘要），其餘轉發官方 API。沒有這個外掛，/api/component/generate
// 會直接被 proxy 到後端然後回 404。
import { injectLocalComponents } from "./local-demo/inject.js";

// 嘗試讀取環境變數，若不存在則回傳 false
let isDockerCompose = process?.env.DOCKER_COMPOSE === "true"; // eslint-disable-line no-undef

const serverConfig = isDockerCompose
	? {
		// Docker Compose override config
		host: "0.0.0.0",
		port: 80, // 如有需要可變更 port
		proxy: {
			"/api/dev": {
				target: "http://dashboard-be:8080",
				changeOrigin: true,
				rewrite: (path) => path.replace("/dev", "/v1")
			}
		}
	}
	: {
		host: "0.0.0.0",
		port: 80,
		proxy: {
			"/api": {
				target: "https://citydashboard.taipei/api/v1",
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/api/, "")
			},
			"/geo_server": {
				target: "https://citydashboard.taipei/geo_server/",
				changeOrigin: true,
				rewrite: (path) => path.replace(/^\/geo_server/, "")
			}
		}
	};

export default defineConfig({
	plugins: [injectLocalComponents(), vue(), viteCompression()],
	build: {
		rollupOptions: {
			output: {
				manualChunks(id) {
					if (id.includes("node_modules")) {
						return id
							.toString()
							.split("node_modules/")[1]
							.split("/")[0]
							.toString();
					}
				},
			},
		},
		chunkSizeWarningLimit: 1600,
	},
	base: "/",
	server: serverConfig,
});