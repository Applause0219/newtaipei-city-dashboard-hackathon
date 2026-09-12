from airflow import DAG
from operators.common_pipeline import CommonDag


CATALOG_URL = "https://data.gov.tw/dataset/13333"
DOWNLOAD_PREFIX = "https://apiservice.mol.gov.tw/OdService/download/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
)

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

ROLE_ALIASES = {
    "benefit_type": ("給付種類", "給付種別", "benefit_type"),
    "injury_type": ("職業傷害類型", "職業災害類型", "injury_type"),
    "gender_code": ("性別", "gender", "sex"),
    "age_label": ("年齡組別", "年齡別", "age_group", "age"),
    "value": ("給付人次數", "給付人次", "人次", "count", "value"),
}

KNOWN_BAD_YEARS = {}
SCALE_ANOMALY_FACTOR = 3.0


def _normalise_header(value):
    import unicodedata

    return unicodedata.normalize("NFKC", str(value)).strip().replace(" ", "")


def _column_indexes(headers):
    normalised = {_normalise_header(value): index for index, value in enumerate(headers)}
    indexes = {}
    for role, aliases in ROLE_ALIASES.items():
        matches = [normalised[_normalise_header(alias)] for alias in aliases
                   if _normalise_header(alias) in normalised]
        if len(matches) != 1:
            raise ValueError(
                f"職災來源找不到唯一的 {role} 欄位；實際欄位={headers}、"
                f"別名={aliases}、命中={matches}"
            )
        indexes[role] = matches[0]
    return indexes


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"職災來源欄位 {field} 為空")
    return value


def _number(value, field, allow_negative=False):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"職災來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"職災來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number != number or (not allow_negative and number < 0):
        raise ValueError(f"職災來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _source_count(value):
    """回傳 (數值, 原始字串)；-1 是來源的無法提供哨兵，不當成負的給付人次。"""
    raw = _text(value, "value")
    if raw == "-1":
        return None, raw
    return _number(raw, "value"), raw


def _parse_age(label):
    import re
    import unicodedata

    raw = _text(label, "age_label")
    value = unicodedata.normalize("NFKC", raw).replace(" ", "")
    value = value.replace("－", "-").replace("–", "-").replace("～", "~")
    if value in {"年齡不詳", "不詳", "未知"}:
        return None, None, "unknown_age"
    if value == "未滿15歲":
        return None, 14, "age_band"
    match = re.fullmatch(r"(\d{1,3})-(\d{1,3})歲?", value)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"職災來源年齡區間上下界顛倒：{raw!r}")
        return lower, upper, "age_band"
    match = re.fullmatch(r"(\d{1,3})歲以上", value)
    if match:
        return int(match.group(1)), None, "age_band"
    raise ValueError(f"無法解析職災來源年齡標籤：{raw!r}")


def _gender(code):
    value = _text(code, "gender_code")
    mapping = {"1": "male", "2": "female", "男": "male", "女": "female"}
    if value not in mapping:
        raise ValueError(f"無法解析職災來源性別代碼：{code!r}")
    return mapping[value]


def _period_dates(period_label):
    import calendar
    import re

    match = re.fullmatch(r"(\d{3})(0[1-9]|1[0-2])-(\d{3})(0[1-9]|1[0-2])", period_label)
    if not match or match.group(1) != match.group(3):
        raise ValueError(f"無法解析職災來源統計期：{period_label!r}")
    roc_year = int(match.group(1))
    start_month = int(match.group(2))
    end_month = int(match.group(4))
    if (start_month, end_month) not in {(1, 6), (7, 12)}:
        raise ValueError(f"職災來源統計期不是半年期：{period_label!r}")
    year = roc_year + 1911
    start = f"{year:04d}-{start_month:02d}-01"
    last_day = calendar.monthrange(year, end_month)[1]
    end = f"{year:04d}-{end_month:02d}-{last_day:02d}"
    half = "H1" if start_month == 1 else "H2"
    return roc_year, start, end, half


def _catalog_resources(html):
    import re

    pattern = re.compile(
        r'href="(?P<url>https://apiservice\.mol\.gov\.tw/OdService/download/[^" ]+)"'
        r'[^>]*title="CSV下載檔案".*?'
        r'<span[^>]*>(?P<period>\d{5}-\d{5})</span>',
        re.DOTALL,
    )
    resources = {}
    for match in pattern.finditer(html):
        resources[match.group("url")] = match.group("period")
    if not resources:
        raise RuntimeError("職災資料集頁找不到 CSV 資源；目錄 HTML 結構可能已變更。")
    return sorted(resources.items(), key=lambda item: item[1])


def fetch_records():
    """從資料集頁解析每個半年期的 CSV，避免寫死資源 UUID。"""
    import csv
    import io
    import requests

    with requests.Session() as session:
        response = session.get(CATALOG_URL, headers={"User-Agent": UA}, timeout=120)
        response.raise_for_status()
        resources = _catalog_resources(response.text)
        records = []
        for url, period_label in resources:
            response = session.get(url, headers={"User-Agent": UA}, timeout=120)
            response.raise_for_status()
            text = response.content.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text))
            try:
                headers = next(reader)
            except StopIteration as exc:
                raise RuntimeError(f"職災來源 CSV 為空：{url}") from exc
            indexes = _column_indexes(headers)
            row_count = 0
            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(headers):
                    raise ValueError(
                        f"職災來源 {period_label} 第 {row_number} 列欄數不符："
                        f"{len(row)} != {len(headers)}"
                    )
                value, value_raw = _source_count(row[indexes["value"]])
                records.append(
                    {
                        "period_label": period_label,
                        "benefit_type": _text(row[indexes["benefit_type"]], "benefit_type"),
                        "injury_type": _text(row[indexes["injury_type"]], "injury_type"),
                        "gender_code": _text(row[indexes["gender_code"]], "gender_code"),
                        "age_label": _text(row[indexes["age_label"]], "age_label"),
                        "value": value,
                        "value_raw": value_raw,
                    }
                )
                row_count += 1
            print(f"occupational injury source {period_label}: {row_count} rows")
    if not records:
        raise RuntimeError("職災來源沒有任何資料列。")
    return records


def _year_totals(records):
    totals = {}
    for record in records:
        roc_year, _, _, _ = _period_dates(_text(record.get("period_label"), "period_label"))
        value = record.get("value")
        if value is not None:
            totals[roc_year] = totals.get(roc_year, 0.0) + _number(value, "value")
    return totals


def drop_scope_anomalies(records):
    """已知年度先明確排除；未知年度尺度異常直接 fail loud。"""
    totals = _year_totals(records)
    if not totals:
        raise RuntimeError("職災來源找不到年度總數，無法做尺度檢核。")
    kept = {year: value for year, value in totals.items() if year not in KNOWN_BAD_YEARS}
    if len(kept) >= 3:
        ordered = sorted(kept.values())
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        for year, value in sorted(kept.items()):
            if median and (value > median * SCALE_ANOMALY_FACTOR
                           or value * SCALE_ANOMALY_FACTOR < median):
                raise ValueError(
                    f"民國 {year} 年職災給付人次 {value:.0f}，與其餘年度中位數 "
                    f"{median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                    "請確認統計範圍或口徑後再更新 KNOWN_BAD_YEARS。"
                )
    else:
        print("occupational injury scale guard: fewer than 3 years; comparison not applicable")
    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if year in totals:
            print(f"occupational injury scale guard: exclude ROC {year}: {reason}")
    return [
        record for record in records
        if _period_dates(_text(record.get("period_label"), "period_label"))[0]
        not in KNOWN_BAD_YEARS
    ]


def _reconcile(records, data):
    expected = {}
    for record in records:
        period = _text(record.get("period_label"), "period_label")
        value = record.get("value")
        if value is not None:
            expected[period] = expected.get(period, 0.0) + _number(value, "value")
    if len(data) != len(records):
        raise ValueError(f"職災輸出列數不一致：輸入 {len(records)}、輸出 {len(data)}")
    for period, expected_total in sorted(expected.items()):
        emitted = float(data.loc[data["breakdown"].str.contains(period, regex=False), "value"].sum())
        if abs(emitted - expected_total) > 0.5:
            raise ValueError(
                f"職災 {period} 對帳失敗：輸入 {expected_total}、輸出 {emitted}"
            )
        print(f"{period}: input total {int(expected_total)}, output total {int(emitted)}, reconciled")


def transform_records(records, data_time=None):
    """DAG 與 verify 共用的職災 CSV 轉換核心。"""
    import json
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    if not records:
        raise RuntimeError("職災來源轉換前沒有資料列。")
    rows = []
    for record in records:
        period_label = _text(record.get("period_label"), "period_label")
        roc_year, period_start, period_end, half = _period_dates(period_label)
        age_lower, age_upper, age_scope = _parse_age(record.get("age_label"))
        gender = _gender(record.get("gender_code"))
        rows.append(
            {
                "indicator_id": "occupational_injury_benefit_count",
                "period_start": period_start,
                "period_end": period_end,
                "period_type": "half_year",
                "age_lower": age_lower,
                "age_upper": age_upper,
                "age_band_raw": _text(record.get("age_label"), "age_label"),
                "gender": gender,
                "area_code": "TW",
                "area_level": "country",
                "breakdown": json.dumps(
                    {
                        "source_period": period_label,
                        "roc_year": roc_year,
                        "half": half,
                        "benefit_type_code": _text(record.get("benefit_type"), "benefit_type"),
                        "injury_type": _text(record.get("injury_type"), "injury_type"),
                        "age_scope": age_scope,
                        "source_value_raw": _text(record.get("value_raw"), "value_raw"),
                        "source_value_status": (
                            "unavailable_sentinel" if record.get("value") is None else "numeric"
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "value": (
                    None if record.get("value") is None
                    else _number(record.get("value"), "value")
                ),
                "unit": "人次",
                "value_type": "count",
                "data_time": data_time,
            }
        )
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = pd.array(data["age_lower"], dtype="Int16")
    data["age_upper"] = pd.array(data["age_upper"], dtype="Int16")
    if data_time is None:
        data["data_time"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    _reconcile(records, data)
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos")
    dag_id = dag_infos.get("dag_id")
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
    update_lasttime_in_data_to_dataset_info(engine, dag_id, data["data_time"].max())


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_occupational_injury_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
