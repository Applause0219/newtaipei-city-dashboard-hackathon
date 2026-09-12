# youth_skill_certification_age_tw

## 來源與實測

- 官方入口：勞動部統計處「各級合格發證數－按級別、性別及年齡分」。
- 實際下載檔：https://statdb.mol.gov.tw/html/sex/yearnew/8050.pdf
- 2026-09-08 實測 HTTP 200、application/pdf、77,336 bytes；一頁表格，單位為「張」。
- 年度總表為民國 110–114；年齡表是最新年度 114，包含 15-19、20-24、25-29、30-34、35-39、40-44、45-49、50-54、55-59、60-64、65 歲以上。

## 契約與 18-35 判定

- gender 是技術士證持有人的性別；PDF 的年齡與性別在同一張交叉表，主體一致。
- 年度總表沒有年齡，因此該部分 age_lower/age_upper/age_band_raw 為 null，狀態為 unavailable；不可把年度總數當作青年數。
- 最新年齡組以整數區間保存；65 歲以上為 (65, null)。
- 18-35 為 apportioned：15-19 與 35-39 跨越命題邊界。DAG 只保留原始區間，不把分攤比例固化進 ETL；查詢層必須另行決定方法。

## 解析、對帳與防護

- pdftotext -tsv 由 shutil.which 檢查；Airflow 映像需有 poppler-utils。這是本 DAG 唯一的外部命令依賴，專案 Dockerfile 已有該依賴。
- 每一列強制解析 11 個數值；破折號視為表格明確的 0，空白或其他無法解析的文字直接 raise。
- 每年檢查總計男+女；甲、乙、丙、單一級男+女也逐列檢查。最新年度再檢查年齡列合計等於年度列的每一個欄位。
- 各級的 gender=total 是來源男＋女的明確推導值，breakdown 會標示 derived_from；不是額外的來源欄位。
- 加入年度總數 3 倍尺度防護；目前 KNOWN_BAD_YEARS 為空，未觀察到需排除的年度。

## 限制

- 來源是全臺灣總表，沒有新北市行政區維度，因此 area_code=TW、area_level=country。
- 「報檢」與「合格發證」不是同一指標；本 DAG 僅輸出合格發證數，沒有把它當報檢數或合格率。
