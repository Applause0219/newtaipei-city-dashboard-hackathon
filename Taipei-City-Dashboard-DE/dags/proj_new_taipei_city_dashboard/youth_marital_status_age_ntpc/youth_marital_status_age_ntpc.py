from airflow import DAG
from operators.common_pipeline import CommonDag


API = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "2c5e1515-2b2f-4334-a53e-27fa4adcdbec/json"
)
DATASET_PAGE = "https://data.ntpc.gov.tw/datasets/2c5e1515-2b2f-4334-a53e-27fa4adcdbec"
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

# 主要欄位說明：itemvalue1(年)、itemvalue2(年齡別)，其餘欄位是婚姻狀況
# × 性別。以別名表兼容資料頁曾使用的「item valueN」寫法。
COLUMN_ALIASES = {
    "year": ("itemvalue1", "item value1"),
    "age": ("itemvalue2", "item value2"),
    "all_total": ("itemvalue3", "item value3"),
    "all_male": ("itemvalue4", "item value4"),
    "all_female": ("itemvalue5", "item value5"),
    "unmarried_total": ("itemvalue6", "item value6"),
    "unmarried_male": ("itemvalue7", "item value7"),
    "unmarried_female": ("itemvalue8", "item value8"),
    "married_total": ("itemvalue9", "item value9"),
    "married_male": ("itemvalue10", "item value10"),
    "married_male_same_sex": ("itemvalue11", "item value11"),
    "married_female": ("itemvalue12", "item value12"),
    "married_female_same_sex": ("itemvalue13", "item value13"),
    "divorced_total": ("itemvalue14", "item value14"),
    "divorced_male": ("itemvalue15", "item value15"),
    "divorced_male_same_sex": ("itemvalue16", "item value16"),
    "divorced_female": ("itemvalue17", "item value17"),
    "divorced_female_same_sex": ("itemvalue18", "item value18"),
    "widowed_total": ("itemvalue19", "item value19"),
    "widowed_male": ("itemvalue20", "item value20"),
    "widowed_male_same_sex": ("itemvalue21", "item value21"),
    "widowed_female": ("itemvalue22", "item value22"),
    "widowed_female_same_sex": ("itemvalue23", "item value23"),
}

# (canonical field, marital status, gender, same-sex subset)
METRICS = [
    ("all_total", "all", "total", False),
    ("all_male", "all", "male", False),
    ("all_female", "all", "female", False),
    ("unmarried_total", "unmarried", "total", False),
    ("unmarried_male", "unmarried", "male", False),
    ("unmarried_female", "unmarried", "female", False),
    ("married_total", "married", "total", False),
    ("married_male", "married", "male", False),
    ("married_male_same_sex", "married", "male", True),
    ("married_female", "married", "female", False),
    ("married_female_same_sex", "married", "female", True),
    ("divorced_total", "divorced", "total", False),
    ("divorced_male", "divorced", "male", False),
    ("divorced_male_same_sex", "divorced", "male", True),
    ("divorced_female", "divorced", "female", False),
    ("divorced_female_same_sex", "divorced", "female", True),
    ("widowed_total", "widowed", "total", False),
    ("widowed_male", "widowed", "male", False),
    ("widowed_male_same_sex", "widowed", "male", True),
    ("widowed_female", "widowed", "female", False),
    ("widowed_female_same_sex", "widowed", "female", True),
]

STATUS_GENDER_COLUMNS = {
    "all": {"total": "all_total", "male": "all_male", "female": "all_female"},
    "unmarried": {
        "total": "unmarried_total", "male": "unmarried_male", "female": "unmarried_female"
    },
    "married": {"total": "married_total", "male": "married_male", "female": "married_female"},
    "divorced": {
        "total": "divorced_total", "male": "divorced_male", "female": "divorced_female"
    },
    "widowed": {
        "total": "widowed_total", "male": "widowed_male", "female": "widowed_female"
    },
}


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"婚姻來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"婚姻來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"婚姻來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"婚姻來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _parse_age(label):
    """回傳 (age_lower, age_upper, age_scope)，不認得的標籤直接拋錯。"""
    import re

    s = _text(label, "age")
    if s == "總計":
        return None, None, "all_age"
    match = re.fullmatch(r"未滿(\d{1,3})歲", s)
    if match:
        return None, int(match.group(1)) - 1, "age_band"
    match = re.fullmatch(r"(\d{1,3})[~～-](\d{1,3})歲", s)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"婚姻來源年齡區間上下界顛倒：{label!r}")
        return lower, upper, "age_band"
    match = re.fullmatch(r"(\d{1,3})歲以上", s)
    if match:
        return int(match.group(1)), None, "age_band"
    raise ValueError(f"無法解析婚姻來源年齡標籤：{label!r}")


def _normalise_record(record):
    normal = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        matches = [alias for alias in aliases if alias in record]
        if not matches:
            raise ValueError(
                f"婚姻來源缺少角色欄位 {canonical}；實際欄位：{list(record)}"
            )
        if len(matches) > 1 and record[matches[0]] != record[matches[1]]:
            raise ValueError(f"婚姻來源欄位別名衝突：{matches}")
        normal[canonical] = record[matches[0]]
    return normal


def fetch_records():
    """data.ntpc 頁碼從 0 開始，抓到空頁為止。"""
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
                raise RuntimeError(f"婚姻來源第 {page} 頁不是 JSON 陣列")
            print(f"marital page {page}: {len(page_records)} rows")
            if not page_records:
                return records
            records.extend(page_records)
            page += 1
    raise RuntimeError("婚姻來源分頁超過 100 頁，疑似 API 行為改變。")


SCALE_ANOMALY_FACTOR = 3.0


def drop_scope_anomalies(records):
    """以各年度全人口總計做尺度防護；未知異常直接拋錯。"""
    totals = {}
    for record in records:
        row = _normalise_record(record)
        if _text(row["age"], "age") != "總計":
            continue
        totals[int(_text(row["year"], "year"))] = _number(row["all_total"], "all_total")
    if len(totals) < 3:
        raise RuntimeError("婚姻來源找不到至少三個年度的總計列，無法做尺度檢核。")
    for year, value in sorted(totals.items()):
        peers = [v for y, v in totals.items() if y != year]
        peers.sort()
        median = peers[len(peers) // 2]
        if median and (value > median * SCALE_ANOMALY_FACTOR or value * SCALE_ANOMALY_FACTOR < median):
            raise ValueError(
                f"西元 {year} 年婚姻全人口 total={value:.0f}，"
                f"與其餘年度中位數 {median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍。"
            )
    print(f"marital scale guard: {len(totals)} years passed")
    return records


def _reconcile(records, data):
    """每個來源欄位只輸出一次，且主要狀態 total=male+female。"""
    expected, emitted = {}, {}
    for record in records:
        row = _normalise_record(record)
        year = int(_text(row["year"], "year"))
        age = _text(row["age"], "age")
        for canonical, _, _, _ in METRICS:
            expected[(year, age, canonical)] = _number(row[canonical], canonical)
    for _, row in data.iterrows():
        key = (
            int(row["period_start"][:4]),
            row["age_band_raw"],
            json_field(row["breakdown"], "source_column"),
        )
        emitted[key] = emitted.get(key, 0.0) + float(row["value"])
    if set(expected) != set(emitted):
        raise ValueError("婚姻來源對帳失敗：輸入欄位與輸出欄位集合不一致。")
    for key, value in expected.items():
        if abs(value - emitted[key]) > 0.5:
            raise ValueError(f"婚姻來源對帳失敗：{key} input={value} output={emitted[key]}")

    normalised = [_normalise_record(record) for record in records]
    for row in normalised:
        for status, columns in STATUS_GENDER_COLUMNS.items():
            total = _number(row[columns["total"]], columns["total"])
            male = _number(row[columns["male"]], columns["male"])
            female = _number(row[columns["female"]], columns["female"])
            if abs(total - male - female) > 0.5:
                raise ValueError(
                    f"婚姻來源 {status} total != male + female：{row['year']} {row['age']}"
                )
    print(f"marital reconciliation: {len(expected)} source cells preserved")


def json_field(value, key):
    import json

    obj = json.loads(value)
    return obj[key]


def transform_records(records, data_time=None):
    import json
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    if not records:
        raise RuntimeError("婚姻來源沒有任何資料列。")
    rows = []
    for record in records:
        row = _normalise_record(record)
        roc_year = int(_text(row["year"], "year"))
        age_label = _text(row["age"], "age")
        age_lower, age_upper, age_scope = _parse_age(age_label)
        base = {
            "period_start": f"{roc_year}-01-01",
            "period_end": f"{roc_year}-12-31",
            "period_type": "year",
            "age_lower": age_lower,
            "age_upper": age_upper,
            "age_band_raw": age_label,
            "area_code": "65000",
            "area_level": "city",
            "unit": "人",
            "value_type": "count",
        }
        for canonical, status, gender, same_sex in METRICS:
            indicator_id = (
                "resident_marital_status_same_sex_count"
                if same_sex else "resident_marital_status_count"
            )
            rows.append(
                dict(
                    base,
                    indicator_id=indicator_id,
                    gender=gender,
                    breakdown=json.dumps(
                        {
                            "marital_status": status,
                            "same_sex_subset": same_sex,
                            "age_scope": age_scope,
                            "source_column": canonical,
                        },
                        ensure_ascii=False,
                    ),
                    value=_number(row[canonical], canonical),
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
    dag_folder="youth_marital_status_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
