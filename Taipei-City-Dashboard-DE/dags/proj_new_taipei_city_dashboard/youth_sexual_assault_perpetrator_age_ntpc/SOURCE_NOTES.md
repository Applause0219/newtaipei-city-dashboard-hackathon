# 新北市性侵害通報案件加害者（按年齡別、性別）

## 來源驗證

- 資料頁：https://data.ntpc.gov.tw/datasets/e871f626-2e65-420d-a49b-a83d3bccbe5e
- API：https://data.ntpc.gov.tw/api/datasets/e871f626-2e65-420d-a49b-a83d3bccbe5e/json?page={0-based}&size=5000
- 驗證日期：2026-09-06；資料頁 HTTP 200、JSON API HTTP 200；請求帶瀏覽器 User-Agent。
- 實際抓到 19 列，涵蓋 2006–2024 共 19 年，單頁已包含全部資料。
- 提供機關：新北市政府主計處；資料集頁標示更新頻率為每年。

## 主要欄位說明（資料集頁原文對應）

資料頁原文從 `field1(年)`、`percent2/3(性侵害防治業務受理通報案件人數_男/女)`，
接著是 `percent4/5(性侵害通報案件加害者人數_總計_男/女)`；年齡欄依序為：

- `percent6/7`：0–5 歲男／女
- `percent8/9`：6–11 歲男／女
- `percent10/11`：12–17 歲男／女
- `percent12/13`：18–23 歲男／女
- `percent14/15`：24–29 歲男／女
- `percent16/17`：30–39 歲男／女
- `percent18/19`：40–49 歲男／女
- `percent20/21`：50–64 歲男／女
- `percent22/23`：65 歲以上男／女
- `percent24/25`：年齡不詳男／女
- `percent26`：性別不詳。

程式以角色別名表接受 `percentN` 與 `percent N`；找不到欄位或別名衝突會拋錯。
`percent2/3` 是另一個非年齡化的受理通報案件人數，且早期值為 `-`，不混入加害者年齡指標。

## 年齡與 18–35 判定

18–23、24–29 完整落在命題的 18–35 歲區間；30–39 跨過 35。因此整支資料判定為 **apportioned**，不是 exact。DAG 保留來源的原始年齡組，不自行把 30–39 拆成 30–35；後續若採均勻假設，必須在查詢結果標示假設。

`年齡不詳` 是來源明示的 sentinel：`age_lower/age_upper=None`、`age_band_raw="年齡不詳"`。全齡男／女合計及性別不詳合計沒有年齡欄位，三個 age 欄位為 `None`，不由總數推估年齡。其他無法解析標籤直接 raise。

## 合計列、對帳與尺度防護

- `sexual_assault_perpetrator_total_count` 是來源的全齡男／女合計；`sexual_assault_perpetrator_age_count` 是年齡明細；`sexual_assault_perpetrator_unknown_gender_count` 是全齡性別不詳，不與男／女合計相加。
- 每次執行都驗證 19 年 ×（2 個全齡性別欄 + 1 個性別不詳欄 + 10 組年齡 × 2 性別）來源 cell 全部且只輸出一次；各年度明細男、女加總必須分別等於來源全齡男、女合計。任何不符直接 raise。
- 每年度全齡男／女合計與其餘年度中位數比較，超過 3 倍直接 raise，防止來源範圍突然改變而畫出假尖峰。
- `value_type=count`、單位「人」。這不是 rate，不做依人口比例縮放。

## 輸出語意與觀察

- `gender` 與 `age` 都指加害者本人；`gender=unknown` 只表示來源的性別不詳欄，不代表任何年齡。
- `area_code=65000`、`area_level=city` 為新北市全市。
- 2006–2024 全齡男／女加害者合計約 475–2,094 人，年度有波動；青年數字不能直接把全齡合計當成 18–35。
