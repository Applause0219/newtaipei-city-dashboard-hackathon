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
SOURCE_URL = "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-L50007-001"
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
AGE_ROLES = [
    ("under_30", "30歲以下", None, 30),
    ("31_40", "31-40歲", 31, 40),
    ("41_50", "41-50歲", 41, 50),
    ("51_65", "51-65歲", 51, 65),
    ("over_65", "65歲以上", 65, None),
]


def _normalise(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def _find_header(headers, role, predicate):
    matches = [index for index, header in enumerate(headers) if predicate(_normalise(header))]
    if len(matches) != 1:
        raise ValueError(
            f"NHI role {role} matched {len(matches)} headers: "
            f"{[(i, headers[i]) for i in matches]}"
        )
    return matches[0]


def _parse_count(value, field_name, allow_missing=False):
    text = _normalise(value).replace(",", "")
    if text in {"", "—", "–", "-", "…", "..."}:
        if allow_missing:
            return None
        raise ValueError(f"NHI required count is empty: {field_name}")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise ValueError(f"unparseable NHI count for {field_name}: {value!r}")
    number = float(text)
    return int(number) if number.is_integer() else number


def _resolve_columns(headers):
    year_col = _find_header(
        headers,
        "year",
        lambda value: value in {"年別", "年度"},
    )
    total_col = _find_header(
        headers,
        "national_total",
        lambda value: "抗憂鬱藥物使用人數" in value,
    )
    ntpc_col = _find_header(
        headers,
        "new_taipei_total",
        lambda value: "新北市" in value,
    )
    age_columns = {}
    for role, raw_label, _, _ in AGE_ROLES:
        label_pattern = {
            "under_30": lambda value: "30" in value and ("以下" in value or "以內" in value),
            "31_40": lambda value: "31" in value and "40" in value,
            "41_50": lambda value: "41" in value and "50" in value,
            "51_65": lambda value: "51" in value and "65" in value,
            "over_65": lambda value: "65" in value and "以上" in value,
        }[role]
        age_columns[role] = _find_header(
            headers,
            role,
            lambda value, pattern=label_pattern: "年齡別" in value and pattern(value),
        )
    return {
        "year": year_col,
        "national_total": total_col,
        "new_taipei_total": ntpc_col,
        "age": age_columns,
    }


def _parse_csv(content):
    rows = list(csv.reader(io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="")))
    if len(rows) < 2:
        raise ValueError("NHI CSV has no data rows")
    columns = _resolve_columns(rows[0])
    records = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(rows[0]):
            raise ValueError(
                f"NHI row {row_number} has {len(row)} fields, expected {len(rows[0])}"
            )
        year_text = _normalise(row[columns["year"]])
        if not re.fullmatch(r"\d{2,3}", year_text):
            raise ValueError(f"unparseable NHI ROC year in row {row_number}: {year_text!r}")
        roc_year = int(year_text)
        total = _parse_count(row[columns["national_total"]], "national_total")
        age_values = {}
        for role, raw_label, lower, upper in AGE_ROLES:
            age_values[role] = _parse_count(row[columns["age"][role]], role)
        age_sum = sum(age_values.values())
        if age_sum != total:
            raise ValueError(
                f"NHI age reconciliation failed for ROC {roc_year}: "
                f"total={total}, age_sum={age_sum}"
            )
        records.append(
            {
                "roc_year": roc_year,
                "national_total": total,
                "age_values": age_values,
                "new_taipei_total": _parse_count(
                    row[columns["new_taipei_total"]],
                    "new_taipei_total",
                    allow_missing=True,
                ),
                "new_taipei_raw": row[columns["new_taipei_total"]],
                "age_labels": {role: raw_label for role, raw_label, _, _ in AGE_ROLES},
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
        int(record["roc_year"]): float(record["national_total"])
        for record in usable
    }
    if KNOWN_BAD_YEARS:
        print(f"mental-health known bad years excluded: {sorted(KNOWN_BAD_YEARS)}")
    if len(values) < 3:
        print("mental-health scale guard skipped: fewer than three usable years")
        return usable
    for year, value in values.items():
        peers = [peer for peer_year, peer in values.items() if peer_year != year]
        median = sorted(peers)[len(peers) // 2]
        if value == 0 or median == 0:
            continue
        if value / median > SCALE_ANOMALY_FACTOR or median / value > SCALE_ANOMALY_FACTOR:
            raise ValueError(
                f"mental-health scale anomaly ROC {year}: {value} versus peer median {median}"
            )
    return usable


def fetch_records():
    return _parse_csv(_get_source_bytes())


def transform_records(records, data_time):
    import pandas as pd

    records = drop_scope_anomalies(records)
    rows = []
    missing_ntpc_years = []
    for record in records:
        roc_year = int(record["roc_year"])
        ad_year = roc_year + 1911
        base = {
            "period_start": f"{ad_year}-01-01",
            "period_end": f"{ad_year}-12-31",
            "period_type": "year",
            "gender": "total",
            "unit": "人",
            "value_type": "count",
            "data_time": data_time,
        }
        youth_reason = (
            "age bands are 30歲以下 and 31-40歲, so 18-35 cannot be exact; "
            "the source is antidepressant medication use, not counselling attendance"
        )
        rows.append(
            {
                **base,
                "indicator_id": "antidepressant_user_total_count",
                "age_lower": None,
                "age_upper": None,
                "age_band_raw": None,
                "area_code": "TW",
                "area_level": "country",
                "breakdown": json.dumps(
                    {
                        "measure": "national_total",
                        "source_dataset": SOURCE_URL,
                        "youth_18_35_status": "unavailable",
                        "youth_18_35_reason": youth_reason,
                    },
                    ensure_ascii=False,
                ),
                "value": record["national_total"],
            }
        )
        for role, raw_label, lower, upper in AGE_ROLES:
            rows.append(
                {
                    **base,
                    "indicator_id": "antidepressant_user_count_by_age",
                    "age_lower": lower,
                    "age_upper": upper,
                    "age_band_raw": raw_label,
                    "area_code": "TW",
                    "area_level": "country",
                    "breakdown": json.dumps(
                        {
                            "measure": "antidepressant_user_count",
                            "source_age_role": role,
                            "source_dataset": SOURCE_URL,
                            "youth_18_35_status": "unavailable",
                            "youth_18_35_reason": youth_reason,
                        },
                        ensure_ascii=False,
                    ),
                    "value": record["age_values"][role],
                }
            )
        if record["new_taipei_total"] is None:
            missing_ntpc_years.append(roc_year)
        else:
            rows.append(
                {
                    **base,
                    "indicator_id": "antidepressant_user_total_count",
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "area_code": "65000",
                    "area_level": "city",
                    "breakdown": json.dumps(
                        {
                            "measure": "new_taipei_total",
                            "source_dataset": SOURCE_URL,
                            "youth_18_35_status": "unavailable",
                            "youth_18_35_reason": youth_reason,
                        },
                        ensure_ascii=False,
                    ),
                    "value": record["new_taipei_total"],
                }
            )
    if missing_ntpc_years:
        print(f"NHI New Taipei series unavailable in source years: {missing_ntpc_years}")
    if not rows:
        raise RuntimeError("mental-health transform produced no rows")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    for column in ("age_lower", "age_upper"):
        data[column] = pd.array(data[column], dtype="Int64")

    national = data[data["area_code"] == "TW"]
    for _, record in enumerate(records):
        roc_year = int(record["roc_year"])
        year = str(roc_year + 1911)
        year_rows = national[national["period_start"].str.startswith(year)]
        emitted_total = float(
            year_rows[year_rows["indicator_id"] == "antidepressant_user_total_count"]["value"].iloc[0]
        )
        emitted_age_sum = float(
            year_rows[year_rows["indicator_id"] == "antidepressant_user_count_by_age"]["value"].sum()
        )
        if emitted_total != float(record["national_total"]) or emitted_age_sum != emitted_total:
            raise ValueError(
                f"mental-health input/output reconciliation failed for ROC {roc_year}: "
                f"source={record['national_total']}, total={emitted_total}, age_sum={emitted_age_sum}"
            )
        ntpc = data[
            (data["area_code"] == "65000")
            & data["period_start"].str.startswith(year)
        ]
        if record["new_taipei_total"] is not None:
            if len(ntpc) != 1 or float(ntpc["value"].iloc[0]) != float(record["new_taipei_total"]):
                raise ValueError(f"mental-health New Taipei reconciliation failed for ROC {roc_year}")
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
    dag_folder="youth_mental_health_antidepressant_tw",
)
dag.create_dag(etl_func=_transfer)
