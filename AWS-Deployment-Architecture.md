# 青年儀表板 - AWS 上雲架構規劃書 (Draft)

## 1. 專案概述
本規劃書旨在為「新北市AI智慧城市黑客松」專案（青年人口儀表板）設計輕量、穩定且安全的 AWS 上雲架構，以供評審展示與後續大會交接使用。

## 2. 核心組件分析
根據本機架構 (`local-demo/ARCHITECTURE.md`)，目前專案包含三大核心模組：
* **前端與代理層 (Frontend & Proxy)**：Vue 3 (Vite) + Node.js (`inject.js` 中介層)，負責提供 UI 介面、官方 API 快取、請求攔截與代理。
* **AI 生成層 (Bedrock Integration)**：使用 Amazon Bedrock (Claude Sonnet 4.6 / Haiku 等)，透過 `@aws-sdk` (SigV4) 進行身分驗證，處理組件生成與 AI 洞察。
* **資料庫 (Database)**：PostgreSQL 16，負責儲存 `01_data_table.sql` (約 2,250 筆新北 29 區人口資料)。

## 3. 建議架構：單台 EC2 容器化部署
考量黑客松短時間交付與現場展示需求，避免複雜且建置耗時的大型叢集，強烈推薦採用 **單一 EC2 主機 + Docker Compose** 架構。

### 3.1 資源配置
* **運算資源**：AWS EC2 (`t4g.small`，ARM64 架構)，性價比高，成本極低且效能足以應付展示。
* **網路設定**：配置 Elastic IP 固定公有 IP；Security Group 僅開放 80 (HTTP), 443 (HTTPS), 22 (SSH)。
* **作業系統**：Ubuntu 24.04 LTS。

### 3.2 容器化設計 (Docker Compose)
* **`postgres-db` 容器**：底層使用官方 PostgreSQL Image，並將 `local-demo/db/` 掛載為初始化腳本，啟動時自動寫入測試資料。
* **`dashboard-web` 容器**：打包 Vite build 後的靜態檔與 Node.js API 服務，統一對外提供服務。

### 3.3 資安與身分驗證最佳實踐 (IAM)
* **無金鑰落地 (Keyless Security)**：將 EC2 綁定 IAM Instance Profile (給予 `AmazonBedrockFullAccess` 權限)。
* **安全效益**：伺服器上**完全不需存放** `AWS_ACCESS_KEY_ID` 與 `AWS_SECRET_ACCESS_KEY`，阻絕金鑰外洩風險，符合現代雲端資安與主辦方規範。

## 4. 團隊審核重點
請團隊成員協助審閱以下重點：
- [ ] **效能預估**：EC2 `t4g.small` 規格是否符合預期的 Demo 併發負載？
- [ ] **資料持久化**：確認 PostgreSQL 是否只需啟動時灌入測試資料，無須額外實作永久儲存區 (Volume)？
- [ ] **網域名稱**：是否需要額外配置專屬 Domain Name 與 HTTPS 憑證，還是直接使用 IP 進行內部 Demo？

> 請組員檢閱此架構，確認無誤後，即可進行 Dockerfile 與 CI/CD 腳本撰寫。
