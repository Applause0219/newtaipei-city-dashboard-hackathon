import json
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36"
)
DATASET_PAGE = "https://data.gov.tw/dataset/146537"
SOURCE_URL = (
    "https://apiservice.mol.gov.tw/OdService/download/"
    "A17000000J-030271-iup"
)
NTPC_AREA_CODE = "65000"
KNOWN_BAD_YEARS = {}
SCALE_ANOMALY_FACTOR = 3.0
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


FIELD_ALIASES = {
    "item": (
        "項目別",
        "項目別(Statistical_Item)",
        "項目別 (Statistical_Item)",
    ),
    "total": ("總計（人）", "總計(人)", "總計"),
    "male": ("男（人）", "男(人)", "男性（人）", "男性(人)"),
    "female": ("女（人）", "女(人)", "女性（人）", "女性(人)"),
}


def _normalize_key(value):
    return re.sub(r"[\s_（）()\-]+", "", str(value or "")).casefold()


def _resolve_key(keys, aliases, role):
    normalized = {_normalize_key(key): key for key in keys}
    for alias in aliases:
        found = normalized.get(_normalize_key(alias))
        if found is not None:
            return found
    raise ValueError(
        f"Disability source role {role!r} is missing; keys={sorted(keys)!r}"
    )


def _number(raw, role):
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if text in {"", "-", "－", "—", "null", "None"}:
        raise ValueError(f"Disability source {role} has missing number {raw!r}")
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(
            f"Disability source {role} has non-numeric value {raw!r}"
        ) from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Disability source {role} has invalid number {raw!r}")
    return value


def _parse_age_band(raw):
    text = str(raw or "").strip()
    text = re.sub(r"\s*[（(]人[）)]\s*$", "", text)
    text = text.replace("～", "-").replace("_", "-")
    if re.fullmatch(r"未滿\d+歲", text):
        upper = int(re.search(r"\d+", text).group()) - 1
        return None, upper, text
    match = re.fullmatch(r"(\d+)-(\d+)歲", text)
    if match:
        lower, upper = map(int, match.groups())
        if lower > upper:
            raise ValueError(f"Disability age band is reversed: {raw!r}")
        return lower, upper, text
    match = re.fullmatch(r"(\d+)歲以上", text)
    if match:
        return int(match.group(1)), None, text
    raise ValueError(f"Disability age label is unparseable: {raw!r}")


def _parse_item(raw):
    text = str(raw or "").strip()
    match = re.fullmatch(r"(\d{3,4})年?底\s*/\s*(.+)", text)
    if not match:
        raise ValueError(
            f"Disability source item is not a year-end area label: {raw!r}"
        )
    year = int(match.group(1))
    year = year + 1911 if year < 1000 else year
    return year, match.group(2).strip()


def _check_scale_guard(period_totals):
    kept = {
        int(year): float(total)
        for year, total in period_totals.items()
        if int(year) not in KNOWN_BAD_YEARS
    }
    if KNOWN_BAD_YEARS:
        print(
            "Disability known bad years excluded from scale guard: "
            f"{KNOWN_BAD_YEARS}"
        )
    applicable = False
    for year, value in sorted(kept.items()):
        peers = [
            peer
            for peer_year, peer in kept.items()
            if peer_year != year and peer > 0
        ]
        if len(peers) < 3 or value <= 0:
            continue
        applicable = True
        peer_median = statistics.median(peers)
        if value > SCALE_ANOMALY_FACTOR * peer_median or (
            value * SCALE_ANOMALY_FACTOR < peer_median
        ):
            raise ValueError(
                f"Disability scale anomaly in {year}: {value} vs peer median "
                f"{peer_median} (factor={SCALE_ANOMALY_FACTOR})"
            )
    if applicable:
        print("Disability scale guard passed.")
    else:
        print(
            "Disability scale guard not applicable: fewer than four positive "
            "comparable years are available."
        )


def fetch_source_records():
    import requests

    response = requests.get(SOURCE_URL, headers={"User-Agent": UA}, timeout=120)
    response.raise_for_status()
    records = response.json()
    if not isinstance(records, list) or not records:
        raise ValueError("Disability source JSON is empty or not a list")
    return records


def transform_records(source_records, data_time=None):
    effective_data_time = data_time or datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")
    keys = set().union(*(record.keys() for record in source_records))
    item_key = _resolve_key(keys, FIELD_ALIASES["item"], "item")
    total_key = _resolve_key(keys, FIELD_ALIASES["total"], "total")
    male_key = _resolve_key(keys, FIELD_ALIASES["male"], "male")
    female_key = _resolve_key(keys, FIELD_ALIASES["female"], "female")

    age_fields = []
    for key in sorted(keys):
        label = re.sub(r"\s*[（(]人[）)]\s*$", "", str(key).strip())
        if "歲" not in label:
            continue
        lower, upper, raw_label = _parse_age_band(label)
        age_fields.append((key, lower, upper, raw_label))
    if not age_fields:
        raise ValueError("Disability source has no parseable age fields")

    rows = []
    period_totals = {}
    target_source_rows = 0
    for source_record in source_records:
        year, area_name = _parse_item(source_record.get(item_key))
        if area_name != "新北市":
            continue
        target_source_rows += 1
        source_total = _number(source_record.get(total_key), total_key)
        source_male = _number(source_record.get(male_key), male_key)
        source_female = _number(source_record.get(female_key), female_key)
        emitted_total = 0.0
        for source_key, lower, upper, raw_label in age_fields:
            value = _number(source_record.get(source_key), source_key)
            emitted_total += value
            rows.append(
                {
                    "indicator_id": "youth_disability_age_ntpc",
                    "period_start": f"{year}-12-31",
                    "period_end": f"{year}-12-31",
                    "period_type": "year_end",
                    "age_lower": lower,
                    "age_upper": upper,
                    "age_band_raw": raw_label,
                    "gender": "total",
                    "area_code": NTPC_AREA_CODE,
                    "area_level": "city",
                    "breakdown": json.dumps(
                        {
                            "area_name": area_name,
                            "metric": "定額進用身心障礙者實際進用人數",
                            "source_dataset": "146537",
                            "source_item": source_record[item_key],
                            "source_field": source_key,
                            "source_gender_scope": "total",
                            "source_male_total": source_male,
                            "source_female_total": source_female,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "value": value,
                    "unit": "persons",
                    "value_type": "count",
                    "data_time": effective_data_time,
                }
            )
        if not math.isclose(source_total, emitted_total, abs_tol=1e-6):
            raise ValueError(
                f"Disability {year} reconciliation failed: source total "
                f"{source_total} != age total {emitted_total}"
            )
        if year in period_totals:
            raise ValueError(f"Duplicate disability source year {year}")
        period_totals[year] = source_total

    if not target_source_rows:
        raise ValueError("Disability source has no New Taipei year-end rows")
    _check_scale_guard(period_totals)
    emitted_by_year = defaultdict(float)
    for row in rows:
        emitted_by_year[int(row["period_start"][:4])] += float(row["value"])
    for year, source_total in period_totals.items():
        if not math.isclose(
            source_total, emitted_by_year[year], abs_tol=1e-6
        ):
            raise ValueError(
                f"Disability source/output reconciliation failed for {year}: "
                f"input={source_total}, output={emitted_by_year[year]}"
            )

    data = _as_dataframe(rows)
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(
            f"Disability contract columns mismatch: {list(data.columns)!r}"
        )
    return data


def _as_dataframe(rows):
    import pandas as pd

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos") or {}
    data = transform_records(
        fetch_source_records(),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")
    print(
        "New Taipei disability source total =========== "
        f"{data['value'].sum()}"
    )
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
    dag_folder="youth_disability_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
