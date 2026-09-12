# 詐欺案件被害人年齡統計（114 年第 2 季）

## 來源驗證

- 候選 #36：[data.gov.tw 資料集 176657](https://data.gov.tw/dataset/176657)；官方 PDF 直連：[114 年第 2 季詐欺案件被害人](https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/57C3F130-0002-4342-A090-7A05BA8C7C19/resource/5A4CD0D4-E863-4703-9672-235AAF5D17FB/download)。實測 HTTP 200、約 253 KB。
- DAG 先抓資料集頁並以 regex 解析真正的 `opdadm` PDF URL，再下載附件。PDF 表 2 是 114 年第 2 季詐欺案件被害人存量，按年齡與性別列出總數；同一份 PDF 的表 3 是「較上季增減」，DAG 明確選表 2，不把表 3 的變動量誤當成被害人數。
- 表內包含總計、男性、女性三欄；來源是全國、沒有新北市欄位，故 `area_code=TW`、`area_level=country`。
- PDF 轉文字依賴 `pdftotext`。DAG 與驗證器都先用 `shutil.which` 檢查；目前本機可用，但 Airflow 映像若未帶 Poppler，需要在映像新增 `poppler-utils`，這是尚未修改的部署依賴。

## 年齡與 18–35

- 官方表列：未滿18歲、18–23歲、24–29歲、30–39歲、40–49歲、50–59歲、60–64歲、65歲以上。
- 18–35 判定為 **unavailable**：30–39 跨過 35，且來源不是五歲組、沒有單齡分子或可配對分母；不可用人口比例硬拆，也不可把同份 PDF 的「較上季增減」表誤當成年齡存量。
- PDF 未列出年齡不詳列，但總計與已列年齡列有差額（本次合計為 150 人、男性 69、女性 6）；DAG 將差額明確保存為 `年齡不詳（表內總計扣除已列年齡組）`，`age_lower/age_upper=None`，並在 `breakdown.derived_residual=true`。若差額為負或表格結構改變，直接 raise。

## 對帳與尺度防護

- 輸出分為 `fraud_victim_count`（年齡列）與 `fraud_victim_all_age_count`（總計列），每個 indicator 都輸出 total/male/female；三性別分別檢查分齡加總與總計相等。
- 每次執行有年度尺度防護：至少三個年度時，以其他年度絕對變動量中位數做 3 倍檢核；目前只有 114Q2，所以只印出 comparison not applicable，不會假裝有跨年驗證。
- 實測表 2 總計：total 54,996、male 26,348、female 28,648；完整驗證器會列 shape、欄位 non-null、對帳與各年齡組彙總。
