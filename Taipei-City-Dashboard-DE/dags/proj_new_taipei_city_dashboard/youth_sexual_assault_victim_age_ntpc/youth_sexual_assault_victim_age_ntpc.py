from airflow import DAG
from operators.common_pipeline import CommonDag


API = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "fe5a68d6-1313-4695-bf51-30bc0940196d/json"
)
DATASET_PAGE = "https://data.ntpc.gov.tw/datasets/fe5a68d6-1313-4695-bf51-30bc0940196d"
PAGE_SIZE = 5000
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

AGE_BANDS = [
    ("0_5", 0, 5, "0-5歲", "percent4", "percent5"),
    ("6_11", 6, 11, "6-11歲", "percent6", "percent7"),
    ("12_17", 12, 17, "12-17歲", "percent8", "percent9"),
    ("18_23", 18, 23, "18-23歲", "percent10", "percent11"),
    ("24_29", 24, 29, "24-29歲", "percent12", "percent13"),
    ("30_39", 30, 39, "30-39歲", "percent14", "percent15"),
    ("40_49", 40, 49, "40-49歲", "percent16", "percent17"),
    ("50_64", 50, 64, "50-64歲", "percent18", "percent19"),
    ("65_plus", 65, None, "65歲以上", "percent20", "percent21"),
    ("unknown", None, None, "年齡不詳", "percent22", "percent23"),
]

COLUMN_ALIASES = {
    "year": ("field1",),
    "male_total": ("percent2", "percent 2"),
    "female_total": ("percent3", "percent 3"),
}
for _key, _, _, _, _male, _female in AGE_BANDS:
    COLUMN_ALIASES[f"{_key}_male"] = (_male, _male.replace("percent", "percent "))
    COLUMN_ALIASES[f"{_key}_female"] = (_female, _female.replace("percent", "percent "))


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"性侵害被害者來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"性侵害被害者來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"性侵害被害者來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"性侵害被害者來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _parse_age(label):
    import re

    s = _text(label, "age")
    if s == "年齡不詳":
        return None, None, "unknown_age"
    match = re.fullmatch(r"(\d{1,3})-(\d{1,3})歲", s)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"性侵害被害者來源年齡區間上下界顛倒：{label!r}")
        return lower, upper, "age_band"
    match = re.fullmatch(r"(\d{1,3})歲以上", s)
    if match:
        return int(match.group(1)), None, "age_band"
    raise ValueError(f"無法解析性侵害被害者來源年齡標籤：{label!r}")


def _normalise_record(record):
    normal = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        matches = [alias for alias in aliases if alias in record]
        if not matches:
            raise ValueError(
                f"性侵害被害者來源缺少角色欄位 {canonical}；實際欄位：{list(record)}"
            )
        if len(matches) > 1 and record[matches[0]] != record[matches[1]]:
            raise ValueError(f"性侵害被害者來源欄位別名衝突：{matches}")
        normal[canonical] = record[matches[0]]
    return normal


def fetch_records():
    import requests

    records, page = [], 0
    with requests.Session() as session:
        while page < 100:
            response = session.get(
                API,
                headers={"User-Agent": UA},
                params={"page": page, "size": PAGE_SIZE},
                timeout=120,
            )
            response.raise_for_status()
            page_records = response.json()
            if not isinstance(page_records, list):
                raise RuntimeError(f"性侵害被害者來源第 {page} 頁不是 JSON 陣列")
            print(f"sexual victim page {page}: {len(page_records)} rows")
            if not page_records:
                return records
            records.extend(page_records)
            page += 1
    raise RuntimeError("性侵害被害者來源分頁超過 100 頁，疑似 API 行為改變。")


SCALE_ANOMALY_FACTOR = 3.0


def _year_totals(records):
    totals = {}
    for record in records:
        row = _normalise_record(record)
        year = int(_text(row["year"], "year"))
        if year in totals:
            raise ValueError(f"性侵害被害者來源年度重複：{year}")
        totals[year] = _number(row["male_total"], "male_total") + _number(
            row["female_total"], "female_total"
        )
    return totals


def drop_scope_anomalies(records):
    totals = _year_totals(records)
    if len(totals) < 3:
        raise RuntimeError("性侵害被害者來源年度少於三年，無法做尺度檢核。")
    for year, value in sorted(totals.items()):
        peers = sorted(v for y, v in totals.items() if y != year)
        median = peers[len(peers) // 2]
        if median and (value > median * SCALE_ANOMALY_FACTOR or value * SCALE_ANOMALY_FACTOR < median):
            raise ValueError(
                f"西元 {year} 年性侵害被害者 total={value:.0f}，"
                f"與其餘年度中位數 {median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍。"
            )
    print(f"sexual victim scale guard: {len(totals)} years passed")
    return records


def _reconcile(records, data):
    import json

    expected, emitted = {}, {}
    normalised = [_normalise_record(record) for record in records]
    for row in normalised:
        year = int(_text(row["year"], "year"))
        for canonical in ("male_total", "female_total"):
            expected[(year, canonical)] = _number(row[canonical], canonical)
        for key, _, _, _, _, _ in AGE_BANDS:
            for gender in ("male", "female"):
                canonical = f"{key}_{gender}"
                expected[(year, canonical)] = _number(row[canonical], canonical)
    for _, output_row in data.iterrows():
        breakdown = json.loads(output_row["breakdown"])
        key = (
            int(output_row["period_start"][:4]),
            breakdown["source_column"],
        )
        emitted[key] = emitted.get(key, 0.0) + float(output_row["value"])
    if set(expected) != set(emitted):
        raise ValueError("性侵害被害者來源對帳失敗：輸入欄位與輸出欄位集合不一致。")
    for key, value in expected.items():
        if abs(value - emitted[key]) > 0.5:
            raise ValueError(f"性侵害被害者來源對帳失敗：{key} input={value} output={emitted[key]}")

    for row in normalised:
        male_detail = sum(_number(row[f"{key}_male"], f"{key}_male") for key, *_ in AGE_BANDS)
        female_detail = sum(_number(row[f"{key}_female"], f"{key}_female") for key, *_ in AGE_BANDS)
        male_total = _number(row["male_total"], "male_total")
        female_total = _number(row["female_total"], "female_total")
        if abs(male_detail - male_total) > 0.5 or abs(female_detail - female_total) > 0.5:
            raise ValueError(
                f"性侵害被害者來源年度對帳失敗：{row['year']} "
                f"male {male_total}/{male_detail}, female {female_total}/{female_detail}"
            )
    print(f"sexual victim reconciliation: {len(expected)} source cells preserved")


def transform_records(records, data_time=None):
    import json
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    if not records:
        raise RuntimeError("性侵害被害者來源沒有任何資料列。")
    rows = []
    for record in records:
        row = _normalise_record(record)
        year = int(_text(row["year"], "year"))
        base = {
            "period_start": f"{year}-01-01",
            "period_end": f"{year}-12-31",
            "period_type": "year",
            "area_code": "65000",
            "area_level": "city",
            "unit": "人",
            "value_type": "count",
        }
        for gender, canonical in (("male", "male_total"), ("female", "female_total")):
            rows.append(
                dict(
                    base,
                    indicator_id="sexual_assault_victim_total_count",
                    age_lower=None,
                    age_upper=None,
                    age_band_raw=None,
                    gender=gender,
                    breakdown=json.dumps(
                        {"scope": "all_age", "source_column": canonical},
                        ensure_ascii=False,
                    ),
                    value=_number(row[canonical], canonical),
                    data_time=data_time,
                )
            )
        for key, lower, upper, label, _, _ in AGE_BANDS:
            age_lower, age_upper, age_scope = _parse_age(label)
            for gender in ("male", "female"):
                canonical = f"{key}_{gender}"
                rows.append(
                    dict(
                        base,
                        indicator_id="sexual_assault_victim_age_count",
                        age_lower=age_lower,
                        age_upper=age_upper,
                        age_band_raw=label,
                        gender=gender,
                        breakdown=json.dumps(
                            {
                                "scope": "age_detail",
                                "age_scope": age_scope,
                                "source_column": canonical,
                            },
                            ensure_ascii=False,
                        ),
                        value=_number(row[canonical], canonical),
                        data_time=data_time,
                    )
                )
    if data_time is None:
        data_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
        for row in rows:
            row["data_time"] = data_time
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    _reconcile(records, data)
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    ready_data_db_uri = kwargs.get("ready_data_db_uri")
    dag_infos = kwargs.get("dag_infos")
    data = transform_records(
        drop_scope_anomalies(fetch_records()),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")
    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos.get("load_behavior"),
        default_table=dag_infos.get("ready_data_default_table"),
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_infos.get("dag_id"), data["data_time"].max()
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_sexual_assault_victim_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
