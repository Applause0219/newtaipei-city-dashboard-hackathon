import csv
import io
import json
import re
import unicodedata

from airflow import DAG
from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
SOURCE_URL = "https://www-api.moda.gov.tw/OpenData/Files/4303"
CATALOG_URL = "https://data.gov.tw/dataset/5960"
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
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or ""))).strip()


def _find_header(headers, *role_parts):
    predicate = role_parts[-1] if role_parts and callable(role_parts[-1]) else None
    tokens = role_parts[:-1] if predicate is not None else role_parts
    matches = []
    for index, header in enumerate(headers):
        normalized = _normalise(header)
        if predicate is not None and not predicate(normalized):
            continue
        if tokens and not all(part in normalized for part in tokens):
            continue
        matches.append(index)
    if len(matches) != 1:
        raise ValueError(
            f"digital source role {role_parts} matched {len(matches)} headers: "
            f"{[(i, _normalise(headers[i])) for i in matches]}"
        )
    return matches[0]


def _find_age_header(headers, measure, age_label):
    matches = []
    for index, header in enumerate(headers):
        normalized = _normalise(header)
        if measure not in normalized or not normalized.startswith(age_label):
            continue
        if re.match(rf"^{re.escape(age_label)}歲", normalized):
            matches.append(index)
    if len(matches) != 1:
        raise ValueError(
            f"digital source age role {(measure, age_label)} matched {len(matches)} headers"
        )
    return matches[0]


def _parse_rate(value, field_name):
    text = _normalise(value).replace(",", "")
    if text in {"", "—", "–", "-", "…", "..."}:
        return None
    text = text.rstrip("%")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise ValueError(f"unparseable digital rate for {field_name}: {value!r}")
    result = float(text)
    if result < 0 or result > 100:
        raise ValueError(f"digital rate out of range for {field_name}: {result}")
    return result


def _age_interval(roc_year, age_label):
    if age_label == "12-14":
        return 12, 14
    if age_label == "15-20":
        if roc_year <= 100:
            return 15, 20
        if roc_year <= 108:
            return 15, 19
        return 12, 19
    if age_label == "21-30":
        return (21, 30) if roc_year <= 100 else (20, 29)
    if age_label == "31-40":
        return (31, 40) if roc_year <= 100 else (30, 39)
    if age_label == "41-50":
        return (41, 50) if roc_year <= 100 else (40, 49)
    if age_label == "51-60":
        return (51, 60) if roc_year <= 100 else (50, 59)
    if age_label == "61":
        if roc_year <= 100:
            return 61, None
        if roc_year == 101:
            return 60, None
        return 60, 64
    if age_label == "65":
        if roc_year < 102:
            raise ValueError(
                f"digital source exposed a 65+ value before ROC 102: {roc_year}"
            )
        return 65, None
    raise ValueError(f"unknown digital age role: {age_label}")


def _parse_csv(content):
    # MODA's published CSV is Big5/CP950. csv.reader is required because the
    # source contains quoted headers with line breaks and commas in values.
    rows = list(csv.reader(io.StringIO(content.decode("cp950"))))
    if len(rows) < 2:
        raise ValueError("digital source CSV has no data rows")
    headers = rows[0]
    year_col = _find_header(headers, lambda value: value == "年度")
    total_col = _find_header(headers, "個人上網率")
    ntpc_col = _find_header(headers, "新北市", "網路使用率")
    age_labels = ["12-14", "15-20", "21-30", "31-40", "41-50", "51-60", "61", "65"]
    roles = {
        "internet_age": {
            age_label: _find_age_header(headers, "網路使用率", age_label)
            for age_label in age_labels
        },
        "computer_age": {
            age_label: _find_age_header(headers, "電腦使用率", age_label)
            for age_label in age_labels
        },
    }
    records = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(headers):
            raise ValueError(
                f"digital source row {row_number} has {len(row)} fields, expected {len(headers)}"
            )
        year_text = _normalise(row[year_col])
        if not re.fullmatch(r"\d{2,3}", year_text):
            raise ValueError(f"unparseable ROC year in digital source row {row_number}: {year_text!r}")
        roc_year = int(year_text)
        values = {
            "internet_total": _parse_rate(row[total_col], "internet_total"),
            "ntpc_internet_total": _parse_rate(row[ntpc_col], "ntpc_internet_total"),
        }
        for measure, age_columns in roles.items():
            for age_label, column in age_columns.items():
                values[f"{measure}:{age_label}"] = _parse_rate(
                    row[column], f"{measure}:{age_label}"
                )
        records.append(
            {
                "roc_year": roc_year,
                "headers": headers,
                "values": values,
            }
        )
    return records


def _get_source_bytes():
    import requests

    response = requests.get(SOURCE_URL, headers={"User-Agent": UA}, timeout=120)
    response.raise_for_status()
    return response.content


def drop_scope_anomalies(records):
    usable = [
        record
        for record in records
        if int(record["roc_year"]) not in KNOWN_BAD_YEARS
    ]
    values = {
        int(record["roc_year"]): record["values"]["internet_total"]
        for record in usable
        if record["values"]["internet_total"] is not None
    }
    if KNOWN_BAD_YEARS:
        print(f"digital known bad years excluded: {sorted(KNOWN_BAD_YEARS)}")
    if len(values) < 3:
        print("digital scale guard skipped: fewer than three usable years")
        return usable
    for year, value in values.items():
        peers = [peer for peer_year, peer in values.items() if peer_year != year]
        median = sorted(peers)[len(peers) // 2]
        if value == 0 or median == 0:
            continue
        if value / median > SCALE_ANOMALY_FACTOR or median / value > SCALE_ANOMALY_FACTOR:
            raise ValueError(
                f"digital scale anomaly ROC {year}: {value} versus peer median {median}"
            )
    return usable


def fetch_records():
    return _parse_csv(_get_source_bytes())


def transform_records(records, data_time):
    import pandas as pd

    records = drop_scope_anomalies(records)
    rows = []
    expected_cells = set()
    emitted_cells = set()
    age_labels = ["12-14", "15-20", "21-30", "31-40", "41-50", "51-60", "61", "65"]
    for record in records:
        roc_year = int(record["roc_year"])
        ad_year = roc_year + 1911
        base = {
            "period_start": f"{ad_year}-01-01",
            "period_end": f"{ad_year}-12-31",
            "period_type": "year",
            "gender": "total",
            "unit": "%",
            "value_type": "rate",
            "data_time": data_time,
        }

        for role, value in record["values"].items():
            if value is not None:
                expected_cells.add((roc_year, role))

        value = record["values"]["internet_total"]
        if value is not None:
            emitted_cells.add((roc_year, "internet_total"))
            rows.append(
                {
                    **base,
                    "indicator_id": "internet_use_rate_tw_total",
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "area_code": "TW",
                    "area_level": "country",
                    "breakdown": json.dumps(
                        {
                            "measure": "personal_internet_use_rate",
                            "source_role": "internet_total",
                            "source_dataset": CATALOG_URL,
                            "youth_18_35_status": "unavailable",
                            "youth_18_35_reason": "source provides a rate only and no 18-35 denominator",
                        },
                        ensure_ascii=False,
                    ),
                    "value": value,
                }
            )

        value = record["values"]["ntpc_internet_total"]
        if value is not None:
            emitted_cells.add((roc_year, "ntpc_internet_total"))
            rows.append(
                {
                    **base,
                    "indicator_id": "internet_use_rate_ntpc_total",
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "area_code": "65000",
                    "area_level": "city",
                    "breakdown": json.dumps(
                        {
                            "measure": "personal_internet_use_rate",
                            "source_role": "ntpc_internet_total",
                            "source_dataset": CATALOG_URL,
                            "youth_18_35_status": "unavailable",
                            "youth_18_35_reason": "source provides a rate only and no 18-35 denominator",
                        },
                        ensure_ascii=False,
                    ),
                    "value": value,
                }
            )

        for measure in ("internet_age", "computer_age"):
            for age_label in age_labels:
                role = f"{measure}:{age_label}"
                value = record["values"][role]
                if value is None:
                    continue
                lower, upper = _age_interval(roc_year, age_label)
                emitted_cells.add((roc_year, role))
                rows.append(
                    {
                        **base,
                        "indicator_id": f"{measure}_rate_tw",
                        "age_lower": lower,
                        "age_upper": upper,
                        "age_band_raw": _normalise(
                            record["headers"][
                                _find_age_header(
                                    record["headers"],
                                    "網路使用率" if measure == "internet_age" else "電腦使用率",
                                    age_label,
                                )
                            ]
                        ),
                        "area_code": "TW",
                        "area_level": "country",
                        "breakdown": json.dumps(
                            {
                                "measure": measure,
                                "source_age_role": age_label,
                                "source_dataset": CATALOG_URL,
                                "youth_18_35_status": "unavailable",
                                "youth_18_35_reason": (
                                    "published values are rates in source age bands; "
                                    "no denominator is supplied for exact 18-35 conversion"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        "value": value,
                    }
                )

    if expected_cells != emitted_cells:
        raise ValueError(
            "digital input/output reconciliation failed: "
            f"expected_cells={len(expected_cells)}, emitted_cells={len(emitted_cells)}"
        )
    if not rows:
        raise RuntimeError("digital transform produced no rows")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    for column in ("age_lower", "age_upper"):
        data[column] = pd.array(data[column], dtype="Int64")
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
    dag_folder="youth_digital_access_age_tw",
)
dag.create_dag(etl_func=_transfer)
