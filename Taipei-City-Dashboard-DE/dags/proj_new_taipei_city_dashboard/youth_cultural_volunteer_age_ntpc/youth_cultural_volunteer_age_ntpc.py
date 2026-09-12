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
DATASET_PAGE = "https://data.ntpc.gov.tw/datasets/0e719f1c-c1a8-4428-9d46-db942ed414a9"
SOURCE_URL = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "0e719f1c-c1a8-4428-9d46-db942ed414a9/json?page=0&size=1000"
)
NTPC_AREA_CODE = "65000"
KNOWN_BAD_YEARS = {}
KNOWN_SOURCE_TOTAL_DISCREPANCIES = {
    2013: 1.0,
}
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
    "year": ("field1", "year", "年份"),
    "total_male": (
        "itemvalue6",
        "cultural_volunteer_total_male",
        "文化志工合計男",
    ),
    "total_female": (
        "itemvalue7",
        "cultural_volunteer_total_female",
        "文化志工合計女",
    ),
}
AGE_FIELD_SPECS = (
    (
        "未滿12歲",
        "male",
        ("itemvalue8", "cultural_volunteer_under_12_male"),
    ),
    (
        "未滿12歲",
        "female",
        ("itemvalue9", "cultural_volunteer_under_12_female"),
    ),
    (
        "12_17歲",
        "male",
        ("itemvalue10", "cultural_volunteer_12_17_male"),
    ),
    (
        "12_17歲",
        "female",
        ("itemvalue11", "cultural_volunteer_12_17_female"),
    ),
    (
        "18_29歲",
        "male",
        ("itemvalue12", "cultural_volunteer_18_29_male"),
    ),
    (
        "18_29歲",
        "female",
        ("itemvalue13", "cultural_volunteer_18_29_female"),
    ),
    (
        "30_49歲",
        "male",
        ("itemvalue14", "cultural_volunteer_30_49_male"),
    ),
    (
        "30_49歲",
        "female",
        ("itemvalue15", "cultural_volunteer_30_49_female"),
    ),
    (
        "50_54歲",
        "male",
        ("itemvalue16", "cultural_volunteer_50_54_male"),
    ),
    (
        "50_54歲",
        "female",
        ("itemvalue17", "cultural_volunteer_50_54_female"),
    ),
    (
        "55_64歲",
        "male",
        ("itemvalue18", "cultural_volunteer_55_64_male"),
    ),
    (
        "55_64歲",
        "female",
        ("itemvalue19", "cultural_volunteer_55_64_female"),
    ),
    (
        "65歲以上",
        "male",
        ("itemvalue20", "cultural_volunteer_65_plus_male"),
    ),
    (
        "65歲以上",
        "female",
        ("itemvalue21", "cultural_volunteer_65_plus_female"),
    ),
)


def _normalize_key(value):
    return re.sub(r"[\s_\-（）()]+", "", str(value or "")).casefold()


def _resolve_key(keys, aliases, role):
    normalized = {_normalize_key(key): key for key in keys}
    for alias in aliases:
        found = normalized.get(_normalize_key(alias))
        if found is not None:
            return found
    raise ValueError(
        f"New Taipei cultural source role {role!r} is missing; "
        f"keys={sorted(keys)!r}"
    )


def _number(raw, role):
    text = str(raw if raw is not None else "").strip()
    text = text.replace(",", "")
    if text in {"", "-", "－", "—", "null", "None"}:
        raise ValueError(f"Cultural source {role} has missing number {raw!r}")
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(
            f"Cultural source {role} has non-numeric value {raw!r}"
        ) from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Cultural source {role} has invalid number {raw!r}")
    return value


def _parse_year(raw):
    text = str(raw or "").strip()
    if not re.fullmatch(r"\d{3,4}", text):
        raise ValueError(f"Cultural source has unparseable year {raw!r}")
    year = int(text)
    return year + 1911 if year < 1000 else year


def _parse_age_band(raw):
    text = str(raw or "").strip().replace("_", "-").replace("～", "-")
    if re.fullmatch(r"未滿\d+歲", text):
        upper = int(re.search(r"\d+", text).group()) - 1
        return None, upper
    match = re.fullmatch(r"(\d+)-(\d+)歲", text)
    if match:
        lower, upper = map(int, match.groups())
        if lower > upper:
            raise ValueError(f"Cultural source age band is reversed: {raw!r}")
        return lower, upper
    match = re.fullmatch(r"(\d+)歲以上", text)
    if match:
        return int(match.group(1)), None
    raise ValueError(f"Cultural source age label is unparseable: {raw!r}")


def _check_scale_guard(period_totals):
    kept = {
        int(year): float(total)
        for year, total in period_totals.items()
        if int(year) not in KNOWN_BAD_YEARS
    }
    if KNOWN_BAD_YEARS:
        print(
            "Cultural known bad years excluded from scale guard: "
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
                f"Cultural scale anomaly in {year}: {value} vs peer median "
                f"{peer_median} (factor={SCALE_ANOMALY_FACTOR})"
            )
    if applicable:
        print("Cultural scale guard passed.")
    else:
        print(
            "Cultural scale guard not applicable: fewer than four positive "
            "comparable years are available."
        )


def fetch_source_records():
    import requests

    response = requests.get(SOURCE_URL, headers={"User-Agent": UA}, timeout=120)
    response.raise_for_status()
    records = response.json()
    if not isinstance(records, list) or not records:
        raise ValueError("Cultural source JSON is empty or not a list")
    return records


def transform_records(source_records, data_time=None):
    effective_data_time = data_time or datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")
    keys = set().union(*(record.keys() for record in source_records))
    year_key = _resolve_key(keys, FIELD_ALIASES["year"], "year")
    total_male_key = _resolve_key(
        keys, FIELD_ALIASES["total_male"], "total_male"
    )
    total_female_key = _resolve_key(
        keys, FIELD_ALIASES["total_female"], "total_female"
    )
    age_fields = []
    for raw_label, gender, aliases in AGE_FIELD_SPECS:
        key = _resolve_key(keys, aliases, f"{raw_label}/{gender}")
        lower, upper = _parse_age_band(raw_label)
        age_fields.append((raw_label, lower, upper, gender, key))

    rows = []
    period_totals = {}
    for source_record in source_records:
        year = _parse_year(source_record.get(year_key))
        total_male = _number(source_record.get(total_male_key), total_male_key)
        total_female = _number(
            source_record.get(total_female_key), total_female_key
        )
        reported_total = total_male + total_female
        emitted_total = 0.0
        for raw_label, lower, upper, gender, source_key in age_fields:
            value = _number(source_record.get(source_key), source_key)
            emitted_total += value
            rows.append(
                {
                    "indicator_id": "youth_cultural_volunteer_age_ntpc",
                    "period_start": f"{year}-01-01",
                    "period_end": f"{year}-12-31",
                    "period_type": "calendar_year",
                    "age_lower": lower,
                    "age_upper": upper,
                    "age_band_raw": raw_label,
                    "gender": gender,
                    "area_code": NTPC_AREA_CODE,
                    "area_level": "city",
                    "breakdown": json.dumps(
                        {
                            "area_name": "新北市",
                            "metric": "文化局圖書館、博物館及園區志工人數",
                            "source_dataset": "0e719f1c-c1a8-4428-9d46-db942ed414a9",
                            "source_field": source_key,
                        "source_year": year,
                        "source_total_male": total_male,
                        "source_total_female": total_female,
                        "source_reported_total": reported_total,
                        "source_age_fields_total": None,
                        "source_reported_total_difference": None,
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
        reported_difference = emitted_total - reported_total
        if not math.isclose(reported_difference, 0.0, abs_tol=1e-6):
            expected_difference = KNOWN_SOURCE_TOTAL_DISCREPANCIES.get(year)
            if expected_difference is None or not math.isclose(
                reported_difference, expected_difference, abs_tol=1e-6
            ):
                raise ValueError(
                    f"Cultural source arithmetic check failed in {year}: "
                    f"age total {emitted_total} != reported total "
                    f"{reported_total}"
                )
            print(
                f"Cultural {year}: reviewed source aggregate discrepancy "
                f"{reported_difference}; age fields remain unaltered."
            )
        for row in rows[-len(age_fields):]:
            breakdown = json.loads(row["breakdown"])
            breakdown["source_age_fields_total"] = emitted_total
            breakdown["source_reported_total_difference"] = reported_difference
            row["breakdown"] = json.dumps(
                breakdown, ensure_ascii=False, sort_keys=True
            )
        if year in period_totals:
            raise ValueError(f"Duplicate cultural source year {year}")
        # The input measure for this age-grain table is the sum of the raw age
        # fields.  The reviewed 2013 aggregate column is off by one; no
        # correction is applied to any age row.
        period_totals[year] = emitted_total

    _check_scale_guard(period_totals)
    emitted_by_year = defaultdict(float)
    for row in rows:
        emitted_by_year[int(row["period_start"][:4])] += float(row["value"])
    for year, source_total in period_totals.items():
        if not math.isclose(
            source_total, emitted_by_year[year], abs_tol=1e-6
        ):
            raise ValueError(
                f"Cultural source/output reconciliation failed for {year}: "
                f"input={source_total}, output={emitted_by_year[year]}"
            )

    data = _as_dataframe(rows)
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(
            f"Cultural contract columns mismatch: {list(data.columns)!r}"
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
        "Cultural volunteer source total =========== "
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
    dag_folder="youth_cultural_volunteer_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
