"""MOL initial-salary facts, split by education and characteristic.

The pure fetch/transform functions in this module are intentionally usable by
the companion verify script without importing Airflow.
"""

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from operators.common_pipeline import CommonDag
except ImportError:  # pragma: no cover - allows local verification without Airflow
    CommonDag = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MEAN_URL = "https://apiservice.mol.gov.tw/OdService/download/A17000000J-020066-J3o"
MEDIAN_URL = "https://apiservice.mol.gov.tw/OdService/download/A17000000J-020066-aj4"
AREA_CODE = "TW"
AREA_LEVEL = "country"

CONTRACT_COLUMNS = (
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
)

FIELD_ALIASES = {
    "year": ("年度",),
    "education": ("教育程度別",),
    "characteristic": ("特性別",),
    "share": (
        "占該教育程度別初任人員人數結構比（%）",
        "占該教育程度別初任人員人數結構比(%)",
        "占該教育程度別初任人員人數結構比（％）",
    ),
    "mean": ("薪資平均數（千元）", "薪資平均數(千元)"),
    "d1": ("薪資D1（千元）", "薪資D1(千元)"),
    "q1": ("薪資Q1（千元）", "薪資Q1(千元)"),
    "median": ("薪資中位數（千元）", "薪資中位數(千元)"),
    "q3": ("薪資Q3（千元）", "薪資Q3(千元)"),
    "d9": ("薪資D9（千元）", "薪資D9(千元)"),
}
MISSING = {"", "-", "－", "—", "–", "…", "..."}


def _normalise_key(value):
    return (
        str(value)
        .strip()
        .replace("（", "(")
        .replace("）", ")")
        .replace("％", "%")
        .replace("　", "")
        .replace(" ", "")
    )


def _find_key(record, role):
    aliases = {_normalise_key(x) for x in FIELD_ALIASES[role]}
    for key in record:
        if _normalise_key(key) in aliases:
            return key
    raise KeyError(
        f"MOL 初任薪資找不到角色欄位 {role!r}；實際欄位={list(record)}"
    )


def _parse_number(value, *, role, row_number):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in MISSING:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"初任薪資第 {row_number} 列 {role} 數值無法解析：{value!r}"
        ) from exc


def _as_json_number(value):
    return None if value is None else float(value)


def _roc_year_to_ad(value):
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"\d{3,4}", text):
        raise ValueError(f"年度不是可解析的民國年：{value!r}")
    year = int(text)
    if 100 <= year <= 999:
        return year + 1911
    if 1900 <= year <= 2500:
        return year
    raise ValueError(f"年度超出合理範圍：{value!r}")


def _period_fields(year):
    ad_year = _roc_year_to_ad(year)
    return f"{ad_year:04d}-01-01", f"{ad_year:04d}-12-31"


def _gender_of(characteristic):
    text = str(characteristic).strip()
    if "性別" in text and ("男性" in text or text.endswith("男")):
        return "male"
    if "性別" in text and ("女性" in text or text.endswith("女")):
        return "female"
    return "total"


def _http_bytes(url, *, timeout=180):
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"來源請求失敗 {url}: {exc}") from exc


def _fetch_json(url):
    payload = json.loads(_http_bytes(url).decode("utf-8-sig"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("records"), list):
            return result["records"]
        for key in ("records", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError(f"初任薪資 JSON 結構無法辨識：{type(payload).__name__}")


def _fact_base(year, data_time):
    period_start, period_end = _period_fields(year)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "period_type": "year",
        "age_lower": None,
        "age_upper": None,
        "age_band_raw": None,
        "area_code": AREA_CODE,
        "area_level": AREA_LEVEL,
        "data_time": data_time,
    }


def transform_records(mean_records, median_records, *, data_time):
    rows = []
    input_numeric = 0
    emitted_numeric = 0
    input_sum = Decimal("0")
    emitted_sum = Decimal("0")
    selected_counts = {"mean": 0, "share": 0, "median": 0}

    for row_number, record in enumerate(mean_records, 1):
        year_key = _find_key(record, "year")
        education_key = _find_key(record, "education")
        characteristic_key = _find_key(record, "characteristic")
        share_key = _find_key(record, "share")
        mean_key = _find_key(record, "mean")
        year = record[year_key]
        education = str(record[education_key]).strip()
        characteristic = str(record[characteristic_key]).strip()
        if not education or not characteristic:
            raise ValueError(f"初任薪資第 {row_number} 列教育／特性空白")
        base = _fact_base(year, data_time)
        gender = _gender_of(characteristic)
        breakdown = {
            "education_level": education,
            "characteristic": characteristic,
            "source": "MOL OAS A17000000J-020066",
        }

        share = _parse_number(record[share_key], role="share", row_number=row_number)
        mean = _parse_number(record[mean_key], role="mean", row_number=row_number)
        if share is not None:
            input_numeric += 1
            input_sum += share
            selected_counts["share"] += 1
            rows.append(
                dict(
                    base,
                    indicator_id="youth_salary_initial_share_by_education",
                    gender=gender,
                    breakdown=json.dumps(
                        dict(breakdown, source_field=share_key),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    value=float(share),
                    unit="%",
                    value_type="rate",
                )
            )
            emitted_numeric += 1
            emitted_sum += share
        if mean is not None:
            input_numeric += 1
            input_sum += mean
            selected_counts["mean"] += 1
            rows.append(
                dict(
                    base,
                    indicator_id="youth_salary_initial_mean_by_education",
                    gender=gender,
                    breakdown=json.dumps(
                        dict(breakdown, source_field=mean_key),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    # 來源單位是千元，換算成元。
                    # youth_salary_initial_history_pdf（民國110-113）用的是元，
                    # 兩支共用同一組 indicator_id，單位不一致會讓串接的序列
                    # 在 2024/2025 之間出現千倍斷層。
                    value=float(mean) * 1000,
                    unit="元",
                    value_type="mean",
                )
            )
            emitted_numeric += 1
            emitted_sum += mean

    for row_number, record in enumerate(median_records, 1):
        year_key = _find_key(record, "year")
        education_key = _find_key(record, "education")
        characteristic_key = _find_key(record, "characteristic")
        median_key = _find_key(record, "median")
        year = record[year_key]
        education = str(record[education_key]).strip()
        characteristic = str(record[characteristic_key]).strip()
        if not education or not characteristic:
            raise ValueError(f"初任薪資分布第 {row_number} 列教育／特性空白")
        median = _parse_number(
            record[median_key], role="median", row_number=row_number
        )
        # D1/Q1/Q3/D9 沒有對應的 value_type；只輸出中位數，並把其餘
        # 原始分位數保留在 breakdown，避免把分位數誤標成 median。
        if median is None:
            continue
        quantiles = {}
        for role in ("d1", "q1", "q3", "d9"):
            key = _find_key(record, role)
            quantiles[role] = _as_json_number(
                _parse_number(record[key], role=role, row_number=row_number)
            )
        input_numeric += 1
        input_sum += median
        emitted_numeric += 1
        emitted_sum += median
        selected_counts["median"] += 1
        base = _fact_base(year, data_time)
        rows.append(
            dict(
                base,
                indicator_id="youth_salary_initial_median_by_education",
                gender=_gender_of(characteristic),
                breakdown=json.dumps(
                    {
                        "education_level": education,
                        "characteristic": characteristic,
                        "source": "MOL OAS A17000000J-020066",
                        "source_field": median_key,
                        "other_quantiles": quantiles,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                # 同上：千元換算成元，與歷年 PDF DAG 對齊。
                value=float(median) * 1000,
                unit="元",
                value_type="median",
            )
        )

    if input_numeric != emitted_numeric or input_sum != emitted_sum:
        raise ValueError(
            "初任薪資對帳失敗："
            f"輸入選定數值 {input_numeric}/{input_sum}，"
            f"輸出 {emitted_numeric}/{emitted_sum}"
        )
    if not rows:
        raise RuntimeError("初任薪資沒有產出任何資料列")
    return rows, {
        "input_numeric_cells": input_numeric,
        "emitted_rows": emitted_numeric,
        "input_value_sum": str(input_sum),
        "emitted_value_sum": str(emitted_sum),
        "selected_counts": selected_counts,
        "mean_source_records": len(mean_records),
        "median_source_records": len(median_records),
    }


def validate_contract(rows):
    allowed_types = {"count", "rate", "median", "mean", "index"}
    for row_number, row in enumerate(rows, 1):
        missing = [column for column in CONTRACT_COLUMNS if column not in row]
        if missing:
            raise ValueError(f"契約第 {row_number} 列缺欄位：{missing}")
        extra = [column for column in row if column not in CONTRACT_COLUMNS]
        if extra:
            raise ValueError(f"契約第 {row_number} 列多出欄位：{extra}")
        if row["value"] is None or row["value_type"] not in allowed_types:
            raise ValueError(f"契約第 {row_number} 列 value/value_type 無效：{row}")
        if row["age_lower"] is not None or row["age_upper"] is not None:
            raise ValueError("薪資來源無年齡欄位，不得產生年齡值")
        json.loads(row["breakdown"])
    return True


def fetch_and_transform(*, data_time=None):
    if data_time is None:
        data_time = datetime.now().astimezone().isoformat()
    mean_records = _fetch_json(MEAN_URL)
    median_records = _fetch_json(MEDIAN_URL)
    return transform_records(mean_records, median_records, data_time=data_time)


def _transfer(**kwargs):
    import pandas as pd
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
    data_time = get_tpe_now_time_str(is_with_tz=True)
    rows, diagnostics = fetch_and_transform(data_time=data_time)
    validate_contract(rows)
    data = pd.DataFrame(rows, columns=list(CONTRACT_COLUMNS))
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    print(f"salary diagnostics =========== {diagnostics}")
    print(f"ready_data shape =========== {data.shape}")

    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=load_behavior,
        default_table=default_table,
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_id, data["data_time"].max()
    )


if CommonDag is not None:
    dag = CommonDag(
        proj_folder="proj_new_taipei_city_dashboard",
        dag_folder="youth_salary_initial_by_education",
    )
    dag.create_dag(etl_func=_transfer)
