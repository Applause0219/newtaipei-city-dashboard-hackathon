from airflow import DAG
from operators.common_pipeline import CommonDag


CATALOG_URL = "https://data.gov.tw/dataset/173898"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

DISTRICT_CODES = {
    "板橋區": "65000010", "三重區": "65000020", "中和區": "65000030",
    "永和區": "65000040", "新莊區": "65000050", "新店區": "65000060",
    "樹林區": "65000070", "鶯歌區": "65000080", "三峽區": "65000090",
    "淡水區": "65000100", "汐止區": "65000110", "瑞芳區": "65000120",
    "土城區": "65000130", "蘆洲區": "65000140", "五股區": "65000150",
    "泰山區": "65000160", "林口區": "65000170", "深坑區": "65000180",
    "石碇區": "65000190", "坪林區": "65000200", "三芝區": "65000210",
    "石門區": "65000220", "八里區": "65000230", "平溪區": "65000240",
    "雙溪區": "65000250", "貢寮區": "65000260", "金山區": "65000270",
    "萬里區": "65000280", "烏來區": "65000290",
}

# 目前沒有已確認的壞年度；保留明確的空集合，讓新年度仍受尺度防護。
KNOWN_BAD_YEARS = {}
SCALE_ANOMALY_FACTOR = 3.0

SOURCE_ALIASES = {
    "district": ("district", "行政區", "區別"),
    "lease_date": (
        "rps07_yyymmddroc", "rps07", "租賃日期", "租約起租日", "租賃起日",
    ),
    "rent": (
        "rps22_amountsunitdollars", "rps22", "租金總額元", "租金總額", "總額元",
    ),
}


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"租賃來源欄位 {field} 為空")
    return value


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"租賃來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"租賃來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"租賃來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _find_column(record, role):
    """依角色與別名尋找欄位；來源改名時直接失敗，不默默少資料。"""
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
        f"租賃來源找不到角色 {role}；可用欄位：{list(record)}；"
        f"接受別名：{aliases}"
    )


def _parse_roc_date(value, field):
    import calendar
    import datetime
    import re

    raw = _text(value, field)
    compact = re.sub(r"[./-]", "", raw)
    match = re.fullmatch(r"(\d{2,3})(\d{2})(\d{2})", compact)
    if not match:
        raise ValueError(f"無法解析租賃來源民國日期 {value!r}")
    roc_year, month, day = (int(part) for part in match.groups())
    year = roc_year + 1911
    if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(year, month)[1]:
        raise ValueError(f"租賃來源日期不存在：{value!r}")
    return datetime.date(year, month, day)


def _year_medians(records):
    import statistics

    values = {}
    for record in records:
        date_value = _parse_roc_date(
            record[_find_column(record, "lease_date")], "lease_date"
        )
        rent = _number(record[_find_column(record, "rent")], "rent")
        values.setdefault(date_value.year, []).append(rent)
    return {year: statistics.median(rents) for year, rents in values.items()}


def drop_scope_anomalies(records):
    """排除明確已知壞年度，並對未知三倍尺度異常直接拋錯。"""
    if not records:
        raise RuntimeError("租賃來源沒有任何資料列。")
    medians = _year_medians(records)
    kept_medians = {year: value for year, value in medians.items()
                    if year not in KNOWN_BAD_YEARS}
    for year, value in sorted(kept_medians.items()):
        others = [v for y, v in kept_medians.items() if y != year]
        if len(others) < 1:
            continue
        import statistics

        reference = statistics.median(others)
        if reference and (value > reference * SCALE_ANOMALY_FACTOR
                          or value * SCALE_ANOMALY_FACTOR < reference):
            raise ValueError(
                f"租賃資料西元 {year} 年租金中位數 {value:.2f}，"
                f"與其餘年度中位數 {reference:.2f} 相差超過 "
                f"{SCALE_ANOMALY_FACTOR} 倍；疑似來源範圍或口徑改變。"
            )
    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if year in medians:
            print(f"rental scope guard =========== 排除 {year}：{reason}")
    excluded = set(KNOWN_BAD_YEARS)
    kept = [
        record for record in records
        if _parse_roc_date(
            record[_find_column(record, "lease_date")], "lease_date"
        ).year not in excluded
    ]
    print(
        f"rental scope guard =========== 保留 {len(kept)} / {len(records)} 列；"
        f"年度 {sorted(set(_parse_roc_date(r[_find_column(r, 'lease_date')], 'lease_date').year for r in kept))}"
    )
    return kept


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


def _resolve_download_url(session):
    import html
    import re

    response = _get_with_retries(session, CATALOG_URL, timeout=(20, 45), label="rental catalog")
    page = html.unescape(response.text.replace("\\u002F", "/").replace("\\/", "/"))
    matches = re.findall(
        r"https://data\.ntpc\.gov\.tw/api/datasets/[A-Za-z0-9-]+/csv/file",
        page,
    )
    if not matches:
        raise RuntimeError(
            f"data.gov.tw #{CATALOG_URL.rsplit('/', 1)[-1]} 找不到 data.ntpc CSV 下載連結。"
        )
    return matches[0]


def fetch_records():
    """從 data.gov.tw 目錄頁解析最新 data.ntpc CSV，再以標準 CSV parser 讀取。"""
    import io

    import pandas as pd
    import requests

    with requests.Session() as session:
        url = _resolve_download_url(session)
        response = _get_with_retries(
            session, url, timeout=(30, 60), label="rental download"
        )
    frame = pd.read_csv(
        io.BytesIO(response.content),
        dtype=str,
        encoding="utf-8-sig",
        keep_default_na=False,
    )
    if frame.empty:
        raise RuntimeError("租賃來源 CSV 沒有任何資料列。")
    records = frame.to_dict("records")
    print(f"rental source =========== {url}; rows={len(records)}")
    return records


def _reconcile(records, data):
    import json

    accounted = sum(
        int(json.loads(value)["source_row_count"])
        for value in data["breakdown"]
    )
    if accounted != len(records):
        raise ValueError(
            f"租賃輸出對帳失敗：輸入 {len(records)} 列、輸出 breakdown 對應 "
            f"{accounted} 列"
        )
    if data.empty:
        raise ValueError("租賃來源轉換後沒有輸出資料。")
    print(
        f"rental reconciliation =========== input_rows={len(records)}, "
        f"accounted_rows={accounted}, output_rows={len(data)}, reconciled"
    )


def transform_records(records, data_time=None):
    """把租賃實價原始列轉成事實表；DAG 與 verify 共用此函式。"""
    import calendar
    import datetime
    import json

    import pandas as pd

    if not records:
        raise RuntimeError("租賃來源沒有任何資料列。")

    normalized = []
    for row_number, record in enumerate(records, start=1):
        district_col = _find_column(record, "district")
        date_col = _find_column(record, "lease_date")
        rent_col = _find_column(record, "rent")
        district = _text(record[district_col], "district")
        if district not in DISTRICT_CODES:
            raise ValueError(
                f"租賃來源第 {row_number} 列出現未定義的新北行政區：{district!r}"
            )
        date_value = _parse_roc_date(record[date_col], "lease_date")
        rent = _number(record[rent_col], "rent")
        normalized.append(
            {
                "period_start": date_value.replace(day=1).isoformat(),
                "period_end": date_value.replace(
                    day=calendar.monthrange(date_value.year, date_value.month)[1]
                ).isoformat(),
                "year": date_value.year,
                "district": district,
                "rent": rent,
            }
        )

    frame = pd.DataFrame(normalized)
    frame["district"] = frame["district"].fillna("未填")
    grouped = (
        frame.groupby(["period_start", "period_end", "district"], dropna=False)
        .agg(value=("rent", "median"), source_row_count=("rent", "size"))
        .reset_index()
        .sort_values(["period_start", "district"])
    )

    if data_time is None:
        data_time = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).isoformat()
    rows = []
    for row in grouped.to_dict("records"):
        breakdown = json.dumps(
            {
                "district": row["district"],
                "source_row_count": int(row["source_row_count"]),
                "source_field": "rps22_amountsunitdollars",
                "age_definition": "unavailable",
                "age_unavailable_reason": "租賃實價登錄沒有承租人實際年齡欄位",
                "source_scope": "新北市租賃實價登錄；租金欄位註記為月租金",
            },
            ensure_ascii=False,
        )
        rows.append(
            {
                "indicator_id": "rental_contract_rent_median",
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "period_type": "month",
                "age_lower": None,
                "age_upper": None,
                "age_band_raw": None,
                "gender": "total",
                "area_code": DISTRICT_CODES[row["district"]],
                "area_level": "district",
                "breakdown": breakdown,
                "value": float(row["value"]),
                "unit": "元/月",
                "value_type": "median",
                "data_time": data_time,
            }
        )
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    if data.empty:
        raise RuntimeError("租賃來源轉換後沒有資料列。")
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
    dag_folder="youth_rental_market_ntpc",
)
dag.create_dag(etl_func=_transfer)
