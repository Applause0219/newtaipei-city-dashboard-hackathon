"""新北市離婚人口年齡結構資料 DAG。"""

import json
import re
import statistics
import unicodedata

from airflow import DAG
from operators.common_pipeline import CommonDag


API = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "f9618881-5cf2-4bcc-83b1-5021885dd4f3/json"
)
DATASET_URL = "https://data.ntpc.gov.tw/datasets/f9618881-5cf2-4bcc-83b1-5021885dd4f3"
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
    ("15_19歲", 15, 19, "fifteen_nineteen_male", "fifteen_nineteen_female"),
    ("20_24歲", 20, 24, "twenty_twenty_four_male", "twenty_twenty_four_female"),
    ("25_29歲", 25, 29, "twenty_five_twenty_nine_male", "twenty_five_twenty_nine_female"),
    ("30_34歲", 30, 34, "thirty_thirty_four_male", "thirty_thirty_four_female"),
    ("35_39歲", 35, 39, "thirty_five_thirty_nine_male", "thirty_five_thirty_nine_female"),
    ("40歲以上", 40, None, "forty_plus_male", "forty_plus_female"),
]


def _norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).lower()


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"離婚來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"離婚來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"離婚來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"離婚來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _resolve_columns(records):
    """依資料頁主要欄位說明解析 percent2–percent13。"""
    keys = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    aliases = {"year": ("field1", "年", "year")}
    source_numbers = iter(range(2, 14))
    for _, _, _, male_role, female_role in AGE_SPECS:
        number = next(source_numbers)
        aliases[male_role] = (
            f"percent{number}", f"percent{number:02d}",
        )
        number = next(source_numbers)
        aliases[female_role] = (
            f"percent{number}", f"percent{number:02d}",
        )
    resolved = {}
    used = {}
    for role, role_aliases in aliases.items():
        matches = [
            key for key in keys
            if any(_norm(key) == _norm(alias) for alias in role_aliases)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"離婚來源語意欄位 {role} 找不到唯一欄名；"
                f"候選={role_aliases}、實際={keys}"
            )
        actual = matches[0]
        if actual in used:
            raise ValueError(
                f"離婚來源欄位 {actual!r} 同時對應 {used[actual]} 與 {role}"
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
                    f"離婚來源第 {page} 頁不是 JSON 陣列：{type(page_records).__name__}"
                )
            print(f"divorce age structure page {page}: {len(page_records)} rows")
            if not page_records:
                return records
            records.extend(page_records)
    raise RuntimeError("離婚來源分頁超過 100 頁，疑似 API 行為改變。")


def _year(record, columns):
    raw = _text(record[columns["year"]], "year")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"離婚來源 year 不是整數：{raw!r}") from exc
    if value < 1900 or value > 2200:
        raise ValueError(f"離婚來源 year 超出西元範圍：{value}")
    return value


def drop_scope_anomalies(records):
    """對每年度男女百分比分布做 3 倍尺度防護。"""
    if not records:
        raise RuntimeError("離婚來源沒有任何資料列")
    columns = _resolve_columns(records)
    totals = {}
    for record in records:
        year = _year(record, columns)
        if year in totals:
            raise ValueError(f"離婚來源年度 {year} 出現重複資料列")
        totals[year] = sum(
            _number(record[columns[role]], role)
            for _, _, _, male_role, female_role in AGE_SPECS
            for role in (male_role, female_role)
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
                    f"離婚來源 {year} total={total:.6f} 與其餘年度中位數 "
                    f"{median:.6f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                    "請先確認統計範圍，不要靜默入庫。"
                )
    print(f"divorce scope guard: keep years={sorted(kept_totals)}")
    return [
        record for record in records
        if _year(record, columns) not in KNOWN_BAD_YEARS
    ]


def _parse_age(raw_label):
    label = unicodedata.normalize("NFKC", str(raw_label)).strip()
    if label == "40歲以上":
        return 40, None
    match = re.fullmatch(r"(\d{1,3})[_~～至-](\d{1,3})歲", label)
    if not match:
        raise ValueError(f"無法解析離婚來源年齡標籤：{raw_label!r}")
    lower, upper = int(match.group(1)), int(match.group(2))
    if lower > upper:
        raise ValueError(f"離婚來源年齡上下界顛倒：{raw_label!r}")
    return lower, upper


def _reconcile(records, data, columns):
    decoded = data["breakdown"].map(json.loads)
    for record in records:
        year = _year(record, columns)
        for gender, suffix in (("male", "male"), ("female", "female")):
            roles = [spec[3 if gender == "male" else 4] for spec in AGE_SPECS]
            expected = sum(_number(record[columns[role]], role) for role in roles)
            if abs(expected - 100.0) > 0.05:
                raise ValueError(
                    f"離婚來源 {year} {suffix} 年齡結構未加總至 100：{expected}"
                )
            mask = (
                (data["period_start"] == f"{year}-01-01")
                & (data["gender"] == gender)
            )
            emitted = float(data.loc[mask, "value"].sum())
            if abs(expected - emitted) > 0.05:
                raise ValueError(
                    f"離婚來源 {year} {suffix} 輸入／輸出對帳失敗："
                    f"輸入 {expected}%、輸出 {emitted}%"
                )
            for _, _, _, male_role, female_role in AGE_SPECS:
                role = male_role if gender == "male" else female_role
                source_value = _number(record[columns[role]], role)
                cell_mask = (
                    (data["period_start"] == f"{year}-01-01")
                    & (data["gender"] == gender)
                    & decoded.map(lambda item: item["source_column"] == columns[role])
                )
                emitted_cell = float(data.loc[cell_mask, "value"].sum())
                if abs(source_value - emitted_cell) > 0.05:
                    raise ValueError(
                        f"離婚來源欄位 {role} 對帳失敗："
                        f"輸入 {source_value}%、輸出 {emitted_cell}%"
                    )


def transform_records(records, data_time=None):
    import pandas as pd

    if not records:
        raise RuntimeError("離婚來源沒有任何資料列")
    columns = _resolve_columns(records)
    loaded_at = data_time
    if loaded_at is None:
        from datetime import datetime, timedelta, timezone
        loaded_at = datetime.now(timezone(timedelta(hours=8))).isoformat()
    rows = []
    for record in records:
        year = _year(record, columns)
        for raw_label, lower, upper, male_role, female_role in AGE_SPECS:
            if _parse_age(raw_label) != (lower, upper):
                raise ValueError(f"離婚內部年齡規則錯誤：{raw_label}")
            for gender, role in (("male", male_role), ("female", female_role)):
                rows.append({
                    "indicator_id": "divorce_age_structure_rate",
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
                        "age_scope": "age_band",
                        "youth_18_35_status": "apportioned",
                        "note": "來源是性別內年齡結構比；不可依人口比例縮放，也不在 ETL 固化 18-35",
                    }, ensure_ascii=False),
                    # API 的 percent 欄位已是百分點；每個性別六欄合計 100。
                    "value": _number(record[columns[role]], role),
                    "unit": "%",
                    "value_type": "rate",
                    "data_time": loaded_at,
                })
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(f"離婚事實表欄位不符合契約：{list(data.columns)}")
    _reconcile(records, data, columns)
    for column in CONTRACT_COLUMNS:
        if column in {"age_lower", "age_upper"}:
            continue
        if data[column].isna().any():
            raise ValueError(f"離婚契約欄位 {column} 出現 NULL")
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos")
    data = transform_records(
        drop_scope_anomalies(fetch_records()),
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
    dag_folder="youth_divorce_age_structure_ntpc",
)
dag.create_dag(etl_func=_transfer)
