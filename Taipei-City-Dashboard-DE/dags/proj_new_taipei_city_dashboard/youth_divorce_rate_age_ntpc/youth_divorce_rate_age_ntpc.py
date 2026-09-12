"""新北市有偶人口離婚率（按性別及年齡）DAG。"""

import json
import math
import re
import statistics
import unicodedata

from airflow import DAG  # noqa: F401
from operators.common_pipeline import CommonDag

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{dataset}/{year}"
DATASET = "ODRP118"
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
KNOWN_INCOMPLETE_PERIODS = {
    113: {
        "missing_genders": ["male"],
        "reason": ("實測民國113新北市只有女性1列，缺少男性列；不補估、不補零。"),
    },
}

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

COLUMN_ALIASES = {
    "period_year": ("statistical_year", "統計年", "統計年度"),
    "according": ("according", "按照別"),
    "area_code": ("area_code", "行政區域代碼"),
    "county": ("county", "縣市別"),
    "sex": ("sex", "性別"),
    "rate_total": (
        "divorcerateofcurrentlymarriedpopulation",
        "總計",
    ),
    "rate_under20": (
        "divorcerateofcurrentlymarriedpopulation_under20",
        "20歲以下",
    ),
    "rate_20_24": (
        "divorcerateofthecurrentlymarriedpopulation_aged20to24",
        "20至24歲",
    ),
    "rate_25_29": (
        "divorcerateofthecurrentlymarriedpopulation_aged25to29",
        "25至29歲",
    ),
    "rate_30_34": (
        "divorcerateofthecurrentlymarriedpopulation_aged30to34",
        "30至34歲",
    ),
    "rate_35_39": (
        "divorcerateofthecurrentlymarriedpopulation_aged35to39",
        "35至39歲",
    ),
    "rate_40_44": (
        "divorcerateofthecurrentlymarriedpopulation_aged40to44",
        "40至44歲",
    ),
    "rate_45_49": (
        "divorcerateofthecurrentlymarriedpopulation_aged45to49",
        "45至49歲",
    ),
    "rate_50_54": (
        "divorcerateofthecurrentlymarriedpopulation_aged50to54",
        "50至54歲",
    ),
    "rate_55_59": (
        "divorcerateofthecurrentlymarriedpopulation_aged55to59",
        "55至59歲",
    ),
    "rate_60_64": (
        "divorcerateofthecurrentlymarriedpopulation_aged60to64",
        "60至64歲",
    ),
    "rate_65_plus": (
        "divorcerateofthecurrentlymarriedpopulation_aged65andover",
        "65歲以上",
    ),
}
GENDER_MAP = {"男": "male", "女": "female", "1": "male", "2": "female"}
AGE_SPECS = [
    (None, None, None, "rate_total"),
    ("20歲以下", None, 19, "rate_under20"),
    ("20至24歲", 20, 24, "rate_20_24"),
    ("25至29歲", 25, 29, "rate_25_29"),
    ("30至34歲", 30, 34, "rate_30_34"),
    ("35至39歲", 35, 39, "rate_35_39"),
    ("40至44歲", 40, 44, "rate_40_44"),
    ("45至49歲", 45, 49, "rate_45_49"),
    ("50至54歲", 50, 54, "rate_50_54"),
    ("55至59歲", 55, 59, "rate_55_59"),
    ("60至64歲", 60, 64, "rate_60_64"),
    ("65歲以上", 65, None, "rate_65_plus"),
]


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
    label = unicodedata.normalize("NFKC", _normalise_key(raw_label))
    label = re.sub(r"\s+", "", label)
    if label in {"20歲以下", "未滿20歲"}:
        return None, 19
    match = re.fullmatch(r"(\d{1,3})(?:至|~|-)(\d{1,3})歲", label)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"離婚率年齡上下界顛倒：{raw_label!r}")
        return lower, upper
    match = re.fullmatch(r"(\d{1,3})歲以上", label)
    if match:
        return int(match.group(1)), None
    raise ValueError(f"無法解析ODRP118年齡標籤：{raw_label!r}")


def _normalise_frame(records):
    import pandas as pd

    if not records:
        raise RuntimeError("ODRP118沒有資料列")
    frame = pd.DataFrame(records).copy()
    frame.columns = [_normalise_key(column) for column in frame.columns]
    if len(frame.columns) != len(set(frame.columns)):
        raise ValueError(f"ODRP118清理欄名後出現重複欄位：{list(frame.columns)}")
    rename = {}
    for role, candidates in COLUMN_ALIASES.items():
        matches = [column for column in frame.columns if column in candidates]
        if len(matches) != 1:
            raise ValueError(
                f"ODRP118角色{role}找不到唯一欄位；"
                f"候選={candidates}、實際={list(frame.columns)}"
            )
        rename[matches[0]] = role
    return frame.rename(columns=rename)


def _select_ntpc(frame, roc_year):
    frame = frame.copy()
    for column in ("period_year", "according", "area_code", "county", "sex"):
        frame[column] = frame[column].map(_normalise_key)
    ntpc = frame[frame["county"] == COUNTY].copy()
    if ntpc.empty:
        raise ValueError(f"ODRP118民國{roc_year}找不到{COUNTY}資料")
    bad_codes = sorted(
        set(ntpc.loc[ntpc["area_code"] != SOURCE_CITY_CODE, "area_code"])
    )
    if bad_codes:
        raise ValueError(
            f"ODRP118民國{roc_year}新北市列出現錯誤行政區代碼：{bad_codes}"
        )
    ntpc["_gender"] = ntpc["sex"].map(GENDER_MAP)
    if ntpc["_gender"].isna().any():
        raise ValueError(
            f"ODRP118民國{roc_year}出現未支援性別代碼："
            f"{sorted(set(ntpc.loc[ntpc['_gender'].isna(), 'sex']))}"
        )
    if len(ntpc) != len(set(ntpc["_gender"])):
        raise ValueError(
            f"ODRP118民國{roc_year}新北市性別列重複，實際：{ntpc['_gender'].tolist()}"
        )
    actual_genders = set(ntpc["_gender"])
    missing_genders = sorted({"male", "female"} - actual_genders)
    known = KNOWN_INCOMPLETE_PERIODS.get(roc_year)
    expected_missing = sorted(known["missing_genders"]) if known else []
    if missing_genders:
        if known is None or missing_genders != expected_missing:
            raise ValueError(
                f"ODRP118民國{roc_year}來源缺少性別列：{missing_genders}；"
                "未登記的缺漏不得靜默入庫。"
            )
        print(
            f"ODRP118民國{roc_year}已知缺漏 =========== "
            f"{known['reason']} missing={missing_genders}"
        )
    elif known is not None:
        raise ValueError("ODRP118已登記的來源缺格本次已補齊，請移除舊守門設定")
    return ntpc


def _validate_period(frame, roc_year):
    values = set(frame["period_year"])
    if values != {str(roc_year)}:
        raise ValueError(f"ODRP118民國{roc_year}統計年度不一致：{sorted(values)}")


def _check_scale(period_values):
    kept = {
        year: value
        for year, value in period_values.items()
        if year not in KNOWN_BAD_YEARS
    }
    if len(kept) < 2:
        print("ODRP118 scale guard =========== not applicable (<2 comparable years)")
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
                f"ODRP118民國{year}尺度異常：{value:.6g}，"
                f"其餘年度中位數{median:.6g}超過{SCALE_ANOMALY_FACTOR}倍"
            )
    print(f"ODRP118 scale guard =========== passed {sorted(kept)}")


def drop_scope_anomalies(year_records):
    period_values = {}
    for roc_year, records in year_records:
        if roc_year in KNOWN_BAD_YEARS:
            continue
        frame = _normalise_frame(records)
        _validate_period(frame, roc_year)
        selected = _select_ntpc(frame, roc_year)
        values = []
        for _, record in selected.iterrows():
            for _, _, _, role in AGE_SPECS:
                values.append(_number(record[role], f"ODRP118民國{roc_year} {role}"))
        # 這裡只取率的中位數做範圍診斷，絕不把率相加。
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
        f"ODRP118民國{roc_year}第{page}頁讀取失敗，重試三次仍失敗：{url}"
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
            raise RuntimeError(f"ODRP118民國{roc_year}第{page}頁意外查無資料")
        if code != "OD-0101-S":
            raise RuntimeError(
                f"ODRP118民國{roc_year}第{page}頁回應碼異常："
                f"{code} {body.get('responseMessage')}"
            )
        page_records = body.get("responseData") or []
        if not page_records:
            raise RuntimeError(f"ODRP118民國{roc_year}第{page}頁成功但沒有資料")
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
        raise RuntimeError(f"ODRP118探測不到資料：{probes}")
    print(f"ODRP118 year probes =========== {probes}")
    print(f"ODRP118 available years =========== {[year for year, _ in available]}")
    return available


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
        missing_genders = sorted({"male", "female"} - set(selected["_gender"]))
        expected = {}
        emitted = {}
        year = roc_year + 1911
        for _, record in selected.iterrows():
            gender = record["_gender"]
            for raw_label, lower, upper, role in AGE_SPECS:
                if raw_label is not None and _parse_age(raw_label) != (lower, upper):
                    raise ValueError(f"ODRP118內部年齡規格錯誤：{raw_label}")
                key = (gender, lower, upper)
                if key in expected:
                    raise ValueError(f"ODRP118民國{roc_year}重複來源格：{key}")
                value = _number(
                    record[role],
                    f"ODRP118民國{roc_year} {gender} {role}",
                )
                expected[key] = value
                emitted[key] = value
                rows.append(
                    {
                        "indicator_id": "divorce_rate_currently_married",
                        "period_start": f"{year}-01-01",
                        "period_end": f"{year}-12-31",
                        "period_type": "year",
                        "age_lower": lower,
                        "age_upper": upper,
                        "age_band_raw": raw_label,
                        "gender": gender,
                        "area_code": CITY_CODE,
                        "area_level": "city",
                        "breakdown": json.dumps(
                            {
                                "source_dataset": DATASET,
                                "source_column": role,
                                "source_age_label": raw_label,
                                "source_sex": _normalise_key(record["sex"]),
                                "count_basis": _normalise_key(record["according"]),
                                "denominator": "同年同縣市同性別同年齡有偶人口",
                                "source_incomplete": bool(missing_genders),
                                "missing_genders": missing_genders,
                                "youth_18_35_status": (
                                    "apportioned"
                                    if raw_label is not None
                                    else "unavailable"
                                ),
                                "rate_rule": (
                                    "率不可相加；邊界年齡組不能直接切出18-35，"
                                    "需查詢階段取得分子分母後加權"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        "value": value,
                        "unit": "‰（每千名有偶人口）",
                        "value_type": "rate",
                        "data_time": data_time,
                    }
                )
        if expected != emitted:
            raise ValueError(f"ODRP118民國{roc_year}來源格與輸出格不一致")
        reconciliations.append(
            {
                "roc_year": roc_year,
                "source_rows": len(selected),
                "emitted_rows": len(emitted),
                "source_rate_cells": len(expected),
                "emitted_rate_cells": len(emitted),
                "max_cell_delta": 0.0,
                "missing_genders": missing_genders,
                "passed": True,
            }
        )
        print(
            f"ODRP118 民國{roc_year} reconciliation =========== "
            f"source_rows={len(selected)}, emitted_rows={len(emitted)}, "
            "rate_cellwise=True"
        )
    if not rows:
        raise RuntimeError("ODRP118沒有產出任何資料")
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
        print(f"divorce rate reconciliation =========== {reconciliation}")
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
    dag_folder="youth_divorce_rate_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
