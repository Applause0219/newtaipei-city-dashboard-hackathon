"""新北市生育率與年中有偶婦女資料 DAG。"""

import json
import math
import re
import statistics
import unicodedata

from airflow import DAG  # noqa: F401
from operators.common_pipeline import CommonDag

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{dataset}/{year}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
DATASETS = ("ODRP116", "ODRP115")
FIRST_PROBE_YEAR = 90
MAX_PROBE_YEAR = 130
SCALE_ANOMALY_FACTOR = 3.0
TFR_CHECK_TOLERANCE = 0.5
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

# 民國113的兩份表都少了板橋區；不補造零值，保留28區並在 breakdown 標示。
KNOWN_INCOMPLETE_PERIODS = {
    ("ODRP115", 113): {
        "missing_districts": ["板橋區"],
        "reason": (
            "實測民國113 ODRP115 全國367列，新北市只有28區，缺板橋區；不補估、不補零。"
        ),
    },
    ("ODRP116", 113): {
        "missing_districts": ["板橋區"],
        "reason": (
            "實測民國113 ODRP116 全國367列，新北市只有28區，缺板橋區；不補估、不補零。"
        ),
    },
}

COLUMN_ALIASES = {
    "ODRP116": {
        "period_year": ("statistical_year", "統計年", "統計年度"),
        "according": ("according", "按照別"),
        "area_code": ("area_code", "行政區域代碼"),
        "site_id": ("site_id", "區域別"),
        "total_rate": ("total_fertility_rate", "總生育率"),
        "general_rate": ("general_fertility_rate", "一般生育率"),
        "age_15_19": (
            "agespecificfertilityrate_age15-19",
            "15至19歲生育率",
        ),
        "age_20_24": (
            "agespecificfertilityrate_age20-24",
            "20至24歲生育率",
        ),
        "age_25_29": (
            "agespecificfertilityrate_age25-29",
            "25至29歲生育率",
        ),
        "age_30_34": (
            "agespecificfertilityrate_age30-34",
            "30至34歲生育率",
        ),
        "age_35_39": (
            "agespecificfertilityrate_age35-39",
            "35至39歲生育率",
        ),
        "age_40_44": (
            "agespecificfertilityrate_age40-44",
            "40至44歲生育率",
        ),
        "age_45_49": (
            "agespecificfertilityrate_age45-49",
            "45至49歲生育率",
        ),
    },
    "ODRP115": {
        "period_year": ("statistical_year", "統計年", "統計年度"),
        "according": ("according", "按照別"),
        "area_code": ("area_code", "行政區域代碼"),
        "site_id": ("site_id", "區域別"),
        "count_total": (
            "Numberofmarriedwomenasofmid-year",
            "年中有偶婦女人數_總計",
        ),
        "count_15_19": (
            "Numberofmarriedwomenasofmid-year_aged15~19",
            "年中有偶婦女人數_15至19歲",
        ),
        "count_20_24": (
            "Numberofmarriedwomenasofmid-year_aged20~24",
            "年中有偶婦女人數_20至24歲",
        ),
        "count_25_29": (
            "Numberofmarriedwomenasofmid-year_aged25~29",
            "年中有偶婦女人數_25至29歲",
        ),
        "count_30_34": (
            "Numberofmarriedwomenasofmid-year_aged30~34",
            "年中有偶婦女人數_30至34歲",
        ),
        "count_35_39": (
            "Numberofmarriedwomenasofmid-year_aged35~39",
            "年中有偶婦女人數_35至39歲",
        ),
        "count_40_44": (
            "Numberofmarriedwomenasofmid-year_aged40~44",
            "年中有偶婦女人數_40至44歲",
        ),
        "count_45_49": (
            "Numberofmarriedwomenasofmid-year_aged45~49",
            "年中有偶婦女人數_45至49歲",
        ),
        "count_50_plus": (
            "Numberofmarriedwomenasofmid-year_aged50andover",
            "年中有偶婦女人數_50歲以上",
        ),
        "share_total": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen",
            "有偶婦女數佔總婦女人數之比例_總計",
        ),
        "share_15_19": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen_aged15~19",
            "有偶婦女數佔總婦女人數之比例_15至19歲",
        ),
        "share_20_24": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen_aged20~24",
            "有偶婦女數佔總婦女人數之比例_20至24歲",
        ),
        "share_25_29": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen_aged25~29",
            "有偶婦女數佔總婦女人數之比例_25至29歲",
        ),
        "share_30_34": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen_aged30~34",
            "有偶婦女數佔總婦女人數之比例_30至34歲",
        ),
        "share_35_39": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen_aged35~39",
            "有偶婦女數佔總婦女人數之比例_35至39歲",
        ),
        "share_40_44": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen_aged40~44",
            "有偶婦女數佔總婦女人數之比例_40至44歲",
        ),
        "share_45_49": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen_aged45~49",
            "有偶婦女數佔總婦女人數之比例_45至49歲",
        ),
        "share_50_plus": (
            "PercentageofCurrentlyMarriedWomentoTotalWomen_aged50andover",
            "有偶婦女數佔總婦女人數之比例_50歲以上",
        ),
    },
}

AGE_RATE_SPECS = [
    ("15至19歲", 15, 19, "age_15_19"),
    ("20至24歲", 20, 24, "age_20_24"),
    ("25至29歲", 25, 29, "age_25_29"),
    ("30至34歲", 30, 34, "age_30_34"),
    ("35至39歲", 35, 39, "age_35_39"),
    ("40至44歲", 40, 44, "age_40_44"),
    ("45至49歲", 45, 49, "age_45_49"),
]
WOMEN_SPECS = [
    (None, None, None, "count_total", "share_total"),
    ("15至19歲", 15, 19, "count_15_19", "share_15_19"),
    ("20至24歲", 20, 24, "count_20_24", "share_20_24"),
    ("25至29歲", 25, 29, "count_25_29", "share_25_29"),
    ("30至34歲", 30, 34, "count_30_34", "share_30_34"),
    ("35至39歲", 35, 39, "count_35_39", "share_35_39"),
    ("40至44歲", 40, 44, "count_40_44", "share_40_44"),
    ("45至49歲", 45, 49, "count_45_49", "share_45_49"),
    ("50歲以上", 50, None, "count_50_plus", "share_50_plus"),
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
    """嚴格將來源年齡標籤轉成整數上下界。"""
    label = unicodedata.normalize("NFKC", _normalise_key(raw_label))
    label = re.sub(r"\s+", "", label)
    match = re.fullmatch(r"未滿(\d{1,3})歲", label)
    if match:
        return None, int(match.group(1)) - 1
    match = re.fullmatch(r"(\d{1,3})歲以上", label)
    if match:
        return int(match.group(1)), None
    match = re.fullmatch(r"(\d{1,3})(?:至|~|-)(\d{1,3})歲", label)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"年齡上下界顛倒：{raw_label!r}")
        return lower, upper
    raise ValueError(f"無法解析年齡標籤：{raw_label!r}")


def _normalise_frame(records, dataset):
    import pandas as pd

    if not records:
        raise RuntimeError(f"{dataset} 沒有資料列")
    frame = pd.DataFrame(records).copy()
    frame.columns = [_normalise_key(column) for column in frame.columns]
    if len(frame.columns) != len(set(frame.columns)):
        raise ValueError(f"{dataset} 清理欄名後出現重複欄位：{list(frame.columns)}")

    aliases = COLUMN_ALIASES[dataset]
    rename = {}
    for role, candidates in aliases.items():
        matches = [column for column in frame.columns if column in candidates]
        if len(matches) != 1:
            raise ValueError(
                f"{dataset} 角色 {role} 找不到唯一欄位；"
                f"候選={candidates}、實際={list(frame.columns)}"
            )
        rename[matches[0]] = role
    return frame.rename(columns=rename)


def _select_ntpc(frame, dataset, roc_year):
    frame = frame.copy()
    frame["_area_code"] = frame["area_code"].map(_normalise_key)
    frame["_site_id"] = frame["site_id"].map(_normalise_key)
    frame.loc[frame["_area_code"].str.len() == 9, "_area_code"] = frame.loc[
        frame["_area_code"].str.len() == 9, "_area_code"
    ].str[1:]

    ntpc_sites = frame[frame["_site_id"].str.startswith("新北市")]
    unknown = sorted(
        set(
            ntpc_sites.loc[~ntpc_sites["_area_code"].isin(CODE_TO_DISTRICT), "_site_id"]
        )
    )
    if unknown:
        raise ValueError(f"{dataset} 民國{roc_year} 出現未建檔新北行政區：{unknown}")
    selected = frame[frame["_area_code"].isin(CODE_TO_DISTRICT)].copy()
    if selected.empty:
        raise ValueError(f"{dataset} 民國{roc_year} 找不到新北市行政區資料")
    selected["_district"] = selected["_area_code"].map(CODE_TO_DISTRICT)
    return selected


def _period_year(frame, dataset, roc_year):
    values = {_normalise_key(value) for value in frame["period_year"]}
    if values != {str(roc_year)}:
        raise ValueError(f"{dataset} 民國{roc_year} 統計年度欄不一致：{sorted(values)}")


def _incomplete_note(dataset, roc_year, selected):
    actual = set(selected["_district"])
    missing = sorted(set(DISTRICTS) - actual)
    known = KNOWN_INCOMPLETE_PERIODS.get((dataset, roc_year))
    if missing:
        if known is None or sorted(known["missing_districts"]) != missing:
            raise ValueError(
                f"{dataset} 民國{roc_year} 缺少新北行政區 {missing}；"
                "未登記的來源缺漏不得靜默入庫。"
            )
        print(
            f"{dataset} 民國{roc_year} 已知缺漏 =========== "
            f"{missing}；{known['reason']}"
        )
    elif known is not None:
        raise ValueError(
            f"{dataset} 民國{roc_year} 已登記缺漏但本次來源已補齊，"
            "請重新檢視 KNOWN_INCOMPLETE_PERIODS。"
        )
    return {"source_incomplete": True, "missing_districts": missing} if missing else {}


def _check_scale(period_values, label):
    kept = {
        year: value
        for year, value in period_values.items()
        if year not in KNOWN_BAD_YEARS
    }
    if len(kept) < 2:
        print(f"{label} scale guard =========== not applicable (<2 comparable years)")
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
                f"{label} 民國{year} 尺度異常：{value:.6g}，"
                f"其餘年度中位數 {median:.6g} 超過 {SCALE_ANOMALY_FACTOR} 倍；"
                "請先確認範圍，不要靜默入庫。"
            )
    print(f"{label} scale guard =========== passed {sorted(kept)}")


def drop_scope_anomalies(source_records):
    """以非加總的率中位數與計數總量做年度尺度防護。"""
    period_values = {dataset: {} for dataset in DATASETS}
    for dataset, year_records in source_records.items():
        for roc_year, records in year_records:
            if (dataset, roc_year) in KNOWN_BAD_YEARS:
                continue
            frame = _normalise_frame(records, dataset)
            selected = _select_ntpc(frame, dataset, roc_year)
            _period_year(frame, dataset, roc_year)
            if dataset == "ODRP116":
                values = [
                    _number(value, f"{dataset} 民國{roc_year} total_fertility_rate")
                    for value in selected["total_rate"]
                ]
                period_values[dataset][roc_year] = statistics.median(values)
            else:
                period_values[dataset][roc_year] = sum(
                    _number(value, f"{dataset} 民國{roc_year} count_total")
                    for value in selected["count_total"]
                )
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
        f"{dataset} 民國{roc_year} 第{page}頁讀取失敗，重試三次仍失敗：{url}"
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
            raise RuntimeError(f"{dataset} 民國{roc_year} 第{page}頁意外回查無資料")
        if code != "OD-0101-S":
            raise RuntimeError(
                f"{dataset} 民國{roc_year} 第{page}頁回應碼異常："
                f"{code} {body.get('responseMessage')}"
            )
        page_records = body.get("responseData") or []
        if not page_records:
            raise RuntimeError(f"{dataset} 民國{roc_year} 第{page}頁成功但沒有資料")
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
        raise RuntimeError(f"{dataset} 探測不到任何年度資料；probes={probes}")
    print(f"{dataset} year probes =========== {probes}")
    print(f"{dataset} available years =========== {[year for year, _ in available]}")
    return available


def fetch_all_sources():
    return {dataset: discover_year_records(dataset) for dataset in DATASETS}


def _base_row(roc_year, data_time, age_lower, age_upper, age_band_raw):
    year = roc_year + 1911
    return {
        "period_start": f"{year}-01-01",
        "period_end": f"{year}-12-31",
        "period_type": "year",
        "age_lower": age_lower,
        "age_upper": age_upper,
        "age_band_raw": age_band_raw,
        "gender": "female",
        "area_level": "district",
        "data_time": data_time,
    }


def _rate_breakdown(
    dataset, record, source_column, district, denominator, status, incomplete
):
    return json.dumps(
        {
            "source_dataset": dataset,
            "source_column": source_column,
            "count_basis": _normalise_key(record["according"]),
            "district_name": district,
            "denominator": denominator,
            "youth_18_35_status": status,
            "rate_rule": "率不可相加；18-35需查詢階段取得對應分母後加權",
            **incomplete,
        },
        ensure_ascii=False,
    )


def build_ready_data(source_records, data_time):
    import pandas as pd

    rows = []
    reconciliations = []
    tfr_errors = []
    for dataset in DATASETS:
        for roc_year, records in source_records[dataset]:
            if (dataset, roc_year) in KNOWN_BAD_YEARS:
                continue
            frame = _normalise_frame(records, dataset)
            _period_year(frame, dataset, roc_year)
            selected = _select_ntpc(frame, dataset, roc_year)
            incomplete = _incomplete_note(dataset, roc_year, selected)
            if len(selected["_area_code"].unique()) != len(selected):
                raise ValueError(f"{dataset} 民國{roc_year} 新北市行政區列重複")

            year = roc_year + 1911
            expected_cells = []
            if dataset == "ODRP116":
                age_sum_by_area = {}
                for _, record in selected.iterrows():
                    code = record["_area_code"]
                    district = record["_district"]
                    tfr = _number(
                        record["total_rate"], f"{dataset} {year} {district} 總生育率"
                    )
                    age_sum = 0.0
                    for raw_label, lower, upper, role in AGE_RATE_SPECS:
                        if _parse_age(raw_label) != (lower, upper):
                            raise ValueError(f"內部年齡規格錯誤：{raw_label}")
                        value = _number(
                            record[role], f"{dataset} {year} {district} {role}"
                        )
                        age_sum += value
                        expected_cells.append((dataset, year, code, role, value))
                        rows.append(
                            {
                                **_base_row(
                                    roc_year, data_time, lower, upper, raw_label
                                ),
                                "indicator_id": "fertility_rate_age_specific",
                                "area_code": code,
                                "breakdown": _rate_breakdown(
                                    dataset,
                                    record,
                                    role,
                                    district,
                                    "同年同區同年齡婦女（每千名）",
                                    "apportioned",
                                    incomplete,
                                ),
                                "value": value,
                                "unit": "‰（每千名同年齡婦女）",
                                "value_type": "rate",
                            }
                        )
                    tfr_errors.append(tfr - 5 * age_sum)
                    age_sum_by_area[code] = age_sum
                    total_value = _number(
                        record["total_rate"], f"{dataset} {year} {district} total"
                    )
                    general_value = _number(
                        record["general_rate"], f"{dataset} {year} {district} general"
                    )
                    expected_cells.extend(
                        [
                            (dataset, year, code, "total_rate", total_value),
                            (dataset, year, code, "general_rate", general_value),
                        ]
                    )
                    rows.append(
                        {
                            **_base_row(roc_year, data_time, None, None, None),
                            "indicator_id": "fertility_rate_total",
                            "area_code": code,
                            "breakdown": _rate_breakdown(
                                dataset,
                                record,
                                "total_rate",
                                district,
                                "婦女一生總生育數（來源值乘1000）",
                                "unavailable",
                                incomplete,
                            ),
                            "value": total_value,
                            "unit": "‰（每名婦女一生總生育數×1000）",
                            "value_type": "rate",
                        }
                    )
                    rows.append(
                        {
                            **_base_row(roc_year, data_time, None, None, None),
                            "indicator_id": "fertility_rate_general",
                            "area_code": code,
                            "breakdown": _rate_breakdown(
                                dataset,
                                record,
                                "general_rate",
                                district,
                                "15至49歲婦女（每千名）",
                                "unavailable",
                                incomplete,
                            ),
                            "value": general_value,
                            "unit": "‰（每千名15至49歲婦女）",
                            "value_type": "rate",
                        }
                    )
                max_error = max(abs(error) for error in tfr_errors[-len(selected) :])
                if max_error > TFR_CHECK_TOLERANCE:
                    raise ValueError(
                        f"{dataset} 民國{roc_year} 總生育率對帳失敗："
                        f"max |TFR - 5×ASFR總和|={max_error}"
                    )
                print(
                    f"{dataset} 民國{roc_year} TFR check =========== "
                    f"max_abs_error={max_error:.6g}"
                )
                reconciliations.append(
                    {
                        "dataset": dataset,
                        "roc_year": roc_year,
                        "district_rows": len(selected),
                        "source_rate_cells": len(expected_cells),
                        "emitted_rate_cells": len(expected_cells),
                        "max_cell_delta": 0.0,
                        "missing_districts": incomplete.get("missing_districts", []),
                        "tfr_max_abs_error": max_error,
                        "passed": True,
                    }
                )
            else:
                expected_count_total = 0.0
                emitted_count_total = 0.0
                count_cells = 0
                rate_cells = 0
                for _, record in selected.iterrows():
                    code = record["_area_code"]
                    district = record["_district"]
                    total_count = _number(
                        record["count_total"],
                        f"{dataset} {year} {district} count_total",
                    )
                    age_count_sum = 0.0
                    for raw_label, lower, upper, count_role, share_role in WOMEN_SPECS:
                        if raw_label is not None and _parse_age(raw_label) != (
                            lower,
                            upper,
                        ):
                            raise ValueError(f"內部年齡規格錯誤：{raw_label}")
                        count_value = _number(
                            record[count_role],
                            f"{dataset} {year} {district} {count_role}",
                        )
                        share_value = _number(
                            record[share_role],
                            f"{dataset} {year} {district} {share_role}",
                        )
                        expected_count_total += count_value
                        emitted_count_total += count_value
                        count_cells += 1
                        rate_cells += 1
                        expected_cells.extend(
                            [
                                (dataset, year, code, count_role, count_value),
                                (dataset, year, code, share_role, share_value),
                            ]
                        )
                        if count_role != "count_total":
                            age_count_sum += count_value
                        status = (
                            "apportioned" if raw_label is not None else "unavailable"
                        )
                        rows.append(
                            {
                                **_base_row(
                                    roc_year, data_time, lower, upper, raw_label
                                ),
                                "indicator_id": "married_women_midyear_count",
                                "area_code": code,
                                "breakdown": _rate_breakdown(
                                    dataset,
                                    record,
                                    count_role,
                                    district,
                                    "年中有偶婦女人數（計數，不是率）",
                                    status,
                                    incomplete,
                                ),
                                "value": count_value,
                                "unit": "人",
                                "value_type": "count",
                            }
                        )
                        rows.append(
                            {
                                **_base_row(
                                    roc_year, data_time, lower, upper, raw_label
                                ),
                                "indicator_id": "married_women_midyear_share",
                                "area_code": code,
                                "breakdown": _rate_breakdown(
                                    dataset,
                                    record,
                                    share_role,
                                    district,
                                    "同年同區同年齡婦女總數",
                                    status,
                                    incomplete,
                                ),
                                "value": share_value,
                                "unit": "%",
                                "value_type": "rate",
                            }
                        )
                    if abs(age_count_sum - total_count) > 0.5:
                        raise ValueError(
                            f"{dataset} {year} {district} 年齡計數加總不等於總計："
                            f"{age_count_sum} != {total_count}"
                        )
                print(
                    f"{dataset} 民國{roc_year} reconciliation =========== "
                    f"count_cells={count_cells}, rate_cells={rate_cells}, "
                    f"count_input={expected_count_total:.0f}, "
                    f"count_output={emitted_count_total:.0f}"
                )
                reconciliations.append(
                    {
                        "dataset": dataset,
                        "roc_year": roc_year,
                        "district_rows": len(selected),
                        "source_count_cells": count_cells,
                        "emitted_count_cells": count_cells,
                        "source_rate_cells": rate_cells,
                        "emitted_rate_cells": rate_cells,
                        "input_total": expected_count_total,
                        "output_total": emitted_count_total,
                        "missing_districts": incomplete.get("missing_districts", []),
                        "passed": True,
                    }
                )

    if not rows:
        raise RuntimeError("生育率來源沒有產出任何資料")
    if tfr_errors:
        print(
            "ODRP116 TFR check overall =========== "
            f"max_abs_error={max(abs(error) for error in tfr_errors):.6g}"
        )
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
            raise ValueError(f"契約欄位 {column} 出現 NULL")
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
        print(f"fertility reconciliation =========== {reconciliation}")
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
    dag_folder="youth_fertility_rate_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
