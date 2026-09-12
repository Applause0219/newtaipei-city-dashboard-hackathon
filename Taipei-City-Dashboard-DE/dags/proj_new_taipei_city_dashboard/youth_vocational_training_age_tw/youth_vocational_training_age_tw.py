import csv
import io
import json
import re
import unicodedata

from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
CATALOG_URL = "https://data.gov.tw/dataset/146541"
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

AGE_SPECS = (
    ("under_15", "未滿15歲", None, 14),
    ("15_19", "15-19歲", 15, 19),
    ("20_24", "20-24歲", 20, 24),
    ("25_29", "25-29歲", 25, 29),
    ("30_34", "30-34歲", 30, 34),
    ("35_39", "35-39歲", 35, 39),
    ("40_44", "40-44歲", 40, 44),
    ("45_64", "45-64歲", 45, 64),
    ("65_plus", "65歲以上", 65, None),
)


def _normalise(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def _find_header(headers, role, predicate):
    matches = [i for i, header in enumerate(headers) if predicate(_normalise(header))]
    if len(matches) != 1:
        raise ValueError(
            f"職訓 CSV 欄位 {role} 應唯一匹配，實際 {matches}: {headers!r}"
        )
    return matches[0]


def _resolve_columns(headers):
    columns = {
        "item": _find_header(headers, "item", lambda value: value.startswith("項目別")),
        "classes": _find_header(headers, "classes", lambda value: value.startswith("班次")),
        "total": _find_header(headers, "total", lambda value: value.startswith("人數")),
        "male": _find_header(headers, "male", lambda value: value.startswith("男")),
        "female": _find_header(headers, "female", lambda value: value.startswith("女")),
    }
    age_columns = {}
    for role, raw_label, lower, upper in AGE_SPECS:
        age_columns[role] = _find_header(
            headers,
            role,
            lambda value, label=raw_label: value.startswith(label),
        )
    columns["age"] = age_columns
    return columns


def _parse_count(value, field_name):
    text = _normalise(value).replace(",", "")
    if text in {"", "—", "－", "-"}:
        raise ValueError(f"職訓 CSV {field_name} 是空值或破折號，不可當成 0")
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"職訓 CSV {field_name} 無法解析：{value!r}")
    return int(text)


def _parse_item(item):
    text = _normalise(item)
    match = re.fullmatch(r"(\d{2,3})年/(.*?)/(職前|在職)", text)
    if not match:
        raise ValueError(f"職訓 CSV 項目別無法解析：{item!r}")
    roc_year, institution, training_type = match.groups()
    if not institution:
        raise ValueError(f"職訓 CSV 機構名稱為空：{item!r}")
    return int(roc_year), institution, training_type


def parse_csv(content):
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    if len(rows) < 2:
        raise ValueError("職訓 CSV 沒有資料列")

    headers = rows[0]
    columns = _resolve_columns(headers)
    records = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(headers):
            raise ValueError(
                f"職訓 CSV 第 {row_number} 列有 {len(row)} 欄，預期 {len(headers)}"
            )
        roc_year, institution, training_type = _parse_item(row[columns["item"]])
        total = _parse_count(row[columns["total"]], "total")
        male = _parse_count(row[columns["male"]], "male")
        female = _parse_count(row[columns["female"]], "female")
        age_values = {}
        age_labels = {}
        for role, raw_label, lower, upper in AGE_SPECS:
            age_values[role] = _parse_count(row[columns["age"][role]], role)
            # age_band_raw 要的是「組別標籤」，不是儲存格的數值。
            # 原本這兩行讀同一個 row[columns["age"][role]]，
            # 於是 age_band_raw 被填成人數字串（實測 2,970 列全部
            # age_band_raw == value，且沒有任何一列含「歲」）。
            # AGE_SPECS 解包出來的 raw_label 才是正確來源。
            age_labels[role] = raw_label
        if total != male + female:
            raise ValueError(
                f"職訓 CSV ROC {roc_year} {institution} {training_type} "
                f"總數與男女人數不一致：{total} != {male}+{female}"
            )
        if total != sum(age_values.values()):
            raise ValueError(
                f"職訓 CSV ROC {roc_year} {institution} {training_type} "
                f"總數與年齡加總不一致：{total} != {sum(age_values.values())}"
            )
        records.append(
            {
                "roc_year": roc_year,
                "institution": institution,
                "training_type": training_type,
                "classes": _parse_count(row[columns["classes"]], "classes"),
                "total": total,
                "male": male,
                "female": female,
                "age_values": age_values,
                "age_labels": age_labels,
            }
        )
    return records


def resolve_csv_url(html):
    candidates = re.findall(r'"contentUrl"\s*:\s*"([^"]+)"', html)
    candidates = [candidate.replace("\\/", "/") for candidate in candidates]
    for candidate in candidates:
        if "apiservice.mol.gov.tw/OdService/download/" in candidate:
            return candidate
    raise RuntimeError("職訓資料集頁面找不到勞動部 CSV contentUrl")


def fetch_records():
    import requests

    headers = {"User-Agent": UA}
    catalog = requests.get(CATALOG_URL, headers=headers, timeout=120)
    catalog.raise_for_status()
    source_url = resolve_csv_url(catalog.text)
    response = requests.get(source_url, headers=headers, timeout=120)
    response.raise_for_status()
    records = parse_csv(response.content)
    if not records:
        raise RuntimeError("職訓來源下載成功但沒有有效資料")
    return records


def drop_scope_anomalies(records):
    usable = [
        record for record in records
        if record["roc_year"] not in KNOWN_BAD_YEARS
    ]
    totals = {}
    for record in usable:
        totals[record["roc_year"]] = totals.get(record["roc_year"], 0) + record["total"]
    if len(totals) >= 3:
        for year, value in totals.items():
            peers = [peer for peer_year, peer in totals.items() if peer_year != year]
            median = sorted(peers)[len(peers) // 2]
            if value and median and (
                value / median > SCALE_ANOMALY_FACTOR
                or median / value > SCALE_ANOMALY_FACTOR
            ):
                raise ValueError(
                    f"職訓年度規模異常 ROC {year}: {value} vs peer median {median}"
                )
    if KNOWN_BAD_YEARS:
        print(f"職訓排除已知異常年度：{sorted(KNOWN_BAD_YEARS)}")
    if not usable:
        raise ValueError("職訓所有資料列都被排除")
    return usable


def _append_record(rows, record, data_time):
    ad_year = record["roc_year"] + 1911
    base = {
        "period_start": f"{ad_year}-01-01",
        "period_end": f"{ad_year}-12-31",
        "period_type": "year",
        "area_code": "TW",
        "area_level": "country",
        "unit": "人",
        "value_type": "count",
        "data_time": data_time,
    }
    reason = (
        "15-19 歲跨越 18 歲、35-39 歲跨越 35 歲；只保留來源年齡組，"
        "不在 ETL 內推估 18-35"
    )
    common_breakdown = {
        "institution": record["institution"],
        "training_type": record["training_type"],
        "classes": record["classes"],
        "youth_18_35_status": "apportioned",
        "youth_18_35_reason": reason,
    }

    rows.append(
        {
            **base,
            "indicator_id": "vocational_training_completer_count",
            "age_lower": None,
            "age_upper": None,
            "age_band_raw": None,
            "gender": "total",
            "breakdown": json.dumps(
                {**common_breakdown, "source_dimension": "total"},
                ensure_ascii=False,
            ),
            "value": record["total"],
        }
    )
    for gender in ("male", "female"):
        rows.append(
            {
                **base,
                "indicator_id": "vocational_training_completer_count",
                "age_lower": None,
                "age_upper": None,
                "age_band_raw": None,
                "gender": gender,
                "breakdown": json.dumps(
                    {
                        **common_breakdown,
                        "source_dimension": "gender",
                        "age_gender_cross_tab": False,
                        "youth_18_35_status": "unavailable",
                    },
                    ensure_ascii=False,
                ),
                "value": record[gender],
            }
        )
    for role, raw_label, lower, upper in AGE_SPECS:
        rows.append(
            {
                **base,
                "indicator_id": "vocational_training_completer_count",
                "age_lower": lower,
                "age_upper": upper,
                "age_band_raw": record["age_labels"][role],
                "gender": "total",
                "breakdown": json.dumps(
                    {
                        **common_breakdown,
                        "source_dimension": "age",
                        "age_role": role,
                    },
                    ensure_ascii=False,
                ),
                "value": record["age_values"][role],
            }
        )


def transform_records(records, data_time):
    import pandas as pd

    records = drop_scope_anomalies(records)
    rows = []
    for record in records:
        _append_record(rows, record, data_time)
        emitted_age_sum = sum(record["age_values"].values())
        emitted_gender_sum = record["male"] + record["female"]
        if record["total"] != emitted_age_sum or record["total"] != emitted_gender_sum:
            raise ValueError(
                f"職訓輸出對帳失敗 ROC {record['roc_year']} "
                f"{record['institution']} {record['training_type']}"
            )
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = pd.array(data["age_lower"], dtype="Int64")
    data["age_upper"] = pd.array(data["age_upper"], dtype="Int64")
    if data.empty:
        raise RuntimeError("職訓轉換後沒有資料")
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
    dag_folder="youth_vocational_training_age_tw",
)
dag.create_dag(etl_func=_transfer)
