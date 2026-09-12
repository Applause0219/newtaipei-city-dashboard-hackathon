import csv
import io
import json
import re
import unicodedata
from collections import defaultdict

from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
CATALOG_URL = "https://data.gov.tw/dataset/24375"
REGULAR_TARGET = "規律運動比例"
REGULAR_CONTENT = "7333規律運動"
SCALE_ANOMALY_FACTOR = 3.0
KNOWN_BAD_YEARS = set()

CONTRACT_COLUMNS = [
    "indicator_id",
    "period_start",
    "period_end",
    "period_type",
    "age_lower",
    "age_upper",
    "age_band_raw",
    "gender",
    "area_code",
    "area_level",
    "breakdown",
    "value",
    "unit",
    "value_type",
    "data_time",
]


def _normalise(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def _find_header(headers, role, expected):
    matches = [
        i for i, header in enumerate(headers)
        if _normalise(header) == expected
    ]
    if len(matches) != 1:
        raise ValueError(
            f"運動 CSV 欄位 {role} 應唯一匹配，實際 {matches}: {headers!r}"
        )
    return matches[0]


def _resolve_columns(headers):
    names = {
        "year": "統計年度",
        "source": "資料來源",
        "target": "分析標的",
        "content": "分析內容",
        "item": "統計項目",
        "item_description": "統計項目說明",
        "value": "分析結果值",
        "unit": "分析結果單位",
    }
    return {
        role: _find_header(headers, role, expected)
        for role, expected in names.items()
    }


def parse_age_label(label):
    raw = _normalise(label)
    if raw in {"整體", "拒答"}:
        return None, None
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})歲", raw)
    if match:
        lower, upper = map(int, match.groups())
        if lower > upper:
            raise ValueError(f"運動調查年齡區間顛倒：{raw!r}")
        return lower, upper
    match = re.fullmatch(r"(\d{1,2})歲及以上", raw)
    if match:
        return int(match.group(1)), None
    match = re.fullmatch(r"(\d{1,2})歲", raw)
    if match:
        age = int(match.group(1))
        return age, age
    raise ValueError(f"運動調查年齡標籤無法解析：{raw!r}")


def _parse_rate(value, label):
    text = _normalise(value).replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise ValueError(f"運動調查百分比無法解析 {label!r}：{value!r}")
    number = float(text)
    if not 0 <= number <= 100:
        raise ValueError(f"運動調查百分比超出 0-100：{label!r}={number}")
    return number


def parse_csv(content):
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    if len(rows) < 2:
        raise ValueError("運動調查 CSV 沒有資料列")

    headers = rows[0]
    columns = _resolve_columns(headers)
    records = []
    seen = set()
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(headers):
            raise ValueError(
                f"運動調查 CSV 第 {row_number} 列有 {len(row)} 欄，預期 {len(headers)}"
            )
        if (
            _normalise(row[columns["target"]]) != REGULAR_TARGET
            or _normalise(row[columns["content"]]) != REGULAR_CONTENT
            or _normalise(row[columns["item"]]) != "年齡"
        ):
            continue
        year_text = _normalise(row[columns["year"]])
        if not re.fullmatch(r"\d{3}", year_text):
            raise ValueError(f"運動調查民國年無法解析：{year_text!r}")
        roc_year = int(year_text)
        raw_category = _normalise(row[columns["item_description"]])
        lower, upper = parse_age_label(raw_category)
        key = (roc_year, raw_category)
        if key in seen:
            raise ValueError(f"運動調查選定指標重複列：{key!r}")
        seen.add(key)
        unit = _normalise(row[columns["unit"]])
        if unit != "百分比":
            raise ValueError(f"運動調查選定指標單位改變：{unit!r}")
        records.append(
            {
                "roc_year": roc_year,
                "source": _normalise(row[columns["source"]]),
                "analysis_content": _normalise(row[columns["content"]]),
                "source_category_raw": raw_category,
                "age_lower": lower,
                "age_upper": upper,
                "value": _parse_rate(
                    row[columns["value"]],
                    f"ROC {roc_year} {raw_category}",
                ),
                "unit": unit,
            }
        )
    if not records:
        raise RuntimeError(
            "運動調查來源沒有找到規律運動比例／7333規律運動／年齡資料"
        )

    categories_by_year = defaultdict(set)
    for record in records:
        categories_by_year[record["roc_year"]].add(record["source_category_raw"])
    category_sets = list(categories_by_year.values())
    if any(category_set != category_sets[0] for category_set in category_sets[1:]):
        raise ValueError("運動調查不同年度的年齡/非年齡分類不一致")
    return sorted(records, key=lambda record: (record["roc_year"], record["source_category_raw"]))


def resolve_csv_url(html):
    candidates = re.findall(r'"contentUrl"\s*:\s*"([^"]+)"', html)
    candidates = [candidate.replace("\\/", "/") for candidate in candidates]
    for candidate in candidates:
        if "ws.sports.gov.tw/" in candidate and candidate.lower().endswith(".csv"):
            return candidate
    raise RuntimeError("運動調查資料集頁面找不到體育署 CSV contentUrl")


def fetch_records():
    import requests

    headers = {"User-Agent": UA}
    catalog = requests.get(CATALOG_URL, headers=headers, timeout=120)
    catalog.raise_for_status()
    source_url = resolve_csv_url(catalog.text)
    response = requests.get(source_url, headers=headers, timeout=120)
    response.raise_for_status()
    return parse_csv(response.content)


def drop_scope_anomalies(records):
    usable = [
        record for record in records
        if record["roc_year"] not in KNOWN_BAD_YEARS
    ]
    values_by_category = defaultdict(dict)
    for record in usable:
        values_by_category[record["source_category_raw"]][record["roc_year"]] = record["value"]
    for category, values in values_by_category.items():
        if len(values) < 3:
            continue
        for year, value in values.items():
            peers = [peer for peer_year, peer in values.items() if peer_year != year]
            median = sorted(peers)[len(peers) // 2]
            if value and median and (
                value / median > SCALE_ANOMALY_FACTOR
                or median / value > SCALE_ANOMALY_FACTOR
            ):
                raise ValueError(
                    f"運動率年度尺度異常 ROC {year} {category}: "
                    f"{value} vs peer median {median}"
                )
    if KNOWN_BAD_YEARS:
        print(f"運動調查排除已知異常年度：{sorted(KNOWN_BAD_YEARS)}")
    return usable


def transform_records(records, data_time):
    import pandas as pd

    records = drop_scope_anomalies(records)
    rows = []
    for record in records:
        ad_year = record["roc_year"] + 1911
        raw_category = record["source_category_raw"]
        is_age = raw_category not in {"整體", "拒答"}
        base = {
            "period_start": f"{ad_year}-01-01",
            "period_end": f"{ad_year}-12-31",
            "period_type": "year",
            "age_lower": record["age_lower"],
            "age_upper": record["age_upper"],
            "age_band_raw": raw_category if is_age else None,
            "gender": "total",
            "area_code": "TW",
            "area_level": "country",
            "unit": "百分比",
            "value_type": "rate",
            "data_time": data_time,
            "indicator_id": "regular_exercise_rate",
        }
        rows.append(
            {
                **base,
                "breakdown": json.dumps(
                    {
                        "analysis_content": record["analysis_content"],
                        "source_category_raw": raw_category,
                        "source_dimension": "age" if is_age else "non_age_category",
                        "source": record["source"],
                        "youth_18_35_status": "unavailable",
                        "youth_18_35_reason": (
                            "來源是百分比且只有 18-24、25-29、30-34、35-39 "
                            "等年齡組，沒有分子/分母，不能把率按人口比例縮放成 18-35"
                        ),
                    },
                    ensure_ascii=False,
                ),
                "value": record["value"],
            }
        )

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = pd.array(data["age_lower"], dtype="Int64")
    data["age_upper"] = pd.array(data["age_upper"], dtype="Int64")
    if len(data) != len(records):
        raise ValueError(
            f"運動調查輸入/輸出列數不一致：{len(records)} -> {len(data)}"
        )
    for record, row in zip(records, data.itertuples(), strict=True):
        expected_age_raw = (
            record["source_category_raw"]
            if record["source_category_raw"] not in {"整體", "拒答"}
            else None
        )
        expected = (
            str(record["roc_year"] + 1911),
            expected_age_raw,
            float(record["value"]),
        )
        actual_age_raw = (
            None if pd.isna(row.age_band_raw) else row.age_band_raw
        )
        actual = (row.period_start[:4], actual_age_raw, float(row.value))
        if actual != expected:
            raise ValueError(
                f"運動調查輸入/輸出逐列對帳失敗：{actual!r} != {expected!r}"
            )
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos") or {}
    data = transform_records(fetch_records(), get_tpe_now_time_str(is_with_tz=True))
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
    dag_folder="youth_sport_behavior_age_tw",
)
dag.create_dag(etl_func=_transfer)
