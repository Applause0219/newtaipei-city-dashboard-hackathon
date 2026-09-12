"""新北市 98 年以前自殺通報人數資料 DAG。"""

import json
import re
import statistics
import unicodedata

from airflow import DAG
from operators.common_pipeline import CommonDag


API = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "90B1AB09-151C-4567-A6C0-2D5430C2C0EB/json"
)
DATASET_URL = "https://data.ntpc.gov.tw/datasets/90B1AB09-151C-4567-A6C0-2D5430C2C0EB"
PAGE_SIZE = 1000
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]
SCALE_ANOMALY_FACTOR = 3.0
KNOWN_BAD_YEARS = {}

AGE_SPECS = [
    ("0_11歲", 0, 11, "zero_eleven_male", "zero_eleven_female"),
    ("12_17歲", 12, 17, "twelve_seventeen_male", "twelve_seventeen_female"),
    ("18_19歲", 18, 19, "eighteen_nineteen_male", "eighteen_nineteen_female"),
    ("20_29歲", 20, 29, "twenty_twenty_nine_male", "twenty_twenty_nine_female"),
    ("30_39歲", 30, 39, "thirty_thirty_nine_male", "thirty_thirty_nine_female"),
    ("40_49歲", 40, 49, "forty_forty_nine_male", "forty_forty_nine_female"),
    ("50_59歲", 50, 59, "fifty_fifty_nine_male", "fifty_fifty_nine_female"),
    ("60歲以上", 60, None, "sixty_plus_male", "sixty_plus_female"),
    ("不詳", None, None, "unknown_male", "unknown_female"),
]


def _norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).lower()


def _blank(value):
    return value is None or str(value).strip().lower() in {"", "nan", "none", "null"}


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"自殺通報來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"自殺通報來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"自殺通報來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"自殺通報來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _resolve_columns(records):
    keys = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    aliases = {
        "year": ("field1", "年", "year"),
        "total_male": ("item value2", "itemvalue2", "總計男"),
        "total_female": ("item value3", "itemvalue3", "總計女"),
    }
    source_numbers = iter(range(4, 22))
    for _, _, _, male_role, female_role in AGE_SPECS:
        male_number = next(source_numbers)
        female_number = next(source_numbers)
        aliases[male_role] = (f"item value{male_number}", f"itemvalue{male_number}")
        aliases[female_role] = (f"item value{female_number}", f"itemvalue{female_number}")
    resolved = {}
    used = {}
    for role, role_aliases in aliases.items():
        matches = [
            key for key in keys
            if any(_norm(key) == _norm(alias) for alias in role_aliases)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"自殺通報來源語意欄位 {role} 找不到唯一欄名；"
                f"候選={role_aliases}、實際={keys}"
            )
        actual = matches[0]
        if actual in used:
            raise ValueError(
                f"自殺通報來源欄位 {actual!r} 同時對應 {used[actual]} 與 {role}"
            )
        resolved[role] = actual
        used[actual] = role
    return resolved


def fetch_records():
    import requests

    records = []
    with requests.Session() as session:
        for page in range(100):
            response = session.get(
                API,
                headers={"User-Agent": UA},
                params={"page": page, "size": PAGE_SIZE},
                timeout=120,
            )
            response.raise_for_status()
            page_records = response.json()
            if not isinstance(page_records, list):
                raise RuntimeError(
                    f"自殺通報來源第 {page} 頁不是 JSON 陣列：{type(page_records).__name__}"
                )
            print(f"old suicide report page {page}: {len(page_records)} rows")
            if not page_records:
                return records
            records.extend(page_records)
    raise RuntimeError("自殺通報來源分頁超過 100 頁，疑似 API 行為改變。")


def drop_empty_records(records):
    """只丟棄來源明確標示的全空列；部分空列直接失敗。"""
    if not records:
        raise RuntimeError("自殺通報來源沒有任何資料列")
    kept = []
    dropped = 0
    for row_number, record in enumerate(records, start=1):
        if all(_blank(value) for value in record.values()):
            dropped += 1
            continue
        if _blank(record.get("field1")):
            raise ValueError(f"自殺通報來源第 {row_number} 列年欄為空但不是全空列")
        kept.append(record)
    if dropped:
        print(f"old suicide report: drop {dropped} explicit blank rows")
    if not kept:
        raise RuntimeError("自殺通報來源只剩全空列")
    return kept


def _year(record, columns):
    raw = _text(record[columns["year"]], "year")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"自殺通報來源 year 不是整數：{raw!r}") from exc
    if value < 1900 or value > 2200:
        raise ValueError(f"自殺通報來源 year 超出西元範圍：{value}")
    return value


def drop_scope_anomalies(records):
    """以全年齡男女總數做年度尺度防護。"""
    if not records:
        raise RuntimeError("自殺通報來源沒有任何有效資料列")
    columns = _resolve_columns(records)
    totals = {}
    for record in records:
        year = _year(record, columns)
        if year in totals:
            raise ValueError(f"自殺通報來源年度 {year} 出現重複資料列")
        totals[year] = (
            _number(record[columns["total_male"]], "total_male")
            + _number(record[columns["total_female"]], "total_female")
        )
    kept_totals = {year: value for year, value in totals.items()
                   if year not in KNOWN_BAD_YEARS}
    if len(kept_totals) >= 3:
        for year, total in sorted(kept_totals.items()):
            peers = [value for other_year, value in kept_totals.items() if other_year != year]
            median = statistics.median(peers)
            if median and (total > median * SCALE_ANOMALY_FACTOR
                           or total * SCALE_ANOMALY_FACTOR < median):
                raise ValueError(
                    f"自殺通報來源 {year} total={total:.0f} 與其餘年度中位數 "
                    f"{median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                    "請先確認統計範圍，不要靜默入庫。"
                )
    print(f"old suicide report scope guard: keep years={sorted(kept_totals)}")
    return [
        record for record in records
        if _year(record, columns) not in KNOWN_BAD_YEARS
    ]


def _parse_age(raw_label):
    label = unicodedata.normalize("NFKC", str(raw_label)).strip()
    if label in {"不詳"}:
        return None, None
    if label == "60歲以上":
        return 60, None
    match = re.fullmatch(r"(\d{1,3})[_~～至-](\d{1,3})歲", label)
    if not match:
        raise ValueError(f"無法解析自殺通報來源年齡標籤：{raw_label!r}")
    lower, upper = int(match.group(1)), int(match.group(2))
    if lower > upper:
        raise ValueError(f"自殺通報來源年齡上下界顛倒：{raw_label!r}")
    return lower, upper


def _reconcile(records, data, columns):
    decoded = data["breakdown"].map(json.loads)
    for record in records:
        year = _year(record, columns)
        for gender, total_role in (("male", "total_male"), ("female", "total_female")):
            age_roles = [spec[3 if gender == "male" else 4] for spec in AGE_SPECS]
            expected = _number(record[columns[total_role]], total_role)
            detail_expected = sum(
                _number(record[columns[role]], role) for role in age_roles
            )
            if abs(expected - detail_expected) > 0.5:
                raise ValueError(
                    f"自殺通報 {year} {gender} 年齡分布對帳失敗："
                    f"總計 {expected}、分齡 {detail_expected}"
                )
            source_mask = (
                (data["period_start"] == f"{year}-01-01")
                & (data["gender"] == gender)
                & decoded.map(lambda item: item["source_column"] == columns[total_role])
            )
            emitted_total = float(data.loc[source_mask, "value"].sum())
            if abs(expected - emitted_total) > 0.5:
                raise ValueError(
                    f"自殺通報欄位 {total_role} 對帳失敗："
                    f"輸入 {expected}、輸出 {emitted_total}"
                )
            age_mask = (
                (data["period_start"] == f"{year}-01-01")
                & (data["gender"] == gender)
                & decoded.map(lambda item: item["age_scope"] == "age_band"
                              or item["age_scope"] == "unknown_age")
            )
            emitted_detail = float(data.loc[age_mask, "value"].sum())
            if abs(expected - emitted_detail) > 0.5:
                raise ValueError(
                    f"自殺通報 {year} {gender} 輸入／輸出對帳失敗："
                    f"輸入 {expected}、輸出 {emitted_detail}"
                )


def transform_records(records, data_time=None):
    import pandas as pd

    if not records:
        raise RuntimeError("自殺通報來源沒有任何資料列")
    columns = _resolve_columns(records)
    loaded_at = data_time
    if loaded_at is None:
        from datetime import datetime, timedelta, timezone
        loaded_at = datetime.now(timezone(timedelta(hours=8))).isoformat()
    rows = []
    for record in records:
        year = _year(record, columns)
        for gender, total_role in (("male", "total_male"), ("female", "total_female")):
            rows.append({
                "indicator_id": "suicide_reported_total_count",
                "period_start": f"{year}-01-01",
                "period_end": f"{year}-12-31",
                "period_type": "year",
                "age_lower": None,
                "age_upper": None,
                "age_band_raw": "總計",
                "gender": gender,
                "area_code": "65000",
                "area_level": "city",
                "breakdown": json.dumps({
                    "source_dataset": DATASET_URL,
                    "source_column": columns[total_role],
                    "age_scope": "all_age",
                    "youth_18_35_status": "unavailable",
                    "note": "全年齡總計沒有可用年齡界線",
                }, ensure_ascii=False),
                "value": _number(record[columns[total_role]], total_role),
                "unit": "人",
                "value_type": "count",
                "data_time": loaded_at,
            })
        for raw_label, lower, upper, male_role, female_role in AGE_SPECS:
            if _parse_age(raw_label) != (lower, upper):
                raise ValueError(f"自殺通報內部年齡規則錯誤：{raw_label}")
            age_scope = "unknown_age" if raw_label == "不詳" else "age_band"
            youth_status = (
                "exact" if raw_label in {"18_19歲", "20_29歲"}
                else "apportioned" if raw_label == "30_39歲"
                else "unavailable"
            )
            for gender, role in (("male", male_role), ("female", female_role)):
                rows.append({
                    "indicator_id": "suicide_report_count_by_age",
                    "period_start": f"{year}-01-01",
                    "period_end": f"{year}-12-31",
                    "period_type": "year",
                    "age_lower": lower,
                    "age_upper": upper,
                    "age_band_raw": raw_label,
                    "gender": gender,
                    "area_code": "65000",
                    "area_level": "city",
                    "breakdown": json.dumps({
                        "source_dataset": DATASET_URL,
                        "source_column": columns[role],
                        "source_age_label": raw_label,
                        "age_scope": age_scope,
                        "youth_18_35_status": youth_status,
                        "note": "ETL 保留原始年齡組，不輸出 18-35 分攤值",
                    }, ensure_ascii=False),
                    "value": _number(record[columns[role]], role),
                    "unit": "人",
                    "value_type": "count",
                    "data_time": loaded_at,
                })
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(f"自殺通報事實表欄位不符合契約：{list(data.columns)}")
    _reconcile(records, data, columns)
    for column in CONTRACT_COLUMNS:
        if column in {"age_lower", "age_upper"}:
            continue
        if data[column].isna().any():
            raise ValueError(f"自殺通報契約欄位 {column} 出現 NULL")
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos")
    records = drop_empty_records(fetch_records())
    data = transform_records(
        drop_scope_anomalies(records),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")
    engine = create_engine(kwargs.get("ready_data_db_uri"))
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
    dag_folder="youth_suicide_report_old_ntpc",
)
dag.create_dag(etl_func=_transfer)
