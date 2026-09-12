"""新北市各年齡死亡率 DAG。"""

import json
import math
import re
import statistics
import unicodedata

from airflow import DAG  # noqa: F401
from operators.common_pipeline import CommonDag

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{dataset}/{year}"
DATASET = "ODRP117"
COUNTY = "新北市"
CITY_CODE = "65000"
SOURCE_CITY_CODE = "65000000"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
FIRST_PROBE_YEAR = 90
MAX_PROBE_YEAR = 130
SCALE_ANOMALY_FACTOR = 3.0
KNOWN_BAD_YEARS = {}

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

# 民國113缺一個來源格；不補造零值，只保留其餘來源格並標示不完整。
KNOWN_INCOMPLETE_PERIODS = {
    113: {
        "missing_cells": [{"gender": "male", "age_lower": 0}],
        "reason": ("實測民國113新北市回傳201格而非202格，缺少男、0歲；不補造零值。"),
    },
}

COLUMN_ALIASES = {
    "period_year": ("statistical_year", "統計年", "統計年度"),
    "according": ("according", "按照別"),
    "area_code": ("area_code", "行政區域代碼"),
    "county": ("county", "縣市別"),
    "sex": ("sex", "性別"),
    "age": ("age", "歲數"),
    "death_rate": ("death_rates", "死亡率"),
}
GENDER_MAP = {"1": "male", "2": "female", "男": "male", "女": "female"}


def _normalise_key(value):
    text = "" if value is None else str(value)
    text = text.replace("\ufeff", "").strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _number(value, context):
    raw = _normalise_key(value).replace(",", "")
    if not raw:
        raise ValueError(f"{context} 為空，不能靜默當成 0")
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"{context} 不是數值：{value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{context} 不是非負有限數值：{value!r}")
    return number


def _parse_age(raw_label):
    """ODRP117為單齡；來源的100代表最後一個100歲以上開放組。"""
    label = unicodedata.normalize("NFKC", _normalise_key(raw_label))
    label = re.sub(r"\s+", "", label)
    if label in {"100", "100歲以上"}:
        return 100, None
    match = re.fullmatch(r"(\d{1,3})(?:歲)?", label)
    if not match:
        raise ValueError(f"無法解析ODRP117年齡標籤：{raw_label!r}")
    age = int(match.group(1))
    if not 0 <= age <= 99:
        raise ValueError(f"ODRP117單齡超出0至99歲範圍：{raw_label!r}")
    return age, age


def _normalise_frame(records):
    import pandas as pd

    if not records:
        raise RuntimeError("ODRP117沒有資料列")
    frame = pd.DataFrame(records).copy()
    frame.columns = [_normalise_key(column) for column in frame.columns]
    if len(frame.columns) != len(set(frame.columns)):
        raise ValueError(f"ODRP117清理欄名後出現重複欄位：{list(frame.columns)}")
    rename = {}
    for role, candidates in COLUMN_ALIASES.items():
        matches = [column for column in frame.columns if column in candidates]
        if len(matches) != 1:
            raise ValueError(
                f"ODRP117角色{role}找不到唯一欄位；"
                f"候選={candidates}、實際={list(frame.columns)}"
            )
        rename[matches[0]] = role
    return frame.rename(columns=rename)


def _select_ntpc(frame, roc_year):
    frame = frame.copy()
    for column in ("period_year", "according", "area_code", "county", "sex", "age"):
        frame[column] = frame[column].map(_normalise_key)
    ntpc = frame[frame["county"] == COUNTY].copy()
    if ntpc.empty:
        raise ValueError(f"ODRP117民國{roc_year}找不到{COUNTY}資料")
    bad_codes = sorted(
        set(ntpc.loc[ntpc["area_code"] != SOURCE_CITY_CODE, "area_code"])
    )
    if bad_codes:
        raise ValueError(
            f"ODRP117民國{roc_year}新北市列出現錯誤行政區代碼：{bad_codes}"
        )
    ntpc["_gender"] = ntpc["sex"].map(GENDER_MAP)
    if ntpc["_gender"].isna().any():
        raise ValueError(
            f"ODRP117民國{roc_year}出現未支援性別代碼："
            f"{sorted(set(ntpc.loc[ntpc['_gender'].isna(), 'sex']))}"
        )
    return ntpc


def _validate_period(frame, roc_year):
    values = set(frame["period_year"])
    if values != {str(roc_year)}:
        raise ValueError(f"ODRP117民國{roc_year}統計年度不一致：{sorted(values)}")


def _expected_cells():
    return {
        (gender, age, age if age < 100 else None)
        for gender in ("male", "female")
        for age in range(101)
    }


def _cell_status(selected, roc_year):
    actual = set()
    for _, record in selected.iterrows():
        lower, upper = _parse_age(record["age"])
        actual.add((record["_gender"], lower, upper))
    expected = _expected_cells()
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if extra:
        raise ValueError(f"ODRP117民國{roc_year}出現未預期年齡／性別格：{extra}")
    known = KNOWN_INCOMPLETE_PERIODS.get(roc_year)
    expected_missing = known["missing_cells"] if known else []
    expected_missing_set = {
        (item["gender"], item["age_lower"]) for item in expected_missing
    }
    missing_key_set = {(gender, lower) for gender, lower, _ in missing}
    if missing:
        if known is None or missing_key_set != expected_missing_set:
            raise ValueError(
                f"ODRP117民國{roc_year}來源缺格：{missing}；未登記的缺漏不得靜默入庫。"
            )
        print(
            f"ODRP117民國{roc_year}已知缺漏 =========== "
            f"{known['reason']} missing={missing}"
        )
    elif known is not None:
        raise ValueError("ODRP117已登記的來源缺格本次已補齊，請移除舊守門設定")
    return missing


def _check_scale(period_values):
    kept = {
        year: value
        for year, value in period_values.items()
        if year not in KNOWN_BAD_YEARS
    }
    if len(kept) < 2:
        print("ODRP117 scale guard =========== not applicable (<2 comparable years)")
        return
    for year, value in sorted(kept.items()):
        peers = [peer for other, peer in kept.items() if other != year and peer > 0]
        if value <= 0 or not peers:
            continue
        median = statistics.median(peers)
        if (
            value > median * SCALE_ANOMALY_FACTOR
            or value * SCALE_ANOMALY_FACTOR < median
        ):
            raise ValueError(
                f"ODRP117民國{year}尺度異常：{value:.6g}，"
                f"其餘年度中位數{median:.6g}超過{SCALE_ANOMALY_FACTOR}倍"
            )
    print(f"ODRP117 scale guard =========== passed {sorted(kept)}")


def drop_scope_anomalies(year_records):
    period_values = {}
    for roc_year, records in year_records:
        if roc_year in KNOWN_BAD_YEARS:
            continue
        frame = _normalise_frame(records)
        _validate_period(frame, roc_year)
        selected = _select_ntpc(frame, roc_year)
        _cell_status(selected, roc_year)
        values = [
            _number(value, f"ODRP117民國{roc_year}死亡率")
            for value in selected["death_rate"]
        ]
        # 只取年度率的中位數作範圍異常診斷，不把率相加成任何政策數字。
        period_values[roc_year] = statistics.median(values)
    _check_scale(period_values)
    return year_records


def _request_json(roc_year, page):
    import time
    from http.client import HTTPException
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    url = API.format(dataset=DATASET, year=roc_year) + "?" + urlencode({"PAGE": page})
    last_error = None
    for attempt in range(3):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Connection": "close",
                },
            )
            with urlopen(request, timeout=180) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8-sig"))
        except (HTTPException, OSError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(
        f"ODRP117民國{roc_year}第{page}頁讀取失敗，重試三次仍失敗：{url}"
    ) from last_error


def fetch_year_records(roc_year):
    records = []
    page = 1
    while True:
        body = _request_json(roc_year, page)
        code = body.get("responseCode")
        if code == "OD-0102-S":
            if page == 1:
                return []
            raise RuntimeError(f"ODRP117民國{roc_year}第{page}頁意外查無資料")
        if code != "OD-0101-S":
            raise RuntimeError(
                f"ODRP117民國{roc_year}第{page}頁回應碼異常："
                f"{code} {body.get('responseMessage')}"
            )
        page_records = body.get("responseData") or []
        if not page_records:
            raise RuntimeError(f"ODRP117民國{roc_year}第{page}頁成功但沒有資料")
        records.extend(page_records)
        total_page = int(body.get("totalPage") or 1)
        if page >= total_page:
            return records
        page += 1


def discover_year_records():
    available = []
    seen_data = False
    empty_after_data = 0
    probes = []
    for roc_year in range(FIRST_PROBE_YEAR, MAX_PROBE_YEAR + 1):
        records = fetch_year_records(roc_year)
        probes.append(
            {"year": roc_year, "has_data": bool(records), "rows": len(records)}
        )
        if records:
            available.append((roc_year, records))
            seen_data = True
            empty_after_data = 0
        elif seen_data:
            empty_after_data += 1
            if empty_after_data >= 2:
                break
    if not available:
        raise RuntimeError(f"ODRP117探測不到資料：{probes}")
    print(f"ODRP117 year probes =========== {probes}")
    print(f"ODRP117 available years =========== {[year for year, _ in available]}")
    return available


def _status_note(roc_year, missing):
    if not missing:
        return {}
    return {
        "source_incomplete": True,
        "missing_cells": [
            {"gender": gender, "age_lower": lower} for gender, lower, _ in missing
        ],
    }


def build_ready_data(year_records, data_time):
    import pandas as pd

    rows = []
    reconciliations = []
    for roc_year, records in year_records:
        if roc_year in KNOWN_BAD_YEARS:
            continue
        frame = _normalise_frame(records)
        _validate_period(frame, roc_year)
        selected = _select_ntpc(frame, roc_year)
        missing = _cell_status(selected, roc_year)
        note = _status_note(roc_year, missing)
        expected = {}
        emitted = {}
        year = roc_year + 1911
        for _, record in selected.iterrows():
            lower, upper = _parse_age(record["age"])
            gender = record["_gender"]
            key = (gender, lower, upper)
            if key in expected:
                raise ValueError(f"ODRP117民國{roc_year}重複來源格：{key}")
            value = _number(
                record["death_rate"],
                f"ODRP117民國{roc_year} {gender} {record['age']}歲死亡率",
            )
            expected[key] = value
            emitted[key] = value
            rows.append(
                {
                    "indicator_id": "death_rate_by_single_age",
                    "period_start": f"{year}-01-01",
                    "period_end": f"{year}-12-31",
                    "period_type": "year",
                    "age_lower": lower,
                    "age_upper": upper,
                    "age_band_raw": _normalise_key(record["age"]),
                    "gender": gender,
                    "area_code": CITY_CODE,
                    "area_level": "city",
                    "breakdown": json.dumps(
                        {
                            "source_dataset": DATASET,
                            "source_age_label": _normalise_key(record["age"]),
                            "source_sex": _normalise_key(record["sex"]),
                            "count_basis": _normalise_key(record["according"]),
                            "denominator": "同年同縣市同性別同年齡人口",
                            "youth_18_35_status": (
                                "exact"
                                if lower >= 18 and upper is not None and upper <= 35
                                else "unavailable"
                            ),
                            "note": "率不可相加；100為來源最後一個100歲以上開放組",
                            **note,
                        },
                        ensure_ascii=False,
                    ),
                    "value": value,
                    "unit": "‰（每千人）",
                    "value_type": "rate",
                    "data_time": data_time,
                }
            )
        if expected != emitted:
            raise ValueError(f"ODRP117民國{roc_year}來源格與輸出格不一致")
        reconciliations.append(
            {
                "roc_year": roc_year,
                "source_rows": len(selected),
                "emitted_rows": len(emitted),
                "source_rate_cells": len(expected),
                "emitted_rate_cells": len(emitted),
                "max_cell_delta": 0.0,
                "missing_cells": [
                    {"gender": gender, "age_lower": lower}
                    for gender, lower, _ in missing
                ],
                "passed": True,
            }
        )
        print(
            f"ODRP117 民國{roc_year} reconciliation =========== "
            f"source_rows={len(selected)}, emitted_rows={len(emitted)}, "
            f"missing_cells={len(missing)}"
        )
    if not rows:
        raise RuntimeError("ODRP117沒有產出任何資料")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(f"事實表欄位不符合契約：{list(data.columns)}")
    for column in CONTRACT_COLUMNS:
        if (
            column not in {"age_lower", "age_upper", "age_band_raw"}
            and data[column].isna().any()
        ):
            raise ValueError(f"契約欄位{column}出現NULL")
    return data, reconciliations


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos")
    year_records = drop_scope_anomalies(discover_year_records())
    data, reconciliations = build_ready_data(
        year_records,
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    for reconciliation in reconciliations:
        print(f"death rate reconciliation =========== {reconciliation}")
    print(f"ready_data shape =========== {data.shape}")
    engine = create_engine(kwargs.get("ready_data_db_uri"))
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos.get("load_behavior"),
        default_table=dag_infos.get("ready_data_default_table"),
    )
    update_lasttime_in_data_to_dataset_info(
        engine,
        dag_infos.get("dag_id"),
        data["data_time"].max(),
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_death_rate_single_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
