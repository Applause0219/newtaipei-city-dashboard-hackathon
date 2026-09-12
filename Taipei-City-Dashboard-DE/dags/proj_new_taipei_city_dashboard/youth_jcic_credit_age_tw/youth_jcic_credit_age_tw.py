"""聯徵中心 NPL_1_63：按年齡／性別／授信類別的信用統計。"""

import calendar
import io
import json
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

try:
    from operators.common_pipeline import CommonDag
except ModuleNotFoundError:
    CommonDag = None


SOURCE_URL = (
    "https://www.jcic.org.tw/jcweb/en/services/inquiry/public/files/"
    "NPL_1_63.csv"
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131 Safari/537.36"
)
KNOWN_BAD_YEARS = set()

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]


def _get(url):
    import requests
    import urllib3

    try:
        response = requests.get(url, headers={"User-Agent": UA}, timeout=120)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError:
        urllib3.disable_warnings()
        response = requests.get(
            url, headers={"User-Agent": UA}, timeout=120, verify=False
        )
        response.raise_for_status()
        return response


def fetch_source():
    response = _get(SOURCE_URL)
    frame = pd.read_csv(io.BytesIO(response.content), encoding="utf-8-sig", dtype=str)
    if frame.empty:
        raise RuntimeError("聯徵年齡信貸 CSV 為空。")
    return frame


def _find_col(columns, *tokens):
    matches = [c for c in columns if all(token in str(c) for token in tokens)]
    if len(matches) != 1:
        raise ValueError(f"聯徵年齡信貸找不到唯一欄位 tokens={tokens!r}: {matches!r}")
    return matches[0]


def _find_exact(columns, label):
    matches = [c for c in columns if str(c).strip() == label]
    if len(matches) != 1:
        raise ValueError(f"聯徵年齡信貸找不到唯一欄位 label={label!r}: {matches!r}")
    return matches[0]


def _find_excluding(columns, include_tokens, exclude_tokens):
    matches = [
        c for c in columns
        if all(token in str(c) for token in include_tokens)
        and not any(token in str(c) for token in exclude_tokens)
    ]
    if len(matches) != 1:
        raise ValueError(
            "聯徵年齡信貸找不到唯一欄位 "
            f"include={include_tokens!r}, exclude={exclude_tokens!r}: {matches!r}"
        )
    return matches[0]


def _number(value, field, allow_missing=True):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "-"}:
        if allow_missing:
            return None
        raise ValueError(f"聯徵年齡信貸欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"聯徵年齡信貸欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"聯徵年齡信貸欄位 {field} 出現無效數值：{value!r}")
    return number


def _parse_age(label):
    """解析原始年齡組；未知／其他保留為無年齡，不猜測其範圍。"""
    raw = str(label).strip()
    text = re.sub(r"\s+", "", raw)
    if text in {"其他", "年齡不詳", "不詳", "nan", "None"}:
        return None, None
    match = re.fullmatch(r"(\d+)[歲]?[-至~](\d+)[歲]?", text)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"聯徵年齡組上下界反轉：{raw}")
        return lower, upper
    match = re.fullmatch(r"(\d+)[歲]?以下", text)
    if match:
        return None, int(match.group(1))
    match = re.fullmatch(r"(\d+)[歲]?以上", text)
    if match:
        return int(match.group(1)), None
    raise ValueError(f"聯徵出現未解析的年齡標籤：{raw!r}")


def _gender(code):
    mapping = {"1": "male", "2": "female"}
    value = str(code).strip()
    if value not in mapping:
        raise ValueError(f"聯徵性別代碼未定義：{code!r}")
    return mapping[value]


def _scale_guard(data):
    work = data.copy()
    work["_year"] = work["period_start"].str[:4].astype(int)
    work["_month"] = work["period_start"].str[5:7].astype(int)
    latest_month = work.groupby(["indicator_id", "_year"], dropna=False)["_month"].transform("max")
    snapshots = work[work["_month"] == latest_month]
    totals = (
        snapshots.groupby(["indicator_id", "_year"], dropna=False)["value"]
        .sum()
        .reset_index()
    )
    checked = 0
    for indicator, group in totals.groupby("indicator_id", dropna=False):
        if len(group) < 3:
            continue
        median = group["value"].median()
        if median <= 0:
            continue
        bad = group[
            (group["value"] > median * 3) | (group["value"] < median / 3)
        ]
        bad = bad[~bad["_year"].astype(str).isin(KNOWN_BAD_YEARS)]
        if not bad.empty:
            raise ValueError(f"聯徵年齡信貸尺度異常：{indicator} {bad.to_dict('records')}")
        checked += 1
    print(f"scale_guard =========== checked={checked}, annual_snapshots={len(totals)}")


def transform(frame, source_url=SOURCE_URL, data_time=None):
    if data_time is None:
        data_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
    columns = list(frame.columns)
    year_col = _find_exact(columns, "年")
    month_col = _find_exact(columns, "月")
    gender_col = _find_exact(columns, "性別")
    age_col = _find_exact(columns, "年齡")
    credit_col = _find_exact(columns, "授信類別")
    people_col = _find_exact(columns, "人數")
    amount_col = _find_excluding(columns, ("額度",), ("平均",))
    case_col = _find_exact(columns, "件數")
    average_col = _find_exact(columns, "平均每筆額度")
    balance_col = _find_exact(columns, "餘額")
    overdue_col = _find_exact(columns, "逾期放款比率")

    definitions = [
        ("jcic_credit_borrower_count", people_col, "人", "count"),
        ("jcic_credit_amount", amount_col, "千元", "sum"),
        ("jcic_credit_case_count", case_col, "件", "count"),
        ("jcic_credit_mean_case_amount", average_col, "千元/件", "mean"),
        ("jcic_credit_balance", balance_col, "千元", "sum"),
        ("jcic_overdue_loan_ratio", overdue_col, "ratio", "rate"),
    ]
    rows = []
    expected_cells = 0
    for row_number, record in frame.iterrows():
        year = int(_number(record[year_col], year_col, allow_missing=False))
        month = int(_number(record[month_col], month_col, allow_missing=False))
        if not 1 <= month <= 12:
            raise ValueError(f"聯徵第 {row_number + 2} 列月份無效：{month}")
        start = f"{year:04d}-{month:02d}-01"
        end = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]}"
        raw_age = str(record[age_col]).strip()
        age_lower, age_upper = _parse_age(raw_age)
        gender = _gender(record[gender_col])
        credit_type = str(record[credit_col]).strip()
        if not credit_type or credit_type.lower() in {"nan", "none"}:
            raise ValueError(f"聯徵第 {row_number + 2} 列缺少授信類別")
        for indicator_id, field, unit, value_type in definitions:
            value = _number(record[field], field)
            if value is None:
                continue
            expected_cells += 1
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "period_start": start,
                    "period_end": end,
                    "period_type": "month",
                    "age_lower": age_lower,
                    "age_upper": age_upper,
                    "age_band_raw": raw_age,
                    "gender": gender,
                    "area_code": "TW",
                    "area_level": "country",
                    "breakdown": json.dumps(
                        {
                            "credit_type": credit_type,
                            "gender_code": str(record[gender_col]).strip(),
                            "source_metric_field": str(field),
                            "stock_flow": "stock",
                            "source_url": source_url,
                        },
                        ensure_ascii=False,
                    ),
                    "value": value,
                    "unit": unit,
                    "value_type": value_type,
                    "data_time": data_time,
                }
            )

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    if data.empty or len(data) != expected_cells:
        raise ValueError(
            f"聯徵年齡信貸對帳失敗：輸入有效數值格 {expected_cells}，"
            f"輸出列 {len(data)}"
        )
    data["age_lower"] = pd.Series(data["age_lower"], dtype="Int16")
    data["age_upper"] = pd.Series(data["age_upper"], dtype="Int16")
    _scale_guard(data)
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs["dag_infos"]
    data = transform(
        fetch_source(),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")
    print(data.groupby("indicator_id")["value"].agg(["count", "sum"]))
    engine = create_engine(kwargs["ready_data_db_uri"])
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos.get("load_behavior"),
        default_table=dag_infos.get("ready_data_default_table"),
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_infos["dag_id"], data["data_time"].max()
    )


if CommonDag is not None:
    dag = CommonDag(
        proj_folder="proj_new_taipei_city_dashboard",
        dag_folder="youth_jcic_credit_age_tw",
    )
    dag.create_dag(etl_func=_transfer)
