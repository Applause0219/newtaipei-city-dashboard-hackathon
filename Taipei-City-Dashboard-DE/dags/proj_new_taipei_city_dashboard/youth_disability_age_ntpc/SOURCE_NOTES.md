# 新北市身心障礙者年齡組

## Source verification

- Dataset page: <https://data.gov.tw/dataset/146537>
- Official JSON resource resolved from the page:
  <https://apiservice.mol.gov.tw/OdService/download/A17000000J-030271-iup>
- The source returns 330 rows covering 22 areas and ROC 100–114 year-end
  observations. The New Taipei subset has one row per year. Its age columns
  are under 15, 15–19, 20–24, 25–29, 30–34, 35–39, 40–44, 45–49,
  50–54, 55–59, 60–64, and 65 plus.
- The source also has male and female totals, but not an age-by-sex
  cross-tab. Therefore output gender is total; male/female totals are kept in
  breakdown and are not assigned to age groups.

## Output and 18–35 judgement

- Output is ROC 100–114 converted to AD 2011–2025, year-end dates,
  period_type=year_end, area_code=65000, and city level.
- Age parsing is strict: 未滿15歲 -> (None, 14), closed intervals are
  converted to integer bounds, and 65歲以上 -> (65, None). Unknown age
  labels raise.
- 18–35 = apportioned at query time: 20–34 is directly covered, while the
  15–19 and 35–39 source bands cross the requested boundaries. This DAG
  emits the raw bands only; it does not assume a within-band distribution.
  The measure is a count, not a rate, so any later apportionment must state
  its allocation basis.

## Checks and unresolved items

- For each New Taipei year, the sum of all age fields must equal the source
  total; the full output is checked against the same yearly totals.
- A recurring three-times scale guard compares each year with the median of
  the other positive years. KNOWN_BAD_YEARS is empty because the verified
  series has no known scope anomaly.
- This is actual employed persons under the quota-employment statistic, not
  the total population holding a disability certificate. A broader
  disability-population source with age-by-sex and district dimensions would
  be a future enhancement.
