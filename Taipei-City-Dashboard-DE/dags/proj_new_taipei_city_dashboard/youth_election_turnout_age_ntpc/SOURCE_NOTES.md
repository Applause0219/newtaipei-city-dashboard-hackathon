# 中選會年齡別投票率（新北市）

## Source verification

- Source page: <https://web.cec.gov.tw/central/article/58559>
- Downloaded source: the official ZIP linked by that page,
  <https://web.cec.gov.tw/api/file/57480d24-9b5f-42c6-8752-fff879dea939.zip>.
- The ZIP contains a large raw XLSX and a small XLSX codebook. The DAG selects
  the raw sheet by compressed member size, then finds cityid, SEX, AVOTE, and
  single-year age by role. cityid=65 is New Taipei City. AVOTE is the 2024
  presidential election vote
  indicator (1 voted, 0 did not vote, #NULL! missing).
- The parser uses XLSX cell references, so blank cells cannot shift columns.
  It resolves fields by role aliases and raises when a required role or value
  is not recognized.

## Output and 18–35 judgement

- Output is 2024-01-13 to 2024-01-13, period_type=election, area_code=65000,
  area_level=city, and one rate row per single age and gender (male, female,
  total).
- value_type=rate, unit=%. Rates are unweighted known-response sample rates.
  The raw file has survey weights, but this DAG does not silently turn a
  survey sample into an official population count.
- 18–35 = unavailable: this election's source begins at age 20, so ages 18
  and 19 are absent. The DAG does not infer or fill them. Ages 20–35 remain
  available as official single-age source rows. No population scaling is used.
- The 13 missing AVOTE responses are retained in each group's breakdown (four
  fall in the 20–35 source rows); a group with no valid denominator raises
  instead of emitting a fake rate.

## Checks and unresolved items

- Reconciliation checks that, separately for total, male, and female, emitted
  source_record_count equals the filtered New Taipei source rows.
- A recurring scale guard is present. It is not applicable to this single
  election period; KNOWN_BAD_YEARS is empty because no bad year was found.
- This is an official raw survey file underlying CEC turnout analysis, not the
  complete administrative voter file. If a future CEC release provides
  administrative single-age numerator and denominator tables, it should
  replace the sample-rate basis.
