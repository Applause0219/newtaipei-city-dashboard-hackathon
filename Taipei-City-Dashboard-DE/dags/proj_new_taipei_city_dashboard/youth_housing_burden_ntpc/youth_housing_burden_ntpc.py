from airflow import DAG
from operators.common_pipeline import CommonDag


# 這是不同於已知 E1050 前端死路的版本頁；DAG 從頁面解析最新 PDF 附件。
PIP_PAGE_URL = "https://pip.moi.gov.tw/V3/E/SCRE0105.aspx"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131 Safari/537.36"
)

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

AREA_CODE = "65000"
# 本次及已驗證附件沒有確認的壞年度；未知尺度異常仍須拋錯。
KNOWN_BAD_YEARS = {}
SCALE_ANOMALY_FACTOR = 3.0

SOURCE_ALIASES = {
    "roc_year": ("roc_year", "民國年", "year", "年度"),
    "quarter": ("quarter", "季", "季度"),
    "area": ("area", "縣市", "地區"),
    "burden_rate": (
        "housing_burden_rate", "房貸負擔率(%)", "房貸負擔率",
    ),
    "price_income": (
        "housing_price_income_ratio", "房價所得比(倍)", "房價所得比",
    ),
}


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"房價負擔來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "-"}:
        raise ValueError(f"房價負擔來源欄位 {field} 為空或缺值")
    try:
        number = float(raw.replace(",", "").replace("%", ""))
    except ValueError as exc:
        raise ValueError(f"房價負擔來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"房價負擔來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _find_column(record, role):
    aliases = SOURCE_ALIASES[role]
    normalized = {
        "".join(ch for ch in str(key).strip().lower() if ch.isalnum()): key
        for key in record
    }
    for alias in aliases:
        key = normalized.get(
            "".join(ch for ch in alias.strip().lower() if ch.isalnum())
        )
        if key is not None:
            return key
    raise ValueError(
        f"房價負擔來源找不到角色 {role}；可用欄位：{list(record)}；"
        f"接受別名：{aliases}"
    )


def _parse_year(value):
    raw = _text(value, "roc_year")
    try:
        year = int(float(raw.replace(",", "")))
    except ValueError as exc:
        raise ValueError(f"房價負擔來源民國年無法解析：{value!r}") from exc
    return year if year >= 1911 else year + 1911


def _parse_quarter(value):
    raw = _text(value, "quarter")
    match = __import__("re").search(r"([1-4])", raw)
    if not match:
        raise ValueError(f"房價負擔來源季度無法解析：{value!r}")
    return int(match.group(1))


def _extract_pdf_text(content):
    import os
    import shutil
    import subprocess
    import tempfile

    executable = shutil.which("pdftotext") or shutil.which("pdftotext.exe")
    if not executable:
        raise RuntimeError(
            "找不到 pdftotext；本 DAG 需要在 Airflow 映像安裝 Poppler/pdftotext "
            "才能解析官方 PDF 附件。"
        )
    file_descriptor, file_path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(file_descriptor, "wb") as pdf_file:
            pdf_file.write(content)
        result = subprocess.run(
            [executable, "-layout", file_path, "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    finally:
        os.unlink(file_path)
    if result.returncode != 0:
        raise RuntimeError(
            f"pdftotext 解析房價負擔 PDF 失敗：{result.stderr.strip()}"
        )
    return result.stdout


def _get_with_retries(session, url, timeout, label):
    import time

    import requests

    for attempt in range(1, 4):
        try:
            response = session.get(
                url,
                headers={"User-Agent": UA},
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except (requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            if attempt == 3:
                raise
            print(
                f"{label} retry =========== attempt={attempt}; "
                f"reason={type(exc).__name__}"
            )
            time.sleep(2)
    raise RuntimeError(f"{label} 沒有取得 HTTP response。")


def _resolve_pdf_url(session):
    """由 PIP 版本頁的相關檔案連結選最新房價負擔 PDF。"""
    import html
    import re
    from urllib.parse import unquote, urljoin, urlparse

    response = _get_with_retries(
        session, PIP_PAGE_URL, timeout=(20, 45), label="housing burden page"
    )
    page = html.unescape(response.text.replace("\\u002F", "/").replace("\\/", "/"))
    candidates = []
    for href in re.findall(r"href=[\"']([^\"']+\.pdf)[\"']", page, re.I):
        url = urljoin(PIP_PAGE_URL, href)
        filename = unquote(urlparse(url).path)
        match = re.search(r"/(\d{2,3})Q([1-4])房價負擔能力.*\.pdf$", filename)
        if match:
            candidates.append((int(match.group(1)), int(match.group(2)), url))
    if not candidates:
        raise RuntimeError(
            "PIP 房價負擔版本頁找不到帶民國年／季度的官方 PDF 附件；"
            "不能猜測最新期別。"
        )
    _, _, url = max(candidates)
    return url


def _parse_pdf_records(text):
    import re

    first_page = text.split("\f", 1)[0]
    period_match = re.search(
        r"(?m)^\s*(\d{2,3})\s*年\s*第?\s*([1-4])\s*季\b",
        first_page,
    )
    if not period_match:
        raise ValueError("房價負擔 PDF 找不到報告民國年／季度，來源版面可能已變更。")
    bullet_match = re.search(
        r"(?ms)^\s*1\.\s*新北市.*?房貸負擔率\s*(?:為|：|:)\s*"
        r"([-+]?\d+(?:\.\d+)?)\s*%.*?房價所得比.*?"
        r"(?:為|：|:)\s*([-+]?\d+(?:\.\d+)?)\s*倍",
        first_page,
    )
    if not bullet_match:
        raise ValueError(
            "房價負擔 PDF 找不到官方六都說明中的第 1 筆數值；"
            "不能在沒有欄位對應證據時猜測新北市。"
        )
    roc_year, quarter = period_match.groups()
    return [
        {
            "roc_year": roc_year,
            "quarter": quarter,
            # 官方第一筆六都說明固定為新北市；數值位置由 PDF 版面驗證。
            "area": "新北市",
            "housing_burden_rate": bullet_match.group(1),
            "housing_price_income_ratio": bullet_match.group(2),
        }
    ]


def _parse_year_medians(records, role):
    import statistics

    values = {}
    for record in records:
        year = _parse_year(record[_find_column(record, "roc_year")])
        value = _number(record[_find_column(record, role)], role)
        values.setdefault(year, []).append(value)
    return {year: statistics.median(items) for year, items in values.items()}


def _check_scale_guard(records, role):
    medians = _parse_year_medians(records, role)
    kept = {year: value for year, value in medians.items()
            if year not in KNOWN_BAD_YEARS}
    for year, value in sorted(kept.items()):
        others = [v for y, v in kept.items() if y != year]
        if not others:
            continue
        import statistics

        reference = statistics.median(others)
        if reference and (value > reference * SCALE_ANOMALY_FACTOR
                          or value * SCALE_ANOMALY_FACTOR < reference):
            raise ValueError(
                f"房價負擔資料西元 {year} 年 {role}={value:.4f}，"
                f"與其餘年度中位數 {reference:.4f} 相差超過 "
                f"{SCALE_ANOMALY_FACTOR} 倍；疑似統計口徑改變。"
            )


def drop_scope_anomalies(records):
    """檢查年度尺度；來源目前是單季快照，未來接成序列仍適用。"""
    if not records:
        raise RuntimeError("房價負擔來源沒有任何資料列。")
    for record in records:
        if _text(record[_find_column(record, "area")], "area") != "新北市":
            raise ValueError("房價負擔 PDF 解析出非新北市列，拒絕錯置行政區。")
    for role in ("burden_rate", "price_income"):
        _check_scale_guard(records, role)
    kept = [
        record for record in records
        if _parse_year(record[_find_column(record, "roc_year")]) not in KNOWN_BAD_YEARS
    ]
    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if any(_parse_year(r[_find_column(r, "roc_year")]) == year for r in records):
            print(f"housing burden scope guard =========== 排除 {year}：{reason}")
    print(
        f"housing burden scope guard =========== 保留 {len(kept)} / "
        f"{len(records)} 筆；年度 "
        f"{sorted(set(_parse_year(r[_find_column(r, 'roc_year')]) for r in kept))}"
    )
    return kept


def fetch_records():
    import requests

    with requests.Session() as session:
        pdf_url = _resolve_pdf_url(session)
        response = _get_with_retries(
            session, pdf_url, timeout=(30, 60), label="housing burden PDF"
        )
    records = _parse_pdf_records(_extract_pdf_text(response.content))
    print(f"housing burden source =========== {pdf_url}; rows={len(records)}")
    return records


def _reconcile(records, data):
    import json

    if len(data) != len(records) * 2:
        raise ValueError(
            f"房價負擔輸出列數不一致：輸入 {len(records)}、輸出 {len(data)}；"
            "每個來源列應輸出房貸負擔率與房價所得比。"
        )
    accounted = sum(
        int(json.loads(value)["source_row_count"])
        for value in data["breakdown"]
    )
    if accounted != len(records) * 2:
        raise ValueError(
            f"房價負擔輸出對帳失敗：輸入指標列 {len(records) * 2}、"
            f"breakdown 對應 {accounted}"
        )
    for indicator_id in ("housing_burden_rate", "housing_price_income_ratio"):
        if (data["indicator_id"] == indicator_id).sum() != len(records):
            raise ValueError(f"房價負擔指標 {indicator_id} 對帳失敗。")
    print(
        f"housing burden reconciliation =========== source_rows={len(records)}, "
        f"output_rows={len(data)}, reconciled"
    )


def transform_records(records, data_time=None):
    """把 PIP 房價負擔 PDF 的新北市列轉成事實表；verify 共用。"""
    import calendar
    import datetime
    import json

    import pandas as pd

    if not records:
        raise RuntimeError("房價負擔來源沒有任何資料列。")
    if data_time is None:
        data_time = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).isoformat()
    rows = []
    definitions = (
        ("housing_burden_rate", "burden_rate", "%", "rate", "房貸負擔率(%)"),
        (
            "housing_price_income_ratio", "price_income", "倍", "index",
            "房價所得比(倍)",
        ),
    )
    for source_row_number, record in enumerate(records, start=1):
        if _text(record[_find_column(record, "area")], "area") != "新北市":
            raise ValueError("房價負擔輸出範圍出現非新北市列。")
        year = _parse_year(record[_find_column(record, "roc_year")])
        quarter = _parse_quarter(record[_find_column(record, "quarter")])
        month = (quarter - 1) * 3 + 1
        period_start = datetime.date(year, month, 1)
        period_end = datetime.date(
            year, month + 2, calendar.monthrange(year, month + 2)[1]
        )
        for indicator_id, role, unit, value_type, source_field in definitions:
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "period_type": "quarter",
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "gender": "total",
                    "area_code": AREA_CODE,
                    "area_level": "city",
                    "breakdown": json.dumps(
                        {
                            "source_row_number": source_row_number,
                            "source_field": source_field,
                            "source_row_count": 1,
                            "age_definition": "unavailable",
                            "age_unavailable_reason": "房價負擔是中位數家戶／城市統計，沒有青年個人年齡欄位",
                            "source_scope": "新北市；PIP 115Q1 房價負擔能力官方附件",
                            "loan_assumption": "20年期、貸款七成、五大銀行購屋貸款利率",
                        },
                        ensure_ascii=False,
                    ),
                    "value": _number(record[_find_column(record, role)], role),
                    "unit": unit,
                    "value_type": value_type,
                    "data_time": data_time,
                }
            )
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
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
    dag_id = dag_infos.get("dag_id")
    load_behavior = dag_infos.get("load_behavior")
    default_table = dag_infos.get("ready_data_default_table")
    data = transform_records(
        drop_scope_anomalies(fetch_records()),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")
    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine, data=data, load_behavior=load_behavior, default_table=default_table
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_id, data["data_time"].max()
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_housing_burden_ntpc",
)
dag.create_dag(etl_func=_transfer)
