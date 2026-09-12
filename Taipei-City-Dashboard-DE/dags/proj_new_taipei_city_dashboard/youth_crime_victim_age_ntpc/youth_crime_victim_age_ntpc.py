"""新北市刑事案件被害者年齡／性別資料 DAG。"""

import json
import re
import unicodedata

from airflow import DAG
from operators.common_pipeline import CommonDag


API = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "56c7b268-8d0f-4cda-b6e2-6fc4131533cc/json"
)
DATASET_URL = "https://data.ntpc.gov.tw/datasets/56c7b268-8d0f-4cda-b6e2-6fc4131533cc"
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

SCALE_ANOMALY_FACTOR = 3.0
# 民國 95、96 年 API 仍回傳一列，但總計與所有年齡／性別欄位全為 0；
# 這是已確認的空年度，不把它當成有效的零值年度參與尺度判斷。
KNOWN_BAD_YEARS = {
    2006: "來源所有欄位為 0，視為未提供年度",
    2007: "來源所有欄位為 0，視為未提供年度",
}


def _norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).lower()


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"被害者來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"被害者來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"被害者來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"被害者來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _resolve_columns(records):
    """依資料集頁面的語意別名解析 API 的無語意欄名。"""
    keys = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)

    aliases = {
        "year": ("field1", "年", "year"),
        "total_male": ("item value2", "總計男", "total_male"),
        "total_female": ("item value3", "總計女", "total_female"),
    }
    bands = (
        ("0_5歲", "0_5歲男", "0_5歲女", "item value4", "item value5"),
        ("6_11歲", "6_11歲男", "6_11歲女", "item value6", "item value7"),
        ("12_17歲", "12_17歲男", "12_17歲女", "item value8", "item value9"),
        ("18_23歲", "18_23歲男", "18_23歲女", "item value10", "item value11"),
        ("24_29歲", "24_29歲男", "24_29歲女", "item value12", "item value13"),
        ("30_39歲", "30_39歲男", "30_39歲女", "item value14", "item value15"),
        ("40_49歲", "40_49歲男", "40_49歲女", "item value16", "item value17"),
        ("50_59歲", "50_59歲男", "50_59歲女", "item value18", "item value19"),
        ("60_64歲", "60_64歲男", "60_64歲女", "item value20", "item value21"),
        ("65_69歲", "65_69歲男", "65_69歲女", "item value22", "item value23"),
        ("70歲以上", "70歲以上男", "70歲以上女", "item value24", "item value25"),
        ("不詳", "不詳男", "不詳女", "item value26", "item value27"),
    )
    for raw_label, male_label, female_label, male_column, female_column in bands:
        aliases[f"{raw_label}_male"] = (male_column, male_label)
        aliases[f"{raw_label}_female"] = (female_column, female_label)

    resolved = {}
    used = {}
    for role, role_aliases in aliases.items():
        matches = [
            key for key in keys
            if any(_norm(key) == _norm(alias) for alias in role_aliases)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"被害者來源語意欄位 {role} 找不到唯一欄名；"
                f"候選={role_aliases}、實際={keys}"
            )
        actual = matches[0]
        if actual in used:
            raise ValueError(
                f"被害者來源欄位 {actual!r} 同時對應 "
                f"{used[actual]} 與 {role}"
            )
        resolved[role] = actual
        used[actual] = role
    for row_number, record in enumerate(records, start=1):
        missing = [key for key in resolved.values() if key not in record]
        if missing:
            raise ValueError(f"被害者來源第 {row_number} 列缺少欄位：{missing}")
    return resolved


def fetch_records():
    """從 page=0 分頁抓取至空頁；API 回應須為 JSON 陣列。"""
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
                    f"被害者來源第 {page} 頁不是 JSON 陣列："
                    f"{type(page_records).__name__}"
                )
            print(f"crime victim page {page}: {len(page_records)} rows")
            if not page_records:
                return records
            records.extend(page_records)
    raise RuntimeError("被害者來源分頁超過 100 頁，疑似 API 行為改變。")


def _year(record, columns):
    raw = _text(record[columns["year"]], "year")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"被害者來源 year 不是整數：{raw!r}") from exc
    if value < 1900 or value > 2200:
        raise ValueError(f"被害者來源 year 超出西元範圍：{value}")
    return value


def _year_totals(records, columns):
    totals = {}
    for record in records:
        year = _year(record, columns)
        if year in totals:
            raise ValueError(f"被害者來源年度 {year} 出現重複資料列")
        totals[year] = (
            _number(record[columns["total_male"]], "total_male")
            + _number(record[columns["total_female"]], "total_female")
        )
    return totals


def drop_scope_anomalies(records):
    """排除已確認空年度，未知的三倍尺度異常直接拋錯。"""
    if not records:
        raise RuntimeError("被害者來源沒有任何資料列")
    columns = _resolve_columns(records)
    totals = _year_totals(records, columns)
    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if year in totals:
            record = next(row for row in records if _year(row, columns) == year)
            numeric_roles = [
                role for role in columns if role != "year"
            ]
            if any(_number(record[columns[role]], role) != 0 for role in numeric_roles):
                raise ValueError(
                    f"已知空年度民國 {year} 的來源值不再全為 0；"
                    "請重新查證後再更新 KNOWN_BAD_YEARS"
                )
            print(f"crime victim scope guard: drop {year} ({reason})")

    kept_totals = {
        year: value for year, value in totals.items()
        if year not in KNOWN_BAD_YEARS
    }
    if len(kept_totals) >= 3:
        for year, value in sorted(kept_totals.items()):
            peers = sorted(
                other for other_year, other in kept_totals.items()
                if other_year != year
            )
            median = peers[len(peers) // 2]
            if median and (
                value > median * SCALE_ANOMALY_FACTOR
                or value * SCALE_ANOMALY_FACTOR < median
            ):
                raise ValueError(
                    f"被害者來源 {year} total={value:.0f} 與其餘年度中位數 "
                    f"{median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                    "請先確認統計範圍，不要靜默入庫。"
                )
    kept = [
        record for record in records
        if _year(record, columns) not in KNOWN_BAD_YEARS
    ]
    print(
        f"crime victim scope guard: keep {len(kept)} / {len(records)} rows; "
        f"years={[ _year(row, columns) for row in kept ]}"
    )
    return kept


def _parse_age(raw_label):
    label = unicodedata.normalize("NFKC", str(raw_label)).strip()
    if label == "總計":
        return None, None, "all_age"
    if label == "不詳":
        return None, None, "unknown_age"
    if re.fullmatch(r"\d{1,3}歲以上", label):
        return int(label[:-3]), None, "age_band"
    match = re.fullmatch(r"(\d{1,3})[_~～至-](\d{1,3})歲", label)
    if not match:
        raise ValueError(f"無法解析被害者來源年齡標籤：{raw_label!r}")
    lower, upper = int(match.group(1)), int(match.group(2))
    if lower > upper:
        raise ValueError(f"被害者來源年齡上下界顛倒：{raw_label!r}")
    return lower, upper, "age_band"


def _indicator_id(age_scope):
    return {
        "age_band": "crime_victim_count",
        "unknown_age": "crime_victim_unknown_age_count",
        "all_age": "crime_victim_all_age_count",
    }[age_scope]


def _data_time(value):
    if value is not None:
        return value
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _reconcile(records, data, columns):
    """逐來源欄位及逐年度性別驗證輸入值等於輸出值。"""
    decoded = data["breakdown"].map(json.loads)
    if len(data) != len(records) * 26:
        raise ValueError(
            f"被害者輸出列數不一致：輸入 {len(records)}、輸出 {len(data)}；"
            "每個年度應輸出 13 個年齡語意欄位 × 2 性別"
        )
    for role in columns:
        if role == "year":
            continue
        expected = sum(
            _number(record[columns[role]], role) for record in records
        )
        mask = decoded.map(
            lambda item: item["source_column"] == columns[role]
        )
        emitted = float(data.loc[mask, "value"].sum())
        if abs(expected - emitted) > 0.5:
            raise ValueError(
                f"被害者來源欄位 {role} 對帳失敗："
                f"輸入 {expected}、輸出 {emitted}"
            )

    bands = [label for label, *_ in (
        ("0_5歲",), ("6_11歲",), ("12_17歲",), ("18_23歲",),
        ("24_29歲",), ("30_39歲",), ("40_49歲",), ("50_59歲",),
        ("60_64歲",), ("65_69歲",), ("70歲以上",), ("不詳",),
    )]
    for record in records:
        year = _year(record, columns)
        for gender, total_role in (("male", "total_male"), ("female", "total_female")):
            expected = _number(record[columns[total_role]], total_role)
            detail_expected = sum(
                _number(record[columns[f"{label}_{gender}"]], f"{label}_{gender}")
                for label in bands
            )
            if abs(expected - detail_expected) > 0.5:
                raise ValueError(
                    f"被害者 {year} {gender} 年齡分布對帳失敗："
                    f"總計 {expected}、分齡 {detail_expected}"
                )
            mask = (
                (data["period_start"] == f"{year}-01-01")
                & (data["gender"] == gender)
                & (decoded.map(lambda item: item["age_scope"] == "age_band"
                               or item["age_scope"] == "unknown_age"))
            )
            emitted = float(data.loc[mask, "value"].sum())
            if abs(expected - emitted) > 0.5:
                raise ValueError(
                    f"被害者 {year} {gender} 輸入／輸出對帳失敗："
                    f"輸入 {expected}、輸出 {emitted}"
                )


def transform_records(records, data_time=None):
    """把資料集欄位轉成契約；DAG 與 verify 共用此函式。"""
    import pandas as pd

    if not records:
        raise RuntimeError("被害者來源沒有任何資料列")
    columns = _resolve_columns(records)
    loaded_at = _data_time(data_time)
    rows = []
    age_specs = [
        ("總計", None, None, "all_age", "total"),
        ("0_5歲", 0, 5, "age_band", "detail"),
        ("6_11歲", 6, 11, "age_band", "detail"),
        ("12_17歲", 12, 17, "age_band", "detail"),
        ("18_23歲", 18, 23, "age_band", "detail"),
        ("24_29歲", 24, 29, "age_band", "detail"),
        ("30_39歲", 30, 39, "age_band", "detail"),
        ("40_49歲", 40, 49, "age_band", "detail"),
        ("50_59歲", 50, 59, "age_band", "detail"),
        ("60_64歲", 60, 64, "age_band", "detail"),
        ("65_69歲", 65, 69, "age_band", "detail"),
        ("70歲以上", 70, None, "age_band", "detail"),
        ("不詳", None, None, "unknown_age", "unknown"),
    ]
    for row_number, record in enumerate(records, start=1):
        year = _year(record, columns)
        for raw_label, lower, upper, age_scope, source_scope in age_specs:
            parsed_lower, parsed_upper, parsed_scope = _parse_age(raw_label)
            if (lower, upper, age_scope) != (parsed_lower, parsed_upper, parsed_scope):
                raise ValueError(f"被害者內部年齡規則錯誤：{raw_label}")
            period_start = f"{year}-01-01"
            period_end = f"{year}-12-31"
            for gender in ("male", "female"):
                role = (
                    f"total_{gender}" if age_scope == "all_age"
                    else f"{raw_label}_{gender}"
                )
                value = _number(record[columns[role]], role)
                youth_status = (
                    "apportioned" if raw_label in {"18_23歲", "24_29歲", "30_39歲"}
                    else "unavailable"
                )
                rows.append(
                    {
                        "indicator_id": _indicator_id(age_scope),
                        "period_start": period_start,
                        "period_end": period_end,
                        "period_type": "year",
                        "age_lower": lower,
                        "age_upper": upper,
                        "age_band_raw": raw_label,
                        "gender": gender,
                        "area_code": "65000",
                        "area_level": "city",
                        "breakdown": json.dumps(
                            {
                                "source_dataset": DATASET_URL,
                                "source_column": columns[role],
                                "source_age_label": raw_label,
                                "age_scope": age_scope,
                                "source_scope": source_scope,
                                "youth_18_35_status": youth_status,
                                "note": "ETL 保留原始年齡組，不輸出 18-35 分攤值",
                            },
                            ensure_ascii=False,
                        ),
                        "value": value,
                        "unit": "人",
                        "value_type": "count",
                        "data_time": loaded_at,
                    }
                )
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(f"被害者事實表欄位不符合契約：{list(data.columns)}")
    _reconcile(records, data, columns)
    for column in (
        "indicator_id", "period_start", "period_end", "period_type", "age_band_raw",
        "gender", "area_code", "area_level", "breakdown", "value", "unit",
        "value_type", "data_time",
    ):
        if data[column].isna().any():
            raise ValueError(f"被害者契約欄位 {column} 出現 NULL")
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )
    from utils.get_time import get_tpe_now_time_str

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
    dag_folder="youth_crime_victim_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
