"""新北市嬰兒出生數按生父年齡的 DAG。"""

import json
import math
import re
import statistics
import unicodedata

from airflow import DAG  # noqa: F401
from operators.common_pipeline import CommonDag

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{dataset}/{year}"
DATASETS = ("ODRP064", "ODRP065")
FIRST_PROBE_YEAR = 90
MAX_PROBE_YEAR = 130
SCALE_ANOMALY_FACTOR = 3.0
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)

# ODRP064民國114回傳截斷的非法JSON（responseData只到部分行政區），不入庫。
KNOWN_BAD_YEARS = {
    ("ODRP064", 114): (
        "API回傳截斷JSON：responseData開啟陣列但結尾缺少]，"
        "且只回傳部分行政區；不把可修補的半份資料當完整年度。"
    ),
}
# ODRP065民國111是正式的OD-0102-S查無資料，不是尺度異常，也不補零。
KNOWN_NO_DATA_YEARS = {
    ("ODRP065", 111): "實測HTTP 200 + OD-0102-S，來源沒有該年度資料。",
}
KNOWN_INCOMPLETE_PERIODS = {
    ("ODRP064", 113): {
        "missing_cells": [
            {
                "area_code": "65000010",
                "baby_gender": "male",
                "father_age": "未滿20歲",
                "mother_age": "未滿15歲",
            }
        ],
        "reason": (
            "實測民國113新北交叉表5,219格而非5,220格，"
            "缺板橋男嬰、生父未滿20歲／生母未滿15歲；不補估、不補零。"
        ),
    },
    ("ODRP065", 113): {
        "missing_cells": [
            {
                "area_code": "65000010",
                "baby_gender": "male",
                "father_age": "未滿20歲",
                "mother_age": "未滿15歲",
            }
        ],
        "reason": (
            "實測民國113新北交叉表5,219格而非5,220格，"
            "缺板橋男嬰、生父未滿20歲／生母未滿15歲；不補估、不補零。"
        ),
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
CODE_TO_DISTRICT = {code: name for name, code in DISTRICTS.items()}

# 這些規格是來源欄位的語意；實際值也會逐列通過 _parse_age。
FATHER_AGE_SPECS = [
    ("未滿20歲", None, 19),
    ("20～24歲", 20, 24),
    ("25～29歲", 25, 29),
    ("30～34歲", 30, 34),
    ("35～39歲", 35, 39),
    ("40～44歲", 40, 44),
    ("45～49歲", 45, 49),
    ("50～54歲", 50, 54),
    ("55～59歲", 55, 59),
    ("60歲以上", 60, None),
]
MOTHER_AGE_SPECS = [
    ("未滿15歲", None, 14),
    ("15～19歲", 15, 19),
    ("20～24歲", 20, 24),
    ("25～29歲", 25, 29),
    ("30～34歲", 30, 34),
    ("35～39歲", 35, 39),
    ("40～44歲", 40, 44),
    ("45～49歲", 45, 49),
    ("50歲以上", 50, None),
]
BABY_GENDER_MAP = {"男": "male", "女": "female", "male": "male", "female": "female"}

COLUMN_ALIASES = {
    "ODRP064": {
        "period_year": ("statistic_yyy", "統計年度", "統計年"),
        "according": ("according", "按照別"),
        "area_code": ("district_code", "行政區域代碼"),
        "site_id": ("site_id", "區域別"),
        "baby_gender": ("sex", "嬰兒性別"),
        "father_age": ("father_age", "生父年齡"),
        "mother_age": ("mother_age", "生母年齡"),
        "birth_count": ("birth_count", "嬰兒出生數"),
    },
    "ODRP065": {
        "period_year": ("statistic_yyy", "統計年度", "統計年"),
        "according": ("according", "按照別"),
        "area_code": ("district_code", "區域別代碼", "行政區域代碼"),
        "site_id": ("site_id", "區域別"),
        "baby_gender": ("sex", "嬰兒性別"),
        "father_age": ("father_age", "生父年齡"),
        "mother_age": ("mother_age", "生母年齡"),
        "birth_count": ("birth_count", "嬰兒出生數"),
    },
}


def _normalise_key(value):
    text = "" if value is None else str(value)
    text = text.replace("\ufeff", "").strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _number(value, context):
    raw = _normalise_key(value).replace(",", "")
    if not raw:
        raise ValueError(f"{context}為空，不能靜默當成0")
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"{context}不是數值：{value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{context}不是非負有限數值：{value!r}")
    return number


def _parse_age(raw_label):
    label = unicodedata.normalize("NFKC", _normalise_key(raw_label))
    label = re.sub(r"\s+", "", label)
    match = re.fullmatch(r"未滿(\d{1,3})歲", label)
    if match:
        return None, int(match.group(1)) - 1
    match = re.fullmatch(r"(\d{1,3})(?:至|~|-)(\d{1,3})歲", label)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"生父／生母年齡上下界顛倒：{raw_label!r}")
        return lower, upper
    match = re.fullmatch(r"(\d{1,3})歲以上", label)
    if match:
        return int(match.group(1)), None
    raise ValueError(f"無法解析生父／生母年齡標籤：{raw_label!r}")


def _normalise_frame(records, dataset):
    import pandas as pd

    if not records:
        raise RuntimeError(f"{dataset}沒有資料列")
    frame = pd.DataFrame(records).copy()
    frame.columns = [_normalise_key(column) for column in frame.columns]
    if len(frame.columns) != len(set(frame.columns)):
        raise ValueError(f"{dataset}清理欄名後出現重複欄位：{list(frame.columns)}")
    rename = {}
    for role, candidates in COLUMN_ALIASES[dataset].items():
        matches = [column for column in frame.columns if column in candidates]
        if len(matches) != 1:
            raise ValueError(
                f"{dataset}角色{role}找不到唯一欄位；"
                f"候選={candidates}、實際={list(frame.columns)}"
            )
        rename[matches[0]] = role
    return frame.rename(columns=rename)


def _select_ntpc(frame, dataset, roc_year):
    frame = frame.copy()
    for column in (
        "period_year",
        "according",
        "area_code",
        "site_id",
        "baby_gender",
        "father_age",
        "mother_age",
    ):
        frame[column] = frame[column].map(_normalise_key)
    frame["_area_code"] = frame["area_code"].map(
        lambda value: value[1:] if len(value) == 9 and value.startswith("0") else value
    )
    ntpc_sites = frame[frame["site_id"].str.startswith("新北市")]
    unknown = sorted(
        set(ntpc_sites.loc[~ntpc_sites["_area_code"].isin(CODE_TO_DISTRICT), "site_id"])
    )
    if unknown:
        raise ValueError(f"{dataset}民國{roc_year}出現未建檔新北行政區：{unknown}")
    selected = frame[frame["_area_code"].isin(CODE_TO_DISTRICT)].copy()
    if selected.empty:
        raise ValueError(f"{dataset}民國{roc_year}找不到新北市行政區資料")
    selected["_district"] = selected["_area_code"].map(CODE_TO_DISTRICT)
    return selected


def _validate_period(frame, dataset, roc_year):
    values = set(frame["period_year"])
    if values != {str(roc_year)}:
        raise ValueError(f"{dataset}民國{roc_year}統計年度不一致：{sorted(values)}")


def _validate_matrix(selected, dataset, roc_year):
    dimensions = ["_area_code", "baby_gender", "father_age", "mother_age"]
    selected[dimensions] = selected[dimensions].apply(
        lambda column: column.map(_normalise_key).replace("", "未填")
    )
    if (selected[dimensions] == "未填").any().any():
        raise ValueError(f"{dataset}民國{roc_year}必要維度有空值")
    if set(selected["baby_gender"]) - set(BABY_GENDER_MAP):
        raise ValueError(
            f"{dataset}民國{roc_year}出現未支援嬰兒性別："
            f"{sorted(set(selected['baby_gender']) - set(BABY_GENDER_MAP))}"
        )
    actual_father = set(selected["father_age"])
    actual_mother = set(selected["mother_age"])
    expected_father = {label for label, _, _ in FATHER_AGE_SPECS}
    expected_mother = {label for label, _, _ in MOTHER_AGE_SPECS}
    if actual_father != expected_father:
        raise ValueError(
            f"{dataset}民國{roc_year}生父年齡組不完整或改版："
            f"缺={sorted(expected_father - actual_father)}、"
            f"多={sorted(actual_father - expected_father)}"
        )
    if actual_mother != expected_mother:
        raise ValueError(
            f"{dataset}民國{roc_year}生母年齡組不完整或改版："
            f"缺={sorted(expected_mother - actual_mother)}、"
            f"多={sorted(actual_mother - expected_mother)}"
        )
    for raw_label, lower, upper in FATHER_AGE_SPECS + MOTHER_AGE_SPECS:
        if _parse_age(raw_label) != (lower, upper):
            raise ValueError(f"內部年齡規格錯誤：{raw_label}")
    district_count = len(set(selected["_area_code"]))
    if district_count != len(DISTRICTS):
        raise ValueError(f"{dataset}民國{roc_year}新北行政區不是29區：{district_count}")
    selected["_baby_gender"] = selected["baby_gender"].map(BABY_GENDER_MAP)
    actual_cells = {
        (area_code, gender, father_age, mother_age)
        for area_code, gender, father_age, mother_age in selected[
            ["_area_code", "_baby_gender", "father_age", "mother_age"]
        ].itertuples(index=False, name=None)
    }
    expected_unique = len(DISTRICTS) * 2 * 10 * 9
    expected_cells = {
        (area_code, gender, father_age, mother_age)
        for area_code in DISTRICTS.values()
        for gender in ("male", "female")
        for father_age, _, _ in FATHER_AGE_SPECS
        for mother_age, _, _ in MOTHER_AGE_SPECS
    }
    missing_cells = sorted(expected_cells - actual_cells)
    extra_cells = sorted(actual_cells - expected_cells)
    known = KNOWN_INCOMPLETE_PERIODS.get((dataset, roc_year))
    expected_missing = {
        (
            item["area_code"],
            item["baby_gender"],
            item["father_age"],
            item["mother_age"],
        )
        for item in (known["missing_cells"] if known else [])
    }
    if extra_cells or set(missing_cells) != expected_missing:
        raise ValueError(
            f"{dataset}民國{roc_year}交叉表缺格或多格："
            f"missing={missing_cells}, extra={extra_cells}"
        )
    if missing_cells:
        print(
            f"{dataset}民國{roc_year}已知缺漏 =========== "
            f"{known['reason']} missing={missing_cells}"
        )
    elif known is not None:
        raise ValueError("已登記的來源缺格本次已補齊，請移除舊守門設定")
    unique_cells = len(actual_cells)
    if unique_cells != expected_unique - len(expected_missing):
        raise ValueError(
            f"{dataset}民國{roc_year}交叉表格數異常："
            f"{unique_cells} != {expected_unique - len(expected_missing)}"
        )
    return [
        {
            "area_code": area_code,
            "baby_gender": gender,
            "father_age": father_age,
            "mother_age": mother_age,
        }
        for area_code, gender, father_age, mother_age in missing_cells
    ]


def _check_scale(period_values, dataset):
    kept = {
        year: value
        for year, value in period_values.items()
        if (dataset, year) not in KNOWN_BAD_YEARS
    }
    if len(kept) < 2:
        print(f"{dataset} scale guard =========== not applicable (<2 comparable years)")
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
                f"{dataset}民國{year}尺度異常：{value:.6g}，"
                f"其餘年度中位數{median:.6g}超過{SCALE_ANOMALY_FACTOR}倍"
            )
    print(f"{dataset} scale guard =========== passed {sorted(kept)}")


def drop_scope_anomalies(source_records):
    period_values = {dataset: {} for dataset in DATASETS}
    for dataset, year_records in source_records.items():
        for roc_year, records in year_records:
            if (dataset, roc_year) in KNOWN_BAD_YEARS:
                print(
                    f"{dataset} 民國{roc_year} known bad =========== "
                    f"{KNOWN_BAD_YEARS[(dataset, roc_year)]}"
                )
                continue
            frame = _normalise_frame(records, dataset)
            _validate_period(frame, dataset, roc_year)
            selected = _select_ntpc(frame, dataset, roc_year)
            _validate_matrix(selected, dataset, roc_year)
            values = [
                _number(value, f"{dataset}民國{roc_year}出生數")
                for value in selected["birth_count"]
            ]
            period_values[dataset][roc_year] = sum(values)
    for dataset, values in period_values.items():
        _check_scale(values, dataset)
    return source_records


def _request_json(dataset, roc_year, page):
    import time
    from http.client import HTTPException
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    url = API.format(dataset=dataset, year=roc_year) + "?" + urlencode({"PAGE": page})
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
        f"{dataset}民國{roc_year}第{page}頁讀取失敗，重試三次仍失敗：{url}"
    ) from last_error


def fetch_year_records(dataset, roc_year):
    records = []
    page = 1
    while True:
        body = _request_json(dataset, roc_year, page)
        code = body.get("responseCode")
        if code == "OD-0102-S":
            if page == 1:
                return []
            raise RuntimeError(f"{dataset}民國{roc_year}第{page}頁意外查無資料")
        if code != "OD-0101-S":
            raise RuntimeError(
                f"{dataset}民國{roc_year}第{page}頁回應碼異常："
                f"{code} {body.get('responseMessage')}"
            )
        page_records = body.get("responseData") or []
        if not page_records:
            raise RuntimeError(f"{dataset}民國{roc_year}第{page}頁成功但沒有資料")
        records.extend(page_records)
        total_page = int(body.get("totalPage") or 1)
        if page >= total_page:
            return records
        page += 1


def discover_year_records(dataset):
    available = []
    seen_data = False
    empty_after_data = 0
    probes = []
    for roc_year in range(FIRST_PROBE_YEAR, MAX_PROBE_YEAR + 1):
        if (dataset, roc_year) in KNOWN_BAD_YEARS:
            print(f"{dataset}民國{roc_year} skip known bad")
            continue
        records = fetch_year_records(dataset, roc_year)
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
        raise RuntimeError(f"{dataset}探測不到資料：{probes}")
    print(f"{dataset} year probes =========== {probes}")
    print(f"{dataset} available years =========== {[year for year, _ in available]}")
    return available


def fetch_all_sources():
    return {dataset: discover_year_records(dataset) for dataset in DATASETS}


def build_ready_data(source_records, data_time):
    import pandas as pd

    rows = []
    reconciliations = []
    for dataset in DATASETS:
        for roc_year, records in source_records[dataset]:
            if (dataset, roc_year) in KNOWN_BAD_YEARS:
                continue
            frame = _normalise_frame(records, dataset)
            _validate_period(frame, dataset, roc_year)
            selected = _select_ntpc(frame, dataset, roc_year)
            missing_cells = _validate_matrix(selected, dataset, roc_year)
            dimensions = [
                "_area_code",
                "according",
                "baby_gender",
                "father_age",
                "mother_age",
            ]
            selected["_value"] = selected.apply(
                lambda row, _dataset=dataset, _roc_year=roc_year: _number(
                    row["birth_count"],
                    f"{_dataset}民國{_roc_year}{row['_district']}出生數",
                ),
                axis=1,
            )
            grouped = selected.groupby(
                dimensions, dropna=False, sort=True, as_index=False
            )["_value"].sum()
            input_total = float(selected["_value"].sum())
            output_total = float(grouped["_value"].sum())
            emitted_rows = len(grouped)
            emitted_nonzero = grouped[grouped["_value"] != 0].copy()
            if abs(input_total - output_total) > 0.0001:
                raise ValueError(
                    f"{dataset}民國{roc_year}對帳失敗："
                    f"input_total={input_total} output_total={output_total}"
                )
            reconciliation = {
                "dataset": dataset,
                "roc_year": roc_year,
                "source_rows": len(selected),
                "emitted_rows": emitted_rows,
                "emitted_rows_nonzero": len(emitted_nonzero),
                "dropped_zero_rows": emitted_rows - len(emitted_nonzero),
                "input_total": input_total,
                "output_total": output_total,
                "missing_cells": missing_cells,
                "passed_before_zero_filter": True,
            }
            reconciliations.append(reconciliation)
            print(f"birth parent reconciliation =========== {reconciliation}")

            year = roc_year + 1911
            for _, record in emitted_nonzero.iterrows():
                father_raw = _normalise_key(record["father_age"])
                mother_raw = _normalise_key(record["mother_age"])
                father_lower, father_upper = _parse_age(father_raw)
                mother_lower, mother_upper = _parse_age(mother_raw)
                baby_raw = _normalise_key(record["baby_gender"])
                baby_gender = BABY_GENDER_MAP[baby_raw]
                rows.append(
                    {
                        "indicator_id": "birth_count_by_father_age",
                        "period_start": f"{year}-01-01",
                        "period_end": f"{year}-12-31",
                        "period_type": "year",
                        "age_lower": father_lower,
                        "age_upper": father_upper,
                        "age_band_raw": father_raw,
                        # age的主體是生父，不是來源裡的嬰兒性別。
                        "gender": "male",
                        "area_code": record["_area_code"],
                        "area_level": "district",
                        "breakdown": json.dumps(
                            {
                                "source_dataset": dataset,
                                "source_column": "birth_count",
                                "count_basis": _normalise_key(record["according"])
                                if "according" in record
                                else "",
                                "district_name": CODE_TO_DISTRICT[record["_area_code"]],
                                "source_incomplete": bool(missing_cells),
                                "missing_cells": missing_cells,
                                "baby_gender": baby_raw,
                                "baby_gender_normalized": baby_gender,
                                "mother_age_raw": mother_raw,
                                "mother_age_lower": mother_lower,
                                "mother_age_upper": mother_upper,
                                "age_subject": "father",
                                "youth_18_35_status": "apportioned",
                                "note": (
                                    "生父年齡為5歲組／未滿20歲；18-35需在查詢階段分攤，"
                                    "本DAG不固化分攤結果"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        "value": float(record["_value"]),
                        "unit": "人",
                        "value_type": "count",
                        "data_time": data_time,
                    }
                )

    if not rows:
        raise RuntimeError("ODRP064/065沒有產出任何資料")
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
    source_records = drop_scope_anomalies(fetch_all_sources())
    data, reconciliations = build_ready_data(
        source_records,
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    for reconciliation in reconciliations:
        print(f"birth parent reconciliation =========== {reconciliation}")
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
    dag_folder="youth_birth_parent_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
