# 新北市文化志工年齡組

## Source verification

- Dataset page: <https://data.ntpc.gov.tw/datasets/0e719f1c-c1a8-4428-9d46-db942ed414a9>
- JSON endpoint: <https://data.ntpc.gov.tw/api/datasets/0e719f1c-c1a8-4428-9d46-db942ed414a9/json?page=0&size=1000>
- The dataset page's 主要欄位說明 maps field1 to year, itemvalue6/7 to
  cultural bureau library/museum/park volunteer totals by male/female, and
  itemvalue8/9 through itemvalue20/21 to the seven age bands by male/female.
  The code keeps this mapping in a role alias table and raises if a role is
  absent.
- The API currently returns 2006–2024 (19 source years). The age-field sum
  equals the source male-plus-female total for every year except 2013, where
  the published aggregate is 2,573 and the age fields sum to 2,574. This
  one-unit source arithmetic discrepancy is explicitly allow-listed after
  review; the DAG uses the raw age-field sum as the input total, emits no
  correction, and records both totals and the difference in breakdown.

## Output and 18–35 judgement

- Output is one row per source year, age band and gender, with
  period_type=calendar_year, area_code=65000, and area_level=city.
- Age parsing is explicit: 未滿12歲 -> (None, 11), 12_17歲 -> (12, 17),
  18_29歲 -> (18, 29), 30_49歲 -> (30, 49), 50_54歲 -> (50, 54),
  55_64歲 -> (55, 64), 65歲以上 -> (65, None).
- 18–35 = apportioned at query time: 18–29 is fully covered, while the
  30–49 source band crosses age 35. This DAG emits raw bands only; it does
  not bake an 18–35 split into ETL. This is a behaviour count, so any later
  18–35 apportionment must be documented as an assumption.

## Checks and unresolved items

- Every year checks age-band output sum against the input male-plus-female
  age-field total, then checks the full output again. A new, unallow-listed
  discrepancy raises immediately.
- A recurring three-times scale guard compares each positive year with the
  median of the other positive years. KNOWN_BAD_YEARS is empty because the
  verified series has no year exceeding the guard.
- This source has no district dimension and no library borrower age field;
  this DAG covers cultural volunteer participation only, not age-specific
  borrowing.
