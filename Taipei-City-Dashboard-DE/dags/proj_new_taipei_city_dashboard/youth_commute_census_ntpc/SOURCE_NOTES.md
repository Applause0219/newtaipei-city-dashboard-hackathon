# youth_commute_census_ntpc

## Source verification

- Official catalog: https://data.gov.tw/dataset/162050
- The catalog resource is a DGBAS XML file for the 109th year of the Republic of China calendar (2020). It is a decennial population and housing census, not an annual series.
- The XML record for 新北市 contains a grand total and three mutually exclusive work-location categories: same township/city/district, another township in the same county/city, and another county/city or overseas.
- The DAG uses a browser User-Agent. The DGBAS host can fail local Windows certificate verification; the code retries with `verify=False` only for the exact known host after the normal TLS request fails. Production should install the CA chain and remove that fallback.

## Contract and youth status

- Output: 3 rows, period 2020-01-01 through 2020-12-31, `period_type=census`, `area_code=65000`, `area_level=city`.
- Age fields are all `None` and `age_band_raw` is `None`, because the machine-readable source has no age field. This is recorded as `unavailable`; the 15+ universe is not treated as an 18-35 universe.
- `gender=total` because the source rows are not age- or person-level gender observations.
- 18-35 status: **unavailable**. No age inference and no 18-35 apportionment is performed. The table also does not provide destination city names; its cross-county category is kept as a source-defined scope only.
- `value_type=count`; no rate is scaled by population.

## Checks and limitations

- Every run verifies that the three emitted scope values sum exactly to the input grand total.
- The required scale guard is present, but it reports a controlled skip because this source currently supplies one census year. `KNOWN_BAD_YEARS` is empty; no year was silently excluded.
- This is useful evidence for New Taipei's commuting burden, but it cannot answer the exact number of 18-35 commuters. The age-bearing census report is not the same machine-readable resource used here, so this DAG does not mix report-level age statistics into the fact table.
