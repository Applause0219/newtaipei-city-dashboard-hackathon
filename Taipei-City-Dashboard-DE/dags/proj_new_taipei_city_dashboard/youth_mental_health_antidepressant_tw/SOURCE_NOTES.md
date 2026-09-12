# youth_mental_health_antidepressant_tw

## Source verification

- Official NHI CSV API: `https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-L50007-001`.
- The live response has ROC 94-113 annual rows. National age groups are 30歲以下, 31-40歲, 41-50歲, 51-65歲, and 65歲以上; the age-group sum exactly matches the national total in every downloaded year.
- The New Taipei business-region total is unavailable in ROC 94-100 (`…`) and numeric from ROC 101 onward. The DAG skips those missing local cells and prints the missing years; it does not turn the ellipsis into zero.

## Contract and youth status

- Age is the medication user's age, so `gender=total` is the correct subject dimension. No sex split is emitted because this dataset's target output is the age structure.
- Age intervals are exact representations of source labels: `30歲以下` -> `(None, 30)`, `31-40歲` -> `(31, 40)`, `41-50歲` -> `(41, 50)`, `51-65歲` -> `(51, 65)`, `65歲以上` -> `(65, None)`.
- 18-35 status: **unavailable**. The source bands cross both 18 and 35, and this medication-use count is not a counselling/service-attendance count. No 18-35 apportionment is performed or persisted.

## Checks and limitations

- Every run reconciles each national total to the five age rows and reconciles each numeric New Taipei total to its source cell.
- A 3x scale guard compares annual national totals with the median of the other years. `KNOWN_BAD_YEARS` is empty; no bad year was observed in the live pull.
- The requested direct community psychological-health service source (counselling, community mental-health-center visits, or 安心專線 by age) was not found as a stable public New Taipei age table during this pass. This DAG is therefore explicitly a proxy and must not be labelled as counselling utilization.
