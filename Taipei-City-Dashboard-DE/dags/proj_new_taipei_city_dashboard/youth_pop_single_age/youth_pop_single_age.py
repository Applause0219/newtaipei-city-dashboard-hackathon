from airflow import DAG
from operators.common_pipeline import CommonDag


def _transfer(**kwargs):
    import json
    import time

    import pandas as pd
    import requests
    from sqlalchemy import create_engine
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )
    from utils.get_time import get_tpe_now_time_str

    # Config
    ready_data_db_uri = kwargs.get("ready_data_db_uri")
    dag_infos = kwargs.get("dag_infos")
    dag_id = dag_infos.get("dag_id")
    load_behavior = dag_infos.get("load_behavior")
    default_table = dag_infos.get("ready_data_default_table")
    history_table = dag_infos.get("ready_data_history_table")

    API_URL = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{yyymm}"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "Chrome/120 Safari/537.36"
        )
    }

    def fetch_month(yyymm, timeout=90):
        """Fetch all pages for one ROC yyymm, keep New Taipei City village rows only."""
        records = []
        page = 1
        while True:
            url = API_URL.format(yyymm=yyymm) + f"?page={page}"
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            body = resp.json()
            if body.get("responseCode") == "OD-0102-S":
                # 查無資料 (no data for this period)
                return []
            data = body.get("responseData", [])
            records.extend(
                [r for r in data if r.get("site_id", "").startswith("新北市")]
            )
            total_page = int(body.get("totalPage", 1))
            if page >= total_page:
                break
            page += 1
            time.sleep(0.2)
        return records

    def find_latest_available_month(roc_year, timeout=90):
        """Try months 12..1 for a given ROC year, return first yyymm with data, else None."""
        for month in range(12, 0, -1):
            yyymm = f"{roc_year:03d}{month:02d}"
            resp = requests.get(
                API_URL.format(yyymm=yyymm), headers=HEADERS, timeout=timeout
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("responseCode") != "OD-0102-S":
                return yyymm
        return None

    def age_columns(record):
        ages = []
        for key in record.keys():
            if key.startswith("people_age_") and key.endswith("_m"):
                ages.append(key[len("people_age_") : -2])
        return sorted(set(ages))

    def melt_month_to_long(records, roc_yyymm):
        """Aggregate village rows to district level, then melt to long-format rows."""
        if not records:
            return []

        raw_df = pd.DataFrame(records)
        people_cols = [c for c in raw_df.columns if c.startswith("people_")]
        numeric_part = raw_df[people_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
        df = pd.concat(
            [
                raw_df[["district_code", "site_id"]].rename(
                    columns={"site_id": "district_name"}
                ),
                numeric_part,
            ],
            axis=1,
        )
        df["area_code"] = df["district_code"].str[:8]

        agg = df.groupby(["area_code", "district_name"], as_index=False)[
            people_cols
        ].sum()

        age_parts = age_columns(records[0])

        yyy = int(roc_yyymm[:3])
        mm = int(roc_yyymm[3:])
        year_ad = yyy + 1911
        period_start = f"{year_ad}-01-01"
        period_end = f"{year_ad}-12-31"
        source_month = f"{year_ad}-{mm:02d}"

        def age_meta(age_part):
            if age_part == "100up":
                return 100, None, "100歲以上"
            age_int = int(age_part)
            return age_int, age_int, f"{age_int}歲"

        rows = []
        # district-level rows
        for _, r in agg.iterrows():
            for age_part in age_parts:
                m_val = r.get(f"people_age_{age_part}_m", 0)
                f_val = r.get(f"people_age_{age_part}_f", 0)
                age_lower, age_upper, age_label = age_meta(age_part)
                base = {
                    "indicator_id": "population_count",
                    "period_start": period_start,
                    "period_end": period_end,
                    "period_type": "year",
                    "age_lower": age_lower,
                    "age_upper": age_upper,
                    "age_band_raw": age_label,
                    "area_code": r["area_code"],
                    "area_level": "district",
                    "breakdown": json.dumps(
                        {
                            "source_month": source_month,
                            "district_name": r["district_name"],
                        },
                        ensure_ascii=False,
                    ),
                    "unit": "人",
                    "value_type": "count",
                }
                rows.append({**base, "gender": "male", "value": m_val})
                rows.append({**base, "gender": "female", "value": f_val})
                rows.append({**base, "gender": "total", "value": m_val + f_val})

        # city-level aggregate row (area_code = 65000)
        city_agg = agg[people_cols].sum()
        for age_part in age_parts:
            m_val = city_agg.get(f"people_age_{age_part}_m", 0)
            f_val = city_agg.get(f"people_age_{age_part}_f", 0)
            age_lower, age_upper, age_label = age_meta(age_part)
            base = {
                "indicator_id": "population_count",
                "period_start": period_start,
                "period_end": period_end,
                "period_type": "year",
                "age_lower": age_lower,
                "age_upper": age_upper,
                "age_band_raw": age_label,
                "area_code": "65000",
                "area_level": "city",
                "breakdown": json.dumps(
                    {"source_month": source_month, "district_name": "新北市"},
                    ensure_ascii=False,
                ),
                "unit": "人",
                "value_type": "count",
            }
            rows.append({**base, "gender": "male", "value": m_val})
            rows.append({**base, "gender": "female", "value": f_val})
            rows.append({**base, "gender": "total", "value": m_val + f_val})

        return rows

    # Historical year-end (December) snapshots, ROC 107 (2018) through ROC 114 (2025),
    # plus the latest available month for the current ROC year (115 = 2026).
    # See SOURCE_NOTES.md for why `replace` + full multi-year rebuild was chosen
    # over `current+history` for this source.
    roc_years_full = list(range(107, 115))
    target_periods = [f"{y:03d}12" for y in roc_years_full]

    current_roc_year = 115
    latest_current_year = find_latest_available_month(current_roc_year)
    if latest_current_year and latest_current_year not in target_periods:
        target_periods.append(latest_current_year)

    all_rows = []
    for yyymm in target_periods:
        print(f"Fetching ODRP014 period {yyymm} ...")
        recs = fetch_month(yyymm)
        print(f"  -> {len(recs)} New Taipei City village records")
        if not recs:
            print(f"  WARNING: no data for {yyymm}, skipping")
            continue
        all_rows.extend(melt_month_to_long(recs, yyymm))

    if not all_rows:
        raise RuntimeError(
            "沒有取得任何期別的資料。這是所有 18-35 分攤的權重基準表，"
            "空表會讓下游每一個分攤數字都失去依據，因此直接失敗而非寫入空表。"
        )

    data = pd.DataFrame(all_rows)

    # ------------------------------------------------------------------
    # 權重基準表的完整性檢核
    #
    # 這張表本身不畫任何一張圖，但所有 apportioned 的 18-35 數字都以它為權重。
    # 它若靜默少了一個年度或一個行政區，下游不會報錯，只會安靜地算錯。
    # 所以這裡寧可失敗，也不要寫入不完整的權重。
    # ------------------------------------------------------------------
    districts = data.loc[data["area_level"] == "district", "area_code"].nunique()
    if districts != 29:
        raise ValueError(
            f"行政區數為 {districts}，新北市應為 29 個。"
            "來源可能缺區或行政區代碼對應有誤，不可作為分攤權重使用。"
        )

    city_total = data[
        (data["area_level"] == "city") & (data["gender"] == "total")
    ]
    by_year = city_total.groupby(city_total["period_start"].str[:4])["value"].sum()
    if by_year.empty:
        raise ValueError("找不到全市 total 列，無法檢核人口總量。")

    # 人口是極穩定的量，年度間不該出現倍數級跳動。
    # 出現就代表來源範圍改變（例如誤放全國數字）或解析錯誤。
    median_pop = by_year.median()
    for year, total in by_year.items():
        if total > median_pop * 1.5 or total * 1.5 < median_pop:
            raise ValueError(
                f"{year} 年全市人口 {total:,.0f} 與各年度中位數 {median_pop:,.0f} "
                "相差超過 1.5 倍。人口不會這樣跳動，來源範圍或解析可能有誤，"
                "不可作為分攤權重使用。"
            )

    # 分齡加總必須等於該區的性別合計，否則 melt 過程漏了欄位。
    male_female = data[data["gender"].isin(["male", "female"])]["value"].sum()
    total_rows = data[data["gender"] == "total"]["value"].sum()
    if abs(male_female - total_rows) > 0.5:
        raise ValueError(
            f"男女列合計 {male_female:,.0f} 不等於 total 列合計 {total_rows:,.0f}，"
            "melt 過程可能漏欄或重複計算。"
        )

    print(
        f"weight table check =========== {districts} districts, "
        f"{len(by_year)} years, city population "
        f"{by_year.min():,.0f}-{by_year.max():,.0f}"
    )

    # age_lower / age_upper 對應 smallint。「100歲以上」組的 age_upper 是 None，
    # 不指定 nullable 整數型別的話 pandas 會把整欄推成 float64，0 歲會變成 0.0，
    # 灌進 smallint 欄位會出錯。
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    data["data_time"] = get_tpe_now_time_str(is_with_tz=True)

    print(f"ready_data =========== {data.head(10)}")
    print(f"ready_data shape =========== {data.shape}")

    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=load_behavior,
        default_table=default_table,
        history_table=history_table,
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_id, data["data_time"].max()
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard", dag_folder="youth_pop_single_age"
)
dag.create_dag(etl_func=_transfer)
