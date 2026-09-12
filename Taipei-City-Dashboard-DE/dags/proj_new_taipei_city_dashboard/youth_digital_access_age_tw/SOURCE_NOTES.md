# youth_digital_access_age_tw

## Source verification

- Official catalog: https://data.gov.tw/dataset/5960
- Direct official CSV resource: `https://www-api.moda.gov.tw/OpenData/Files/4303`.
- The CSV is CP950/Big5 and contains quoted headers with line breaks; parsing uses Python's `csv.reader`, not comma splitting.
- Observed records cover ROC 93-114 (2004-2025), with ROC 110 absent from the downloaded file. The source is annual, but age-band definitions change over time.

## Contract and youth status

- Age labels are parsed into integer intervals. ROC 101 changes 15-20 to 15-19, 21-30 to 20-29, etc.; ROC 109 changes the 15-20 source group to 12-19; ROC 102 adds 65+ and changes the older group to 60-64. The original header text is retained in `age_band_raw`.
- New Taipei total internet-use rows use `area_code=65000`, `area_level=city`; age rows are Taiwan-wide because the published age breakdown has no New Taipei age dimension.
- 18-35 status: **unavailable**. These are rates, not counts, and the source provides no denominator for a defensible exact conversion. Rates are not population-scaled. No fixed 18-35 apportionment is put into ETL.
- `gender=total`; no gender-specific age rows are present in this resource.

## Checks and limitations

- Every numeric source cell selected by the role map is required to appear exactly once in the output; a missing or extra emitted cell raises.
- A 3x scale guard compares annual total internet-use rates to the median of other years. `KNOWN_BAD_YEARS` is empty because no bad year was found in the live pull.
- The dataset measures digital access/use, not commuting or mental-health service use. It is a national age profile plus a New Taipei total rate, not a New Taipei youth rate.
