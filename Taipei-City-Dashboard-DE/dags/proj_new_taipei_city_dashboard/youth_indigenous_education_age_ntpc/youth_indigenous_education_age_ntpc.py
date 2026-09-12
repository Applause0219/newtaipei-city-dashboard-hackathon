"""新北市十五歲以上原住民族人口教育程度／年齡 DAG。"""

import json
import re
import unicodedata

from airflow import DAG
from operators.common_pipeline import CommonDag


API = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "6B47F1A9-35C3-4085-8AF3-EE36D629ACBC/json"
)
DATASET_URL = "https://data.ntpc.gov.tw/datasets/6B47F1A9-35C3-4085-8AF3-EE36D629ACBC"
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
# 目前沒有已確認的壞年度；未知年度的尺度異常仍須 fail loud。
KNOWN_BAD_YEARS = {}

METRIC_DEFINITIONS = (
    ("total_graduated", "總計畢業", ("itemvalue5", "總計畢業", "total_graduated")),
    ("total_incomplete", "總計肄業", ("itemvalue6", "總計肄業", "total_incomplete")),
    ("literate_graduated", "識字者畢業", ("itemvalue7", "識字者畢業", "literate_graduated")),
    ("literate_incomplete", "識字者肄業", ("itemvalue8", "識字者肄業", "literate_incomplete")),
    ("doctorate_graduated", "博士畢業", ("itemvalue9", "博士畢業", "doctorate_graduated")),
    ("doctorate_incomplete", "博士肄業", ("itemvalue10", "博士肄業", "doctorate_incomplete")),
    ("master_graduated", "碩士畢業", ("itemvalue11", "碩士畢業", "master_graduated")),
    ("master_incomplete", "碩士肄業", ("itemvalue12", "碩士肄業", "master_incomplete")),
    (
        "university_graduated",
        "大學_獨立學院_畢業",
        ("itemvalue13", "大學_獨立學院_畢業", "university_graduated"),
    ),
    (
        "university_incomplete",
        "大學_獨立學院_肄業",
        ("itemvalue14", "大學_獨立學院_肄業", "university_incomplete"),
    ),
    (
        "junior_college_2_3_graduated",
        "專科_二_三年制畢業",
        ("itemvalue15", "專科_二_三年制畢業", "junior_college_2_3_graduated"),
    ),
    (
        "junior_college_2_3_incomplete",
        "專科_二_三年制肄業",
        ("itemvalue16", "專科_二_三年制肄業", "junior_college_2_3_incomplete"),
    ),
    (
        "junior_college_5_after_graduated",
        "專科_五年制_後兩年畢業",
        ("itemvalue17", "專科_五年制_後兩年畢業", "junior_college_5_after_graduated"),
    ),
    (
        "junior_college_5_after_incomplete",
        "專科_五年制_後兩年肄業",
        ("itemvalue18", "專科_五年制_後兩年肄業", "junior_college_5_after_incomplete"),
    ),
    (
        "junior_college_5_before_graduated",
        "專科_五年制_前三年畢業",
        ("itemvalue19", "專科_五年制_前三年畢業", "junior_college_5_before_graduated"),
    ),
    (
        "junior_college_5_before_incomplete",
        "專科_五年制_前三年肄業",
        ("itemvalue20", "專科_五年制_前三年肄業", "junior_college_5_before_incomplete"),
    ),
    ("senior_high_graduated", "高中畢業", ("itemvalue21", "高中畢業", "senior_high_graduated")),
    ("senior_high_incomplete", "高中肄業", ("itemvalue22", "高中肄業", "senior_high_incomplete")),
    ("vocational_graduated", "高職畢業", ("itemvalue23", "高職畢業", "vocational_graduated")),
    ("vocational_incomplete", "高職肄業", ("itemvalue24", "高職肄業", "vocational_incomplete")),
    ("junior_high_graduated", "國_初_中畢業", ("itemvalue25", "國_初_中畢業", "junior_high_graduated")),
    ("junior_high_incomplete", "國_初_中肄業", ("itemvalue26", "國_初_中肄業", "junior_high_incomplete")),
    ("vocational_middle_graduated", "初職畢業", ("itemvalue27", "初職畢業", "vocational_middle_graduated")),
    ("vocational_middle_incomplete", "初職肄業", ("itemvalue28", "初職肄業", "vocational_middle_incomplete")),
    ("primary_graduated", "小學畢業", ("itemvalue29", "小學畢業", "primary_graduated")),
    ("primary_incomplete", "小學肄業", ("itemvalue30", "小學肄業", "primary_incomplete")),
    ("self_study_graduated", "自修畢業", ("itemvalue31", "自修畢業", "self_study_graduated")),
    ("illiterate_graduated", "不識字者畢業", ("itemvalue32", "不識字者畢業", "illiterate_graduated")),
)


def _norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).lower()


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"原住民族教育來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"原住民族教育來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(
            f"原住民族教育來源欄位 {field} 無法轉數值：{value!r}"
        ) from exc
    if number < 0 or number != number:
        raise ValueError(
            f"原住民族教育來源欄位 {field} 出現無效數值：{value!r}"
        )
    return number


def _resolve_columns(records):
    """用資料集頁面的主要欄位說明解析欄名，不依賴固定 API 欄名。"""
    keys = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    aliases = {
        "year": ("itemvalue1", "年", "year"),
        "age": ("itemvalue2", "年齡別", "age"),
        "gender": ("itemvalue3", "性別", "gender"),
        "attribute": ("itemvalue4", "屬性", "attribute"),
    }
    for metric_id, label, metric_aliases in METRIC_DEFINITIONS:
        aliases[metric_id] = metric_aliases

    resolved = {}
    used = {}
    for role, role_aliases in aliases.items():
        matches = [
            key for key in keys
            if any(_norm(key) == _norm(alias) for alias in role_aliases)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"原住民族教育來源語意欄位 {role} 找不到唯一欄名；"
                f"候選={role_aliases}、實際={keys}"
            )
        actual = matches[0]
        if actual in used:
            raise ValueError(
                f"原住民族教育來源欄位 {actual!r} 同時對應 "
                f"{used[actual]} 與 {role}"
            )
        resolved[role] = actual
        used[actual] = role
    for row_number, record in enumerate(records, start=1):
        missing = [key for key in resolved.values() if key not in record]
        if missing:
            raise ValueError(f"原住民族教育來源第 {row_number} 列缺少欄位：{missing}")
    return resolved


def fetch_records():
    """從 page=0 分頁抓取至空頁，並帶瀏覽器 User-Agent。"""
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
                    f"原住民族教育來源第 {page} 頁不是 JSON 陣列："
                    f"{type(page_records).__name__}"
                )
            print(f"indigenous education page {page}: {len(page_records)} rows")
            if not page_records:
                return records
            records.extend(page_records)
    raise RuntimeError("原住民族教育來源分頁超過 100 頁，疑似 API 行為改變。")


def _parse_year(value):
    raw = _text(value, "year")
    try:
        year = int(raw)
    except ValueError as exc:
        raise ValueError(f"原住民族教育 year 不是整數：{raw!r}") from exc
    if year < 1900 or year > 2200:
        raise ValueError(f"原住民族教育 year 超出西元範圍：{year}")
    return year


def _parse_age(value):
    label = unicodedata.normalize("NFKC", _text(value, "age"))
    label = re.sub(r"\s+", "", label).replace("～", "~").replace("至", "~")
    match = re.fullmatch(r"(\d{1,3})歲以上", label)
    if match:
        return int(match.group(1)), None
    match = re.fullmatch(r"(\d{1,3})[~_-](\d{1,3})歲", label)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"原住民族教育年齡上下界顛倒：{value!r}")
        return lower, upper
    raise ValueError(f"無法解析原住民族教育年齡標籤：{value!r}")


def _parse_gender(value):
    label = _norm(_text(value, "gender"))
    if label in {_norm("男"), "male"}:
        return "male"
    if label in {_norm("女"), "female"}:
        return "female"
    raise ValueError(f"無法解析原住民族教育性別標籤：{value!r}")


def _parse_attribute(value):
    label = _norm(_text(value, "attribute"))
    mapping = {
        _norm("合計"): "total",
        _norm("平地"): "plain",
        _norm("山地"): "mountain",
    }
    try:
        return mapping[label]
    except KeyError as exc:
        raise ValueError(f"無法解析原住民族教育屬性標籤：{value!r}") from exc


def _scale_totals(records, columns):
    sentinel = {}
    for record in records:
        age = _norm(_text(record[columns["age"]], "age"))
        attribute = _parse_attribute(record[columns["attribute"]])
        if age != _norm("15歲以上") or attribute != "total":
            continue
        year = _parse_year(record[columns["year"]])
        gender = _parse_gender(record[columns["gender"]])
        if year not in sentinel:
            sentinel[year] = {}
        if gender in sentinel[year]:
            raise ValueError(f"原住民族教育尺度檢核年度 {year} 性別重複")
        sentinel[year][gender] = _number(
            record[columns["total_graduated"]], "total_graduated"
        )
    totals = {}
    for year, values in sentinel.items():
        if set(values) != {"male", "female"}:
            raise ValueError(
                f"原住民族教育尺度檢核年度 {year} 缺少 15歲以上合計男女列"
            )
        totals[year] = sum(values.values())
    return totals


def drop_scope_anomalies(records):
    """以 15 歲以上／總計／總計畢業作跨年尺度哨兵。"""
    if not records:
        raise RuntimeError("原住民族教育來源沒有任何資料列")
    columns = _resolve_columns(records)
    totals = _scale_totals(records, columns)
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
                    f"原住民族教育 {year} 哨兵值={value:.0f} 與其餘年度中位數 "
                    f"{median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                    "請先確認來源尺度，不要靜默入庫。"
                )
    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if year in totals:
            print(f"indigenous education scope guard: drop {year} ({reason})")
    kept = [
        record for record in records
        if _parse_year(record[columns["year"]]) not in KNOWN_BAD_YEARS
    ]
    print(
        f"indigenous education scope guard: keep {len(kept)} / {len(records)} rows; "
        f"years={sorted(set(_parse_year(row[columns['year']]) for row in kept))}"
    )
    return kept


def _data_time(value):
    if value is not None:
        return value
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _reconcile(records, data, columns):
    """檢查合計＝平地＋山地，並逐教育指標核對輸入／輸出總和。"""
    import pandas as pd

    decoded = data["breakdown"].map(json.loads)
    if len(data) != len(records) * len(METRIC_DEFINITIONS):
        raise ValueError(
            f"原住民族教育輸出列數不一致：輸入 {len(records)}、輸出 {len(data)}；"
            f"每筆輸入應展開 {len(METRIC_DEFINITIONS)} 個教育指標"
        )

    index = {}
    for row_number, record in enumerate(records, start=1):
        year = _parse_year(record[columns["year"]])
        age = _norm(_text(record[columns["age"]], "age"))
        gender = _parse_gender(record[columns["gender"]])
        attribute = _parse_attribute(record[columns["attribute"]])
        for metric_id, _, _ in METRIC_DEFINITIONS:
            key = (year, age, gender, metric_id, attribute)
            if key in index:
                raise ValueError(f"原住民族教育來源第 {row_number} 列造成重複鍵：{key}")
            index[key] = _number(record[columns[metric_id]], metric_id)

    mismatch_count = 0
    for key in sorted({key[:4] for key in index}):
        total_key = (*key, "total")
        plain_key = (*key, "plain")
        mountain_key = (*key, "mountain")
        missing = [candidate for candidate in (total_key, plain_key, mountain_key) if candidate not in index]
        if missing:
            raise ValueError(f"原住民族教育屬性列不完整：{missing}")
        if abs(index[total_key] - index[plain_key] - index[mountain_key]) > 0.5:
            mismatch_count += 1
    if mismatch_count:
        raise ValueError(f"原住民族教育合計／平地／山地對帳失敗：{mismatch_count} 組")
    print(
        f"indigenous education attribute reconciliation: "
        f"{len(index) // len(METRIC_DEFINITIONS) // 3} source groups, mismatches=0"
    )

    for metric_id, _, _ in METRIC_DEFINITIONS:
        expected = sum(
            _number(record[columns[metric_id]], metric_id) for record in records
        )
        mask = decoded.map(lambda item: item["metric_id"] == metric_id)
        emitted = float(data.loc[mask, "value"].sum())
        if abs(expected - emitted) > 0.5:
            raise ValueError(
                f"原住民族教育 {metric_id} 輸入／輸出對帳失敗："
                f"輸入 {expected}、輸出 {emitted}"
            )
    if not isinstance(data, pd.DataFrame):
        raise TypeError("原住民族教育事實表不是 pandas DataFrame")


def transform_records(records, data_time=None):
    """把 API 原始列轉成契約；DAG 與 verify 共用此函式。"""
    import pandas as pd

    if not records:
        raise RuntimeError("原住民族教育來源沒有任何資料列")
    columns = _resolve_columns(records)
    loaded_at = _data_time(data_time)
    rows = []
    for record in records:
        year = _parse_year(record[columns["year"]])
        age_label = _text(record[columns["age"]], "age")
        age_lower, age_upper = _parse_age(age_label)
        gender = _parse_gender(record[columns["gender"]])
        attribute = _parse_attribute(record[columns["attribute"]])
        for metric_id, metric_label, _ in METRIC_DEFINITIONS:
            rows.append(
                {
                    "indicator_id": "indigenous_education_by_age_count",
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
                            "source_dataset": DATASET_URL,
                            "source_column": columns[metric_id],
                            "metric_id": metric_id,
                            "metric_label": metric_label,
                            "attribute": attribute,
                            "source_age_label": age_label,
                            "age_coverage": "source_band_exact",
                            "youth_18_35_status": "apportioned",
                            "note": (
                                "15-19 與 35-39 跨越 18-35 邊界；"
                                "ETL 只保留原始年齡組，不輸出分攤值"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    "value": _number(record[columns[metric_id]], metric_id),
                    "unit": "人",
                    "value_type": "count",
                    "data_time": loaded_at,
                }
            )
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(f"原住民族教育事實表欄位不符合契約：{list(data.columns)}")
    _reconcile(records, data, columns)
    for column in (
        "indicator_id", "period_start", "period_end", "period_type", "age_band_raw",
        "gender", "area_code", "area_level", "breakdown", "value", "unit",
        "value_type", "data_time",
    ):
        if data[column].isna().any():
            raise ValueError(f"原住民族教育契約欄位 {column} 出現 NULL")
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
    dag_folder="youth_indigenous_education_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
