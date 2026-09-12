import csv
import json
import re
import unicodedata
import xml.etree.ElementTree as ET

from airflow import DAG
from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
SOURCE_URL = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/231699/mp06233a109.xml"
CATALOG_URL = "https://data.gov.tw/dataset/162050"
ROC_YEAR = 109
KNOWN_BAD_YEARS = set()
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


def _normalise(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or ""))).strip()


def _parse_number(text, field_name):
    value = _normalise(text).replace(",", "")
    if value in {"", "—", "–", "-", "…", "..."}:
        raise ValueError(f"commuting source field {field_name} is empty")
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise ValueError(f"unparseable commuting value for {field_name}: {text!r}")
    return float(value) if "." in value else int(value)


def _find_field(children, aliases, field_name):
    for child in children:
        tag = _normalise(child.tag)
        if any(all(token in tag for token in alias) for alias in aliases):
            return _parse_number(child.text, field_name), tag
    raise ValueError(
        f"unable to find commuting role {field_name}; available fields="
        f"{[_normalise(child.tag) for child in children]}"
    )


def _parse_ntpc_record(xml_bytes):
    root = ET.fromstring(xml_bytes)
    for parent in root.iter():
        children = list(parent)
        if not any(_normalise(child.text) == "新北市" for child in children):
            continue

        total, total_tag = _find_field(
            children,
            [("總計",), ("Grand_total",)],
            "grand_total",
        )
        same_township, same_township_tag = _find_field(
            children,
            [("同現住鄉鎮市區",), ("Same_township_city_district",)],
            "same_township",
        )
        same_county, same_county_tag = _find_field(
            children,
            [("同現住縣市不同鄉鎮市區",), ("Same_county_city",)],
            "same_county_different_township",
        )
        other_county, other_county_tag = _find_field(
            children,
            [("不同縣市或國外地區",), ("Other_county_city_or_overseas",)],
            "other_county_or_overseas",
        )
        scopes = {
            "same_township": same_township,
            "same_county_different_township": same_county,
            "other_county_or_overseas": other_county,
        }
        if total != sum(scopes.values()):
            raise ValueError(
                "commuting source reconciliation failed: "
                f"grand_total={total}, output_scopes={sum(scopes.values())}"
            )
        return {
            "roc_year": ROC_YEAR,
            "area_name": "新北市",
            "grand_total": total,
            "scopes": scopes,
            "field_tags": {
                "grand_total": total_tag,
                "same_township": same_township_tag,
                "same_county_different_township": same_county_tag,
                "other_county_or_overseas": other_county_tag,
            },
        }
    raise ValueError("commuting XML has no 新北市 record")


def _get_source_bytes():
    import requests

    try:
        response = requests.get(
            SOURCE_URL,
            headers={"User-Agent": UA},
            timeout=120,
        )
    except requests.exceptions.SSLError:
        # This is a narrow compatibility fallback for the known Windows/DGBAS
        # certificate chain issue. Other hosts must not silently disable TLS.
        if "ws.dgbas.gov.tw" not in SOURCE_URL:
            raise
        response = requests.get(
            SOURCE_URL,
            headers={"User-Agent": UA},
            timeout=120,
            verify=False,
        )
    response.raise_for_status()
    return response.content


def drop_scope_anomalies(records):
    values = {
        int(record["roc_year"]): float(record["grand_total"])
        for record in records
        if int(record["roc_year"]) not in KNOWN_BAD_YEARS
    }
    if KNOWN_BAD_YEARS:
        records = [
            record
            for record in records
            if int(record["roc_year"]) not in KNOWN_BAD_YEARS
        ]
    if len(values) < 3:
        print("commuting scale guard skipped: only one census year is available")
        return records
    for year, value in values.items():
        peers = [peer for peer_year, peer in values.items() if peer_year != year]
        median = sorted(peers)[len(peers) // 2]
        if value == 0 or median == 0:
            if value != median:
                raise ValueError(
                    f"commuting scale anomaly ROC {year}: {value} versus peer median {median}"
                )
            continue
        if value / median > SCALE_ANOMALY_FACTOR or median / value > SCALE_ANOMALY_FACTOR:
            raise ValueError(
                f"commuting scale anomaly ROC {year}: {value} versus peer median {median}"
            )
    return records


def fetch_records():
    return [_parse_ntpc_record(_get_source_bytes())]


def transform_records(records, data_time):
    import pandas as pd

    rows = []
    for record in drop_scope_anomalies(records):
        ad_year = int(record["roc_year"]) + 1911
        base = {
            "period_start": f"{ad_year}-01-01",
            "period_end": f"{ad_year}-12-31",
            "period_type": "census",
            "age_lower": None,
            "age_upper": None,
            "age_band_raw": None,
            "gender": "total",
            "area_code": "65000",
            "area_level": "city",
            "unit": "人",
            "value_type": "count",
            "data_time": data_time,
        }
        for scope, value in record["scopes"].items():
            rows.append(
                {
                    **base,
                    "indicator_id": "commute_work_location_count",
                    "breakdown": json.dumps(
                        {
                            "flow_scope": scope,
                            "source_field": record["field_tags"][scope],
                            "source_dataset": CATALOG_URL,
                            "youth_18_35_status": "unavailable",
                            "youth_18_35_reason": (
                                "the machine-readable census table has no age field "
                                "or destination city; no age inference is made"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    "value": value,
                }
            )
    if not rows:
        raise RuntimeError("commuting transform produced no rows")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    for column in ("age_lower", "age_upper"):
        data[column] = pd.array(data[column], dtype="Int64")
    emitted_total = float(data["value"].sum())
    input_total = sum(float(record["grand_total"]) for record in records)
    if emitted_total != input_total:
        raise ValueError(
            f"commuting input/output reconciliation failed: input={input_total}, output={emitted_total}"
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
    records = fetch_records()
    data = transform_records(records, get_tpe_now_time_str(is_with_tz=True))
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
    dag_folder="youth_commute_census_ntpc",
)
dag.create_dag(etl_func=_transfer)
