from airflow import DAG
from operators.common_pipeline import CommonDag


API = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "7854f7b9-894c-4505-b62b-a2053575b80a/json"
)
DATASET_PAGE = "https://data.ntpc.gov.tw/datasets/7854f7b9-894c-4505-b62b-a2053575b80a"
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

# 資料集頁的主要欄位說明使用 itemvalueN；保留 item valueN 別名，
# 避免欄名格式變動時靜默產出錯誤資料。
COLUMN_ALIASES = {
    "year": ("field1",),
    "age": ("itemvalue2", "item value2"),
    "gender": ("itemvalue3", "item value3"),
}

METRICS = [
    ("total_graduated", "itemvalue4", "總計畢業"),
    ("total_not_graduated", "itemvalue5", "總計肄業"),
    ("literate_graduated", "itemvalue6", "識字者畢業"),
    ("literate_not_graduated", "itemvalue7", "識字者肄業"),
    ("doctoral_graduated", "itemvalue8", "博士畢業"),
    ("doctoral_not_graduated", "itemvalue9", "博士肄業"),
    ("master_graduated", "itemvalue10", "碩士畢業"),
    ("master_not_graduated", "itemvalue11", "碩士肄業"),
    ("university_graduated", "itemvalue12", "大學_獨立學院_畢業"),
    ("university_not_graduated", "itemvalue13", "大學_獨立學院_肄業"),
    ("junior_college_2_3y_graduated", "itemvalue14", "專科_二_三年制畢業"),
    ("junior_college_2_3y_not_graduated", "itemvalue15", "專科_二_三年制肄業"),
    ("junior_college_5y_last2_graduated", "itemvalue16", "專科_五年制_後兩年畢業"),
    ("junior_college_5y_last2_not_graduated", "itemvalue17", "專科_五年制_後兩年肄業"),
    ("junior_college_5y_first3_graduated", "itemvalue18", "專科_五年制_前三年畢業"),
    ("junior_college_5y_first3_not_graduated", "itemvalue19", "專科_五年制_前三年肄業"),
    ("senior_high_graduated", "itemvalue20", "高中畢業"),
    ("senior_high_not_graduated", "itemvalue21", "高中肄業"),
    ("vocational_high_graduated", "itemvalue22", "高職畢業"),
    ("vocational_high_not_graduated", "itemvalue23", "高職肄業"),
    ("junior_high_graduated", "itemvalue24", "國_初_中畢業"),
    ("junior_high_not_graduated", "itemvalue25", "國_初_中肄業"),
    ("junior_vocational_graduated", "itemvalue26", "初職畢業"),
    ("junior_vocational_not_graduated", "itemvalue27", "初職肄業"),
    ("elementary_graduated", "itemvalue28", "小學畢業"),
    ("elementary_not_graduated", "itemvalue29", "小學肄業"),
    ("self_study", "itemvalue30", "自修"),
    ("illiterate", "itemvalue31", "不識字者"),
]

# 1998 年 25-29 歲、專科五年制前三年肄業的來源三欄本身不相等。
# 保留來源原值；只有這一個已知的來源內部不一致可被容許，其餘新不一致要 raise。
KNOWN_COMPONENT_MISMATCHES = {
    (1998, "25~29歲", "junior_college_5y_first3_not_graduated"),
}


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"教育來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"教育來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"教育來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"教育來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _parse_age(label):
    """回傳 (age_lower, age_upper, age_scope)，未知標籤直接拋錯。"""
    import re

    s = _text(label, "age")
    if s == "15歲以上總人口":
        return 15, None, "summary"
    match = re.fullmatch(r"(\d{1,3})[~～-](\d{1,3})歲", s)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"教育來源年齡區間上下界顛倒：{label!r}")
        return lower, upper, "age_band"
    match = re.fullmatch(r"(\d{1,3})歲以上", s)
    if match:
        return int(match.group(1)), None, "age_band"
    raise ValueError(f"無法解析教育來源年齡標籤：{label!r}")


def _normalise_record(record):
    normal = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        matches = [alias for alias in aliases if alias in record]
        if not matches:
            raise ValueError(
                f"教育來源缺少角色欄位 {canonical}；實際欄位：{list(record)}"
            )
        if len(matches) > 1 and record[matches[0]] != record[matches[1]]:
            raise ValueError(f"教育來源欄位別名衝突：{matches}")
        normal[canonical] = record[matches[0]]
    for canonical, source_column, _ in METRICS:
        aliases = (source_column, source_column.replace("itemvalue", "item value"))
        matches = [alias for alias in aliases if alias in record]
        if not matches:
            raise ValueError(
                f"教育來源缺少角色欄位 {canonical}；實際欄位：{list(record)}"
            )
        if len(matches) > 1 and record[matches[0]] != record[matches[1]]:
            raise ValueError(f"教育來源欄位別名衝突：{matches}")
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
                raise RuntimeError(f"教育來源第 {page} 頁不是 JSON 陣列")
            print(f"education page {page}: {len(page_records)} rows")
            if not page_records:
                return records
            records.extend(page_records)
            page += 1
    raise RuntimeError("教育來源分頁超過 100 頁，疑似 API 行為改變。")


SCALE_ANOMALY_FACTOR = 3.0


def drop_scope_anomalies(records):
    """用 15 歲以上總人口的畢業人數做年度尺度防護。"""
    totals = {}
    for record in records:
        row = _normalise_record(record)
        if (
            _text(row["age"], "age") == "15歲以上總人口"
            and _text(row["gender"], "gender") == "計"
        ):
            year = int(_text(row["year"], "year"))
            totals[year] = _number(row["total_graduated"], "total_graduated")
    if len(totals) < 3:
        raise RuntimeError("教育來源找不到至少三個年度的 15 歲以上總人口列。")
    for year, value in sorted(totals.items()):
        peers = sorted(v for y, v in totals.items() if y != year)
        median = peers[len(peers) // 2]
        if median and (value > median * SCALE_ANOMALY_FACTOR or value * SCALE_ANOMALY_FACTOR < median):
            raise ValueError(
                f"西元 {year} 年教育 total_graduated={value:.0f}，"
                f"與其餘年度中位數 {median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍。"
            )
    print(f"education scale guard: {len(totals)} years passed")
    return records


def _reconcile(records, data):
    import json

    expected, emitted = {}, {}
    normalised = [_normalise_record(record) for record in records]
    for row in normalised:
        year = int(_text(row["year"], "year"))
        age = _text(row["age"], "age")
        gender = _text(row["gender"], "gender")
        for canonical, _, _ in METRICS:
            expected[(year, age, gender, canonical)] = _number(row[canonical], canonical)
    for _, row in data.iterrows():
        breakdown = json.loads(row["breakdown"])
        key = (
            int(row["period_start"][:4]),
            row["age_band_raw"],
            breakdown["source_category"],
            breakdown["source_column"],
        )
        emitted[key] = emitted.get(key, 0.0) + float(row["value"])
    if set(expected) != set(emitted):
        raise ValueError("教育來源對帳失敗：輸入欄位與輸出欄位集合不一致。")
    for key, value in expected.items():
        if abs(value - emitted[key]) > 0.5:
            raise ValueError(f"教育來源對帳失敗：{key} input={value} output={emitted[key]}")

    mismatches = []
    by_key = {(row["year"], _text(row["age"], "age")): row for row in normalised}
    for (year, age), rows_for_age in by_key.items():
        del rows_for_age
        group = [
            row for row in normalised
            if row["year"] == year and _text(row["age"], "age") == age
        ]
        by_gender = {_text(row["gender"], "gender"): row for row in group}
        if not {"計", "男", "女"}.issubset(by_gender):
            raise ValueError(f"教育來源 {year} {age} 缺少計／男／女其中一類")
        for canonical, _, _ in METRICS:
            values = {
                gender: _number(by_gender[gender][canonical], canonical)
                for gender in ("計", "男", "女")
            }
            if abs(values["計"] - values["男"] - values["女"]) > 0.5:
                mismatch = (int(year), age, canonical)
                mismatches.append((mismatch, values))
                if mismatch not in KNOWN_COMPONENT_MISMATCHES:
                    raise ValueError(
                        f"教育來源 total != male + female：{mismatch} {values}"
                    )
    if mismatches:
        print(
            f"education source warning: {len(mismatches)} known component mismatch(es) "
            "retained without correction"
        )
    print(f"education reconciliation: {len(expected)} source cells preserved")


def transform_records(records, data_time=None):
    import json
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    if not records:
        raise RuntimeError("教育來源沒有任何資料列。")
    rows = []
    for record in records:
        row = _normalise_record(record)
        year = int(_text(row["year"], "year"))
        age_label = _text(row["age"], "age")
        age_lower, age_upper, age_scope = _parse_age(age_label)
        source_category = _text(row["gender"], "gender")
        gender = {"計": "total", "男": "male", "女": "female"}.get(source_category)
        if gender is None:
            raise ValueError(f"教育來源出現未知類別：{source_category!r}")
        indicator_id = (
            "education_attainment_summary_count"
            if age_scope == "summary" else "education_attainment_count"
        )
        for canonical, _, source_label in METRICS:
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "period_start": f"{year}-01-01",
                    "period_end": f"{year}-12-31",
                    "period_type": "year",
                    "age_lower": age_lower,
                    "age_upper": age_upper,
                    "age_band_raw": age_label,
                    "gender": gender,
                    "area_code": "65000",
                    "area_level": "city",
                    "breakdown": json.dumps(
                        {
                            "attainment": canonical,
                            "source_label": source_label,
                            "source_column": canonical,
                            "source_field": next(
                                source_column
                                for metric, source_column, _ in METRICS
                                if metric == canonical
                            ),
                            "source_category": source_category,
                            "age_scope": age_scope,
                        },
                        ensure_ascii=False,
                    ),
                    "value": _number(row[canonical], canonical),
                    "unit": "人",
                    "value_type": "count",
                    "data_time": data_time,
                }
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
    dag_folder="youth_education_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
