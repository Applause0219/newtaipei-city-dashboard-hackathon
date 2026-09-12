# youth_youbike_station_ntpc

## Source verification

- Official catalog: https://data.gov.tw/dataset/146969
- Direct official CSV: `https://data.ntpc.gov.tw/api/datasets/010e5b15-3823-4b20-b401-b1cf000550c5/csv/file`.
- The source is a current station snapshot with `sno`, `sna`, `sarea`, `tot_quantity`, `sbi_quantity`, `bemp`, and `mday`. The downloaded file contained 1,600 New Taipei stations and a single snapshot timestamp.
- This DAG uses the public NTPC CSV directly. The repo's TDX helper remains a separate option, but it requires Airflow Variables `TDX_CLIENT_ID` and `TDX_CLIENT_SECRET`; this DAG does not assume those credentials.

## Contract and youth status

- Each input station emits three rows: total slots, available bikes, and available docks. The district codes exactly match the existing New Taipei DAG contract.
- Age fields and `age_band_raw` are `None`; station inventory has no rider age, gender, or trip-user dimension. 18-35 status: **unavailable**.
- `period_type=instant`; `period_start` and `period_end` are the source `mday` snapshot timestamp. `value_type=count`.

## Checks and limitations

- Unknown New Taipei district names raise instead of being silently dropped.
- Every run reconciles the station row multiplier and the summed total-slots, available-bikes, and available-docks values to the input snapshot.
- The required 3x scale guard exists, but reports a controlled skip because this resource is a single current snapshot rather than a historical annual series. `KNOWN_BAD_YEARS` is empty.
- The output is intentionally station-grain and can be a few thousand rows per snapshot. It is a mobility-resource measure, not a count of youth riders or trips.
