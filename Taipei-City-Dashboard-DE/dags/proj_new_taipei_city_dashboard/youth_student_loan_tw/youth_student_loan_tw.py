"""聯徵中心公開資料：新增個人就學貸款月資料。"""

import calendar
import io
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

try:
    from operators.common_pipeline import CommonDag
except ModuleNotFoundError:
    CommonDag = None


SOURCE_URL = (
    "https://www.jcic.org.tw/jcweb/en/services/inquiry/public/files/"
    "Stut_New_Amt.CSV"
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
        raise RuntimeError("就學貸款 CSV 為空。")
    return frame


def _find_col(columns, *tokens):
    matches = [c for c in columns if all(token in str(c) for token in tokens)]
    if len(matches) != 1:
        raise ValueError(f"就學貸款找不到唯一欄位 tokens={tokens!r}: {matches!r}")
    return matches[0]


def _find_exact(columns, label):
    matches = [c for c in columns if str(c).strip() == label]
    if len(matches) != 1:
        raise ValueError(f"就學貸款找不到唯一欄位 label={label!r}: {matches!r}")
    return matches[0]


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "-"}:
        raise ValueError(f"就學貸款欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"就學貸款欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"就學貸款欄位 {field} 出現無效數值：{value!r}")
    return number


def _scale_guard(data):
    work = data.copy()
    work["_year"] = work["period_start"].str[:4].astype(int)
    work["_month"] = work["period_start"].str[5:7].astype(int)
    full_years = (
        work.groupby("_year", dropna=False)["_month"].nunique()
        .loc[lambda s: s == 12]
        .index
    )
    totals = (
        work[work["_year"].isin(full_years)]
        .groupby(["indicator_id", "_year"], dropna=False)["value"]
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
            raise ValueError(f"就學貸款尺度異常：{indicator} {bad.to_dict('records')}")
        checked += 1
    print(
        "scale_guard =========== "
        f"checked={checked}, full_years={list(full_years)}; "
        "partial years excluded"
    )


def transform(frame, source_url=SOURCE_URL, data_time=None):
    if data_time is None:
        data_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
    columns = list(frame.columns)
    year_col = _find_exact(columns, "年")
    month_col = _find_exact(columns, "月")
    count_col = _find_col(columns, "新增", "學貸", "總人數")
    amount_col = _find_col(columns, "新增", "學貸", "總金額")
    average_col = _find_col(columns, "新增", "學貸", "平均金額")

    definitions = [
        ("student_loan_applicant_count", count_col, "人", "count"),
        # 總金額是可加總的觀測量；保留 sum 以免誤標成 mean。
        ("student_loan_amount", amount_col, "千元", "sum"),
        ("student_loan_mean_amount", average_col, "千元/人", "mean"),
    ]
    rows = []
    expected_cells = 0
    seen_periods = set()
    for row_number, record in frame.iterrows():
        year = int(_number(record[year_col], year_col))
        month = int(_number(record[month_col], month_col))
        if not 1 <= month <= 12:
            raise ValueError(f"就學貸款第 {row_number + 2} 列月份無效：{month}")
        start = f"{year:04d}-{month:02d}-01"
        end = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]}"
        seen_periods.add(start)
        for indicator_id, field, unit, value_type in definitions:
            value = _number(record[field], field)
            expected_cells += 1
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "period_start": start,
                    "period_end": end,
                    "period_type": "month",
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "gender": "total",
                    "area_code": "TW",
                    "area_level": "country",
                    "breakdown": json.dumps(
                        {
                            "loan_scope": "all_student_loans",
                            "period_basis": "calendar_month",
                            "source_field": str(field),
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
            f"就學貸款對帳失敗：來源期數 {len(seen_periods)}，"
            f"輸入數值格 {expected_cells}，輸出列 {len(data)}"
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
        dag_folder="youth_student_loan_tw",
    )
    dag.create_dag(etl_func=_transfer)
