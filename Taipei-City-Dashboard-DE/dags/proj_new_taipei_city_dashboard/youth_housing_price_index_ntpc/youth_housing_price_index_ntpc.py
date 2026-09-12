from airflow import DAG
from operators.common_pipeline import CommonDag


# 不同於已知 E1060 前端死路的版本頁；表 2 附件會隨最新季資料更新。
PIP_PAGE_URL = "https://pip.moi.gov.tw/V3/E/SCRE0106.aspx"
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
KNOWN_BAD_YEARS = {}
SCALE_ANOMALY_FACTOR = 3.0

SOURCE_ALIASES = {
    "roc_year": ("roc_year", "民國年", "year", "年度"),
    "quarter": ("quarter", "季", "季度"),
    "new_taipei_index": ("new_taipei_index", "新北市", "ntpc_index"),
}


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"住宅價格指數來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "-"}:
        raise ValueError(f"住宅價格指數來源欄位 {field} 為空或缺值")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"住宅價格指數來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"住宅價格指數來源欄位 {field} 出現無效數值：{value!r}")
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
        f"住宅價格指數來源找不到角色 {role}；可用欄位：{list(record)}；"
        f"接受別名：{aliases}"
    )


def _parse_year(value):
    raw = _text(value, "roc_year")
    try:
        year = int(float(raw.replace(",", "")))
    except ValueError as exc:
        raise ValueError(f"住宅價格指數來源民國年無法解析：{value!r}") from exc
    return year if year >= 1911 else year + 1911


def _parse_quarter(value):
    raw = _text(value, "quarter")
    import re

    match = re.fullmatch(r"[第]?(\d)[季]?$", raw)
    if not match or not 1 <= int(match.group(1)) <= 4:
        raise ValueError(f"住宅價格指數來源季度無法解析：{value!r}")
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
            f"pdftotext 解析住宅價格指數 PDF 失敗：{result.stderr.strip()}"
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
    """由 PIP 住宅價格指數版本頁解析表 2 官方附件。"""
    import html
    import re
    from urllib.parse import urljoin, unquote

    response = _get_with_retries(
        session, PIP_PAGE_URL, timeout=(20, 45), label="housing price page"
    )
    page = html.unescape(response.text.replace("\\u002F", "/").replace("\\/", "/"))
    candidates = []
    for href in re.findall(r"href=[\"']([^\"']+\.pdf)[\"']", page, re.I):
        url = urljoin(PIP_PAGE_URL, href)
        if "表2" in unquote(url):
            candidates.append(url)
    if not candidates:
        raise RuntimeError(
            "PIP 住宅價格指數版本頁找不到表 2 官方 PDF 附件；"
            "不能猜測住宅價格指數序列。"
        )
    return candidates[0]


def _parse_pdf_records(text):
    import re

    records = []
    number_pattern = re.compile(r"[-+]?\d+(?:\.\d+)?$")
    for line in text.splitlines():
        period_match = re.match(
            r"^\s*(\d{2,3})\s*年\s*第?\s*([1-4])\s*季\s+(.*)$", line
        )
        if not period_match:
            continue
        fields = period_match.group(3).split()
        if len(fields) < 4:
            continue
        if not all(number_pattern.fullmatch(value) for value in fields[:4]):
            continue
        records.append(
            {
                "roc_year": period_match.group(1),
                "quarter": period_match.group(2),
                "nation_index": fields[0],
                # 表 2 欄位順序為：全國、新北市、臺北市、桃園市……。
                "new_taipei_index": fields[1],
                "table_numeric_columns": len(fields),
            }
        )
    if len(records) < 50:
        raise ValueError(
            f"住宅價格指數 PDF 只解析到 {len(records)} 個季度列；"
            "表 2 欄位／版面可能已變更，拒絕產生不完整序列。"
        )
    first = (records[0]["roc_year"], records[0]["quarter"])
    periods = [
        (int(record["roc_year"]), int(record["quarter"]))
        for record in records
    ]
    if first != ("101", "3") or periods != sorted(set(periods)):
        raise ValueError(
            f"住宅價格指數 PDF 序列邊界或排序異常：first={first}, "
            f"last={periods[-1]}；"
            "拒絕把版面錯位當成資料。"
        )
    return records


def _parse_year_medians(records):
    import statistics

    values = {}
    for record in records:
        year = _parse_year(record[_find_column(record, "roc_year")])
        value = _number(record[_find_column(record, "new_taipei_index")], "new_taipei_index")
        values.setdefault(year, []).append(value)
    return {year: statistics.median(items) for year, items in values.items()}


def _check_scale_guard(records):
    medians = _parse_year_medians(records)
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
                f"住宅價格指數西元 {year} 年中位數={value:.4f}，與其餘年度中位數 "
                f"{reference:.4f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                "疑似來源範圍或基期／口徑改變。"
            )


def drop_scope_anomalies(records):
    """對每年度住宅價格指數執行三倍尺度防護。"""
    if not records:
        raise RuntimeError("住宅價格指數來源沒有任何資料列。")
    _check_scale_guard(records)
    kept = [
        record for record in records
        if _parse_year(record[_find_column(record, "roc_year")]) not in KNOWN_BAD_YEARS
    ]
    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if any(_parse_year(r[_find_column(r, "roc_year")]) == year for r in records):
            print(f"housing price scope guard =========== 排除 {year}：{reason}")
    print(
        f"housing price scope guard =========== 保留 {len(kept)} / "
        f"{len(records)} 筆季度列；年度 "
        f"{sorted(set(_parse_year(r[_find_column(r, 'roc_year')]) for r in kept))}"
    )
    return kept


def fetch_records():
    import requests

    with requests.Session() as session:
        pdf_url = _resolve_pdf_url(session)
        response = _get_with_retries(
            session, pdf_url, timeout=(30, 60), label="housing price PDF"
        )
    records = _parse_pdf_records(_extract_pdf_text(response.content))
    print(f"housing price source =========== {pdf_url}; rows={len(records)}")
    return records


def _reconcile(records, data):
    import json

    if len(data) != len(records):
        raise ValueError(
            f"住宅價格指數輸出列數不一致：輸入 {len(records)}、輸出 {len(data)}"
        )
    accounted = sum(
        int(json.loads(value)["source_row_count"])
        for value in data["breakdown"]
    )
    if accounted != len(records):
        raise ValueError(
            f"住宅價格指數輸出對帳失敗：輸入 {len(records)}、"
            f"breakdown 對應 {accounted}"
        )
    print(
        f"housing price reconciliation =========== input_rows={len(records)}, "
        f"output_rows={len(data)}, reconciled"
    )


def transform_records(records, data_time=None):
    """把 PIP 表 2 的新北市季度指數轉成事實表；verify 共用。"""
    import calendar
    import datetime
    import json

    import pandas as pd

    if not records:
        raise RuntimeError("住宅價格指數來源沒有任何資料列。")
    if data_time is None:
        data_time = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).isoformat()
    rows = []
    for source_row_number, record in enumerate(records, start=1):
        year = _parse_year(record[_find_column(record, "roc_year")])
        quarter = _parse_quarter(record[_find_column(record, "quarter")])
        month = (quarter - 1) * 3 + 1
        period_start = datetime.date(year, month, 1)
        period_end = datetime.date(
            year, month + 2, calendar.monthrange(year, month + 2)[1]
        )
        rows.append(
            {
                "indicator_id": "housing_price_index_ntpc",
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
                        "source_field": "新北市",
                        "source_row_count": 1,
                        "age_definition": "unavailable",
                        "age_unavailable_reason": "住宅價格指數沒有購屋者實際年齡欄位",
                        "source_scope": "新北市；PIP 表 2 全國及六都住宅價格指數",
                        "base_period": "民國105年全年=100",
                    },
                    ensure_ascii=False,
                ),
                "value": _number(
                    record[_find_column(record, "new_taipei_index")],
                    "new_taipei_index",
                ),
                "unit": "指數",
                "value_type": "index",
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
    dag_folder="youth_housing_price_index_ntpc",
)
dag.create_dag(etl_func=_transfer)
