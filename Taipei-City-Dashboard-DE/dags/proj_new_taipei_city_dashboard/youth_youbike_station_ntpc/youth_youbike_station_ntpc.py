import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime

from airflow import DAG
from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
SOURCE_URL = "https://data.ntpc.gov.tw/api/datasets/010e5b15-3823-4b20-b401-b1cf000550c5/csv/file"
CATALOG_URL = "https://data.gov.tw/dataset/146969"
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
DISTRICTS = {
    "板橋區": "65000010",
    "三重區": "65000020",
    "中和區": "65000030",
    "永和區": "65000040",
    "新莊區": "65000050",
    "新店區": "65000060",
    "樹林區": "65000070",
    "鶯歌區": "65000080",
    "三峽區": "65000090",
    "淡水區": "65000100",
    "汐止區": "65000110",
    "瑞芳區": "65000120",
    "土城區": "65000130",
    "蘆洲區": "65000140",
    "五股區": "65000150",
    "泰山區": "65000160",
    "林口區": "65000170",
    "深坑區": "65000180",
    "石碇區": "65000190",
    "坪林區": "65000200",
    "三芝區": "65000210",
    "石門區": "65000220",
    "八里區": "65000230",
    "平溪區": "65000240",
    "雙溪區": "65000250",
    "貢寮區": "65000260",
    "金山區": "65000270",
    "萬里區": "65000280",
    "烏來區": "65000290",
}
FIELD_ALIASES = {
    "city": {"scity", "city", "縣市"},
    "city_en": {"scityen", "city_en"},
    "district": {"sarea", "district", "行政區"},
    "station_id": {"sno", "station_id", "站點代號"},
    "station_name": {"sna", "station_name", "站點名稱"},
    "address": {"ar", "address", "地址"},
    "total_slots": {"tot_quantity", "total_slots", "總車位"},
    "available_bikes": {"sbi_quantity", "available_bikes", "可借車輛"},
    "snapshot": {"mday", "update_time", "更新時間"},
    "available_docks": {"bemp", "available_docks", "可借車位"},
}


def _normalise(value):
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _resolve_columns(headers):
    normalised = {_normalise(header): header for header in headers}
    resolved = {}
    for role, aliases in FIELD_ALIASES.items():
        matches = [normalised[alias] for alias in aliases if alias in normalised]
        if len(matches) != 1:
            raise ValueError(
                f"YouBike role {role} matched {len(matches)} headers; available={headers}"
            )
        resolved[role] = matches[0]
    return resolved


def _parse_count(value, field_name):
    text = str(value or "").strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise ValueError(f"unparseable YouBike count for {field_name}: {value!r}")
    number = float(text)
    if number < 0 or not number.is_integer():
        raise ValueError(f"invalid nonnegative integer YouBike count for {field_name}: {value!r}")
    return int(number)


def _parse_snapshot(value):
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%S").strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"unparseable YouBike snapshot timestamp: {value!r}") from exc


def _parse_csv(content):
    reader = csv.DictReader(io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline=""))
    if not reader.fieldnames:
        raise ValueError("YouBike source CSV has no header")
    columns = _resolve_columns(reader.fieldnames)
    records = []
    for row_number, row in enumerate(reader, start=2):
        city = str(row[columns["city"]] or "").strip()
        city_en = str(row[columns["city_en"]] or "").strip()
        if city != "新北市" and city_en != "New Taipei City":
            continue
        district = str(row[columns["district"]] or "").strip()
        if district not in DISTRICTS:
            raise ValueError(
                f"YouBike row {row_number} has unmapped New Taipei district: {district!r}"
            )
        records.append(
            {
                "district": district,
                "station_id": str(row[columns["station_id"]] or "").strip(),
                "station_name": str(row[columns["station_name"]] or "").strip(),
                "address": str(row[columns["address"]] or "").strip(),
                "total_slots": _parse_count(row[columns["total_slots"]], "total_slots"),
                "available_bikes": _parse_count(
                    row[columns["available_bikes"]], "available_bikes"
                ),
                "available_docks": _parse_count(
                    row[columns["available_docks"]], "available_docks"
                ),
                "snapshot": _parse_snapshot(row[columns["snapshot"]]),
            }
        )
    if not records:
        raise RuntimeError("YouBike source has no New Taipei rows")
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
        if int(record["snapshot"][:4]) - 1911 not in KNOWN_BAD_YEARS
    ]
    totals = defaultdict(float)
    for record in usable:
        totals[record["snapshot"]] += record["total_slots"]
    if KNOWN_BAD_YEARS:
        print(f"YouBike known bad years excluded: {sorted(KNOWN_BAD_YEARS)}")
    if len(totals) < 3:
        print("YouBike scale guard skipped: source is a single current snapshot")
        return usable
    for snapshot, value in totals.items():
        peers = [peer for peer_snapshot, peer in totals.items() if peer_snapshot != snapshot]
        median = sorted(peers)[len(peers) // 2]
        if value == 0 or median == 0:
            continue
        if value / median > SCALE_ANOMALY_FACTOR or median / value > SCALE_ANOMALY_FACTOR:
            raise ValueError(
                f"YouBike scale anomaly at {snapshot}: {value} versus peer median {median}"
            )
    return usable


def fetch_records():
    return _parse_csv(_get_source_bytes())


def transform_records(records, data_time):
    import pandas as pd

    records = drop_scope_anomalies(records)
    rows = []
    for record in records:
        base = {
            "period_start": record["snapshot"],
            "period_end": record["snapshot"],
            "period_type": "instant",
            "age_lower": None,
            "age_upper": None,
            "age_band_raw": None,
            "gender": "total",
            "area_code": DISTRICTS[record["district"]],
            "area_level": "district",
            "unit": "輛",
            "value_type": "count",
            "data_time": data_time,
        }
        for indicator_id, source_field, unit in (
            ("youbike_total_slots", "total_slots", "輛"),
            ("youbike_available_bikes", "available_bikes", "輛"),
            ("youbike_available_docks", "available_docks", "個"),
        ):
            rows.append(
                {
                    **base,
                    "indicator_id": indicator_id,
                    "unit": unit,
                    "breakdown": json.dumps(
                        {
                            "station_id": record["station_id"],
                            "station_name": record["station_name"],
                            "address": record["address"],
                            "source_field": source_field,
                            "source_dataset": CATALOG_URL,
                            "source_snapshot": record["snapshot"],
                            "youth_18_35_status": "unavailable",
                            "youth_18_35_reason": "source is station inventory and has no rider age or gender",
                        },
                        ensure_ascii=False,
                    ),
                    "value": record[source_field],
                }
            )
    if len(rows) != len(records) * 3:
        raise ValueError("YouBike input/output reconciliation failed: station row count changed")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    for column in ("age_lower", "age_upper"):
        data[column] = pd.array(data[column], dtype="Int64")
    for indicator_id, source_field in (
        ("youbike_total_slots", "total_slots"),
        ("youbike_available_bikes", "available_bikes"),
        ("youbike_available_docks", "available_docks"),
    ):
        input_total = sum(record[source_field] for record in records)
        output_total = data.loc[data["indicator_id"] == indicator_id, "value"].sum()
        if float(input_total) != float(output_total):
            raise ValueError(
                f"YouBike reconciliation failed for {indicator_id}: "
                f"input={input_total}, output={output_total}"
            )
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
    dag_folder="youth_youbike_station_ntpc",
)
dag.create_dag(etl_func=_transfer)
