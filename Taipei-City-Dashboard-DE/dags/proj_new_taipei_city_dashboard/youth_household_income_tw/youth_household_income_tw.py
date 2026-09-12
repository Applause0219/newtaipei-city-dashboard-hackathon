"""主計總處家庭收支調查：經濟戶長年齡別的所得與消費。"""

import html
import io
import json
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

try:
    from operators.common_pipeline import CommonDag
except ModuleNotFoundError:  # 讓獨立 verify 可載入同一套轉換邏輯
    CommonDag = None


CATALOG_URL = "https://data.gov.tw/dataset/108265"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131 Safari/537.36"
)
SOURCE_YEAR = 2024
KNOWN_BAD_YEARS = set()

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

# 原始 CSV 的年齡組；不把它們切成 18--35，也不在 ETL 內分攤。
AGE_SPECS = [
    ("all", None, None, None),
    ("未滿30歲", None, 29, "未滿30歲"),
    ("30至34歲", 30, 34, "30至34歲"),
    ("35至39歲", 35, 39, "35至39歲"),
    ("40至44歲", 40, 44, "40至44歲"),
    ("45至54歲", 45, 54, "45至54歲"),
    ("55至64歲", 55, 64, "55至64歲"),
    ("65歲及以上", 65, None, "65歲及以上"),
]


def _get(url, **kwargs):
    import requests
    import urllib3

    headers = {"User-Agent": UA}
    headers.update(kwargs.pop("headers", {}))
    try:
        response = requests.get(url, headers=headers, timeout=120, **kwargs)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError:
        # ws.dgbas.gov.tw 在部分 Windows 執行環境的憑證鏈不完整；
        # 只在 TLS 驗證失敗時重試，並保留來源 URL。
        urllib3.disable_warnings()
        response = requests.get(
            url, headers=headers, timeout=120, verify=False, **kwargs
        )
        response.raise_for_status()
        return response


def _resource_url(catalog_html):
    candidates = []
    for raw in re.findall(r"https?://[^\"'<>\s]+", catalog_html):
        candidate = html.unescape(raw).replace("\\/", "/").rstrip(",)")
        if re.search(r"\.csv(?:[?#]|$)", candidate, flags=re.IGNORECASE):
            candidates.append(candidate)
    for candidate in candidates:
        if "ws.dgbas.gov.tw" in candidate:
            return candidate
    raise RuntimeError("data.gov.tw 目錄頁找不到主計總處 CSV 資源連結。")


def fetch_source():
    """從資料集頁面解析真正的 CSV 連結，避免固定資源 UUID。"""
    catalog = _get(CATALOG_URL)
    source_url = _resource_url(catalog.text)
    response = _get(source_url)
    frame = pd.read_csv(io.BytesIO(response.content), encoding="utf-8-sig", dtype=str)
    if frame.empty:
        raise RuntimeError("家庭收支 CSV 為空。")
    return frame, source_url


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "-"}:
        raise ValueError(f"家庭收支欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"家庭收支欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"家庭收支欄位 {field} 出現無效數值：{value!r}")
    return number


def _column(columns, metric, age_token=None):
    matches = []
    for column in columns:
        text = str(column)
        if metric not in text or "平均每戶" not in text:
            continue
        if age_token is None:
            if "經濟戶長" not in text:
                matches.append(column)
        elif age_token in text and "經濟戶長" in text:
            matches.append(column)
    if len(matches) != 1:
        raise ValueError(
            f"家庭收支找不到唯一欄位：metric={metric!r}, age={age_token!r}, "
            f"matches={matches!r}"
        )
    return matches[0]


def _scale_guard(data):
    totals = (
        data.groupby(["indicator_id", "period_start"], dropna=False)["value"]
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
        bad = bad[~bad["period_start"].astype(str).str[:4].isin(KNOWN_BAD_YEARS)]
        if not bad.empty:
            raise ValueError(f"家庭收支尺度異常：{indicator} {bad.to_dict('records')}")
        checked += 1
    print(f"scale_guard =========== checked={checked}, periods={totals['period_start'].nunique()}")


def transform(frame, source_url, data_time=None):
    if data_time is None:
        data_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
    columns = list(frame.columns)
    quintile_column = columns[0]
    metric_columns = {}
    for metric, token in (
        ("disposable", "可支配所得"),
        ("consumption", "消費支出"),
    ):
        metric_columns[metric] = {}
        metric_columns[metric]["all"] = _column(columns, token)
        for key, _lo, _hi, age_token in AGE_SPECS[1:]:
            metric_columns[metric][key] = _column(columns, token, age_token)

    rows = []
    expected_cells = 0
    for row_number, record in frame.iterrows():
        quintile = str(record[quintile_column]).strip()
        if not quintile or quintile.lower() in {"nan", "none"}:
            raise ValueError(f"家庭收支第 {row_number + 2} 列缺少所得分位別")
        for metric, indicator_id in (
            ("disposable", "household_disposable_income_mean"),
            ("consumption", "household_consumption_mean"),
        ):
            for age_key, age_lower, age_upper, age_raw in AGE_SPECS:
                field = metric_columns[metric][age_key]
                value = _number(record[field], field)
                expected_cells += 1
                rows.append(
                    {
                        "indicator_id": indicator_id,
                        "period_start": f"{SOURCE_YEAR}-01-01",
                        "period_end": f"{SOURCE_YEAR}-12-31",
                        "period_type": "year",
                        "age_lower": age_lower,
                        "age_upper": age_upper,
                        "age_band_raw": age_raw,
                        "gender": "total",
                        "area_code": "TW",
                        "area_level": "country",
                        "breakdown": json.dumps(
                            {
                                "quintile": quintile,
                                "household_subject": "economic_household_head",
                                "source_year": SOURCE_YEAR,
                                "source_url": source_url,
                            },
                            ensure_ascii=False,
                        ),
                        "value": value,
                        "unit": "元/戶/年",
                        "value_type": "mean",
                        "data_time": data_time,
                    }
                )

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    if len(data) != expected_cells or data.empty:
        raise ValueError(
            f"家庭收支對帳失敗：輸入數值格 {expected_cells}，輸出列 {len(data)}"
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
    frame, source_url = fetch_source()
    data = transform(
        frame,
        source_url=source_url,
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
        dag_folder="youth_household_income_tw",
    )
    dag.create_dag(etl_func=_transfer)
