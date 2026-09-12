# 初任人員薪資歷年（官方 PDF）來源筆記

## 來源與實測

本 DAG 不再重試已確認 `records=0` 的 MOL OAS 歷史 resource，而是從四個官方發布頁解析當下的 PDF 附件：

| 民國年 | 官方發布頁 | PDF 實測 |
|---:|---|---|
| 110 | [50679](https://www.mol.gov.tw/1607/1632/1640/50679/) | HTTP 200、`application/pdf`、515,675 bytes、`pdftotext` 有文字層 |
| 111 | [58627/post](https://www.mol.gov.tw/1607/1632/1640/58627/post) | HTTP 200、`application/pdf`、583,496 bytes、`pdftotext` 有文字層 |
| 112 | [68628](https://www.mol.gov.tw/1607/1632/1640/68628/) | HTTP 200、`application/pdf`、847,441 bytes、`pdftotext` 有文字層 |
| 113 | [80217](https://www.mol.gov.tw/1607/1632/1640/80217/) | HTTP 200、`application/pdf`、908,026 bytes、`pdftotext` 有文字層 |

所有請求都帶瀏覽器 User-Agent。DAG 不寫死 media URL；每次先解析發布頁的唯一 `.pdf` href，再檢查 `Content-Type=application/pdf` 與 `%PDF` magic bytes。這是必要的，因為探勘時曾遇到普通 GET 暫時回 HTML 驗證頁；HTML 不會送進 parser。

## 抽取方式與輸出

- 使用 Poppler `pdftotext -layout -enc UTF-8`，並在程式以 `shutil.which("pdftotext")` 檢查；缺少時直接拋錯並指出 Airflow 映像依賴。`poppler-utils` 已在既有 `docker/develop/Dockerfile`、`docker/prod/Dockerfile`，本 DAG 沒有新增外部依賴。
- 110 年 PDF 的薪資表是獨立表格；111 年附表 1 是 110／111 兩年並列；112、113 年附表 1 是多年度並列。程式只取該 PDF 對應的民國年度，不能把比較欄誤當成當年度。
- 輸出三種既有 indicator：`youth_salary_initial_share_by_education`、`youth_salary_initial_mean_by_education`、`youth_salary_initial_median_by_education`。`breakdown` 保留既有的 `education_level`、`characteristic` 結構，另存原始 PDF 表名與千元值。
- PDF 的薪資單位是千元；mean／median 一律乘 1,000，輸出 `unit="元"`。share 是百分比，輸出 `unit="%"`、`value_type="rate"`。mean／median 的 `value_type` 分別是 `mean`／`median`，不是 `count`。
- 每個選定的 PDF 數值格都對應一列事實；輸入格數、輸出列數與換算後 Decimal 總和均在 DAG 內對帳，不符直接 `raise`。

## 口徑差異（不可硬湊）

PDF 的教育程度分類是：全體、國中、高級中等（高中、高職）、專科、大學、研究所；沒有 114 年 JSON 的「專科以上」分類。DAG 保留 PDF 原始標籤，不自行新增或合併「專科以上」。

110 年薪資分布表的中位數只發布：全體、高級中等（高中、高職）、大專（專科及大學）、研究所。因此 110 年沒有國中、專科、大學的獨立中位數列，DAG 不補值；111–113 年附表 1 則有五個教育程度的中位數。110 年的「大專（專科及大學）」也不拆成專科／大學。

PDF 的 `人數結構比（%）` 才對應既有 JSON 的 share；110 年主表另有「高於基本工資」、111–113 年另有「為基本工資」比例，兩者語意不同，未冒充 share indicator。

## 18–35 判定：`unavailable`

「初任」是統計資格定義，不是可用的個人年齡欄位。PDF 雖在定義或附表提到年齡上限／年齡分布，這支歷年教育薪資表沒有能與薪資值同一列對應的 18–35 個人年齡欄位；因此 `age_lower`、`age_upper`、`age_band_raw` 全部為 `None`，不由「初任」、教育程度或資格年齡推估青年數，也不做任何人口比例分攤。

## 年度尺度防護

`KNOWN_BAD_YEARS = {}`，四份 PDF 實測沒有已知壞年度。仍以各年「全體薪資平均數」做例行 3 倍尺度檢查；未知異常直接失敗，不會把最新年度的範圍改變畫成趨勢。

## 已知相容性限制

任務要求歷史薪資從千元換算成元，所以本 DAG 的 mean／median 是元。但既有 `youth_salary_initial_by_education` 程式目前仍輸出 `unit="千元"`、未乘 1,000；依「不要修改既有 DAG」規則，本次沒有動它。兩張表的 indicator／breakdown 可對應，但要直接 join 數值時，下游必須先把既有 114 年千元轉成元；這是現況限制，不假裝已解決。

## 觀察

四年全體月薪平均（PDF 原始千元／本 DAG 輸出元）：110 年 32／32,000、111 年 34／34,000、112 年 35／35,000、113 年 37／37,000；全體中位數為 28／28,000、30／30,000、31／31,000、33／33,000。教育程度與起薪的跨年趨勢因此可做，但不能把它解讀成 18–35 專屬薪資趨勢。

## 未解決

- PDF 歷史資料沒有 114 年同格式的完整跨年欄位；114 年仍由既有 OAS DAG 提供，且目前單位相容性如上。
- 本 DAG 只抽教育程度摘要，未把 PDF 的行業、就讀學科等其他表格併入，避免把不同年度不一致的分類硬接在同一個教育指標上。
- verify 是本機以真實來源執行的 Python 轉換驗證，尚未在 Airflow 排程與 PostgreSQL 實際寫入。
