from airflow import DAG
from operators.common_pipeline import CommonDag

__all__ = ["DAG"]

BASE_URL = "https://udb.moe.edu.tw"
STUDENT_URL = "https://stats.moe.gov.tw/files/detail/{year}/{year}_student.csv"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)
CURRENT_DIRECTORIES = (114, 113, 112, 111)
CURRENT_FILENAME = "學12-3.新生(含境外生)註冊率-以「校」統計.csv"
CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]
COUNTY_CODE = {
    "臺北市": "63000", "新北市": "65000", "桃園市": "68000", "臺中市": "66000",
    "臺南市": "67000", "高雄市": "64000", "基隆市": "10017", "新竹市": "10018",
    "嘉義市": "10020", "宜蘭縣": "10002", "新竹縣": "10004", "苗栗縣": "10005",
    "彰化縣": "10007", "南投縣": "10008", "雲林縣": "10009", "嘉義縣": "10010",
    "屏東縣": "10013", "臺東縣": "10014", "花蓮縣": "10015", "澎湖縣": "10016",
    "金門縣": "09020", "連江縣": "09007",
}
MANUAL_SCHOOL_COUNTY_BY_CODE = {
    "1R01": "新北市", "1R02": "臺北市", "1R03": "新北市", "1R04": "高雄市",
    "1R05": "臺北市", "1R06": "南投縣", "1R07": "臺南市", "1R08": "桃園市",
    "1R09": "南投縣", "1R10": "雲林縣",
    "1085": "新北市",
}
KNOWN_BAD_YEARS = frozenset()
CROSSWALK_FIRST_YEAR = 103
CROSSWALK_LAST_YEAR = 114
INDICATOR_ID = "college_freshman_registration_rate"


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "<na>"}:
        return None
    return text


def _normalise_school_code(value):
    text = _clean(value)
    if text is None:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(4) if text.isdigit() else text.upper()


def _normalise_school_name(value):
    text = _clean(value)
    if text is None:
        return None
    return " ".join(text.replace("台", "臺").split())


def _normalise_county(value):
    import re

    text = _clean(value)
    if text is None:
        return None
    text = re.sub(r"^\s*\d+\s*", "", text).replace("台", "臺")
    return {
        "桃園縣": "桃園市", "臺中縣": "臺中市", "臺南縣": "臺南市",
        "高雄縣": "高雄市",
    }.get(text, text)


def _find_first(columns, *musts, exclude=()):
    for column in columns:
        if (
            all(must in str(column) for must in musts)
            and not any(token in str(column) for token in exclude)
        ):
            return column
    return None


def _resolve_columns(columns):
    columns = list(columns)
    roles = {
        "academic_year": _find_first(columns, "學年度"),
        "school_code": (
            _find_first(columns, "學校", "統計處", "代碼")
            or _find_first(columns, "學校", "代碼")
            or _find_first(columns, "學校", "代號")
        ),
        "school_name": _find_first(columns, "學校", "名稱"),
        "establishment": _find_first(columns, "設立別"),
        "school_type": _find_first(columns, "學校類別"),
        "a": _find_first(columns, "(A)"),
        "b": _find_first(columns, "(B)"),
        "c": _find_first(columns, "(C)"),
        "d": (
            _find_first(columns, "(D)")
            or _find_first(columns, "(E)", exclude=("招生率",))
        ),
        "rate": (
            _find_first(columns, "註冊率", "(%)")
            or _find_first(columns, "註冊率", "％")
            or _find_first(columns, "註冊率")
        ),
    }
    missing = [role for role, column in roles.items() if column is None]
    if missing:
        raise ValueError(
            f"註冊率來源缺少必要欄位角色 {missing}；實際欄位：{columns}"
        )
    return roles


def _parse_number(value, field, stats, kind="count"):
    import math

    text = _clean(value)
    if text in {"...", "…", "．．．"}:
        stats["hidden_by_field"][str(field)] = (
            stats["hidden_by_field"].get(str(field), 0) + 1
        )
        return None
    if text is None:
        stats["blank_by_field"][str(field)] = (
            stats["blank_by_field"].get(str(field), 0) + 1
        )
        return None
    if text in {"-", "—", "－"}:
        return 0.0
    try:
        number = float(text.replace(",", "").replace("%", "").strip())
    except ValueError as exc:
        raise ValueError(f"註冊率來源欄位 {field} 無法轉數值：{value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"註冊率來源欄位 {field} 出現無效數值：{value!r}")
    if kind == "rate" and number > 100:
        raise ValueError(f"註冊率超過100%：{field}={value!r}")
    return number


def _academic_period(academic_year):
    start_year = academic_year + 1911
    return f"{start_year}-08-01", f"{start_year + 1}-07-31"


def _fetch_csv(path, requests_module, pd_module, io_module, quote_module):
    url = BASE_URL + quote_module.quote(path, safe="/")
    response = requests_module.get(
        url, headers={"User-Agent": UA}, timeout=180
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return pd_module.read_csv(
        io_module.BytesIO(response.content),
        dtype=str,
        encoding="utf-8-sig",
        keep_default_na=False,
    )


def fetch_source_frames(requests_module, pd_module, io_module, quote_module):
    selected = {}
    for directory in CURRENT_DIRECTORIES:
        path = f"/download/udata/static_file/{directory}/{CURRENT_FILENAME}"
        frame = _fetch_csv(path, requests_module, pd_module, io_module, quote_module)
        if frame is None:
            print(f"registration source missing: static_file/{directory}")
            continue
        year_col = _find_first(frame.columns, "學年度")
        if year_col is None:
            raise ValueError(f"註冊率 CSV 缺少學年度欄：{path}")
        for year_text in sorted(frame[year_col].astype(str).str.strip().unique()):
            year = int(year_text)
            if year not in selected:
                selected[year] = (
                    year,
                    frame[frame[year_col].astype(str).str.strip() == year_text].copy(),
                    f"static_file/{directory}",
                )
    if not selected:
        raise RuntimeError("沒有抓到任何新生註冊率學年度資料")
    return [selected[year] for year in sorted(selected)]


def _fetch_student_frames(requests_module, pd_module, io_module, last_year):
    frames = []
    for year in range(CROSSWALK_FIRST_YEAR, max(CROSSWALK_LAST_YEAR, last_year) + 1):
        url = STUDENT_URL.format(year=year)
        response = requests_module.get(
            url, headers={"User-Agent": UA}, timeout=120
        )
        if response.status_code == 404:
            continue
        response.raise_for_status()
        frames.append(
            (
                year,
                pd_module.read_csv(
                    io_module.BytesIO(response.content),
                    dtype=str,
                    encoding="utf-8-sig",
                    keep_default_na=False,
                ),
            )
        )
    if not frames:
        raise RuntimeError("無法取得教育部 student.csv 校籍對照資料")
    return frames


def build_school_county_lookup(
    source_frames, requests_module, pd_module, io_module, quote_module
):
    student_frames = _fetch_student_frames(
        requests_module,
        pd_module,
        io_module,
        max(year for year, _, _ in source_frames),
    )
    code_map = {}
    name_map = {}
    for _, frame in student_frames:
        code_col = _find_first(frame.columns, "學校", "代碼") or _find_first(
            frame.columns, "學校", "代號"
        )
        name_col = _find_first(frame.columns, "學校", "名稱")
        county_col = _find_first(frame.columns, "縣市", "名稱")
        if not code_col or not name_col or not county_col:
            raise ValueError(
                f"student.csv 缺少校籍對照欄位：{list(frame.columns)}"
            )
        for code, name, county in frame[[code_col, name_col, county_col]].drop_duplicates().itertuples(index=False):
            code = _normalise_school_code(code)
            name = _normalise_school_name(name)
            county = _normalise_county(county)
            if not code or not name or not county:
                continue
            if county not in COUNTY_CODE:
                raise ValueError(f"student.csv 出現未知縣市：{county!r}")
            code_map.setdefault(code, set()).add(county)
            name_map.setdefault(name, set()).add(county)

    pairs = set()
    for _, frame, _ in source_frames:
        roles = _resolve_columns(frame.columns)
        for code, name in frame[[roles["school_code"], roles["school_name"]]].drop_duplicates().itertuples(index=False):
            pairs.add((_normalise_school_code(code), _normalise_school_name(name)))
    lookup = {}
    summary = {
        "total_school_pairs": len(pairs),
        "school_code_matches": 0,
        "school_name_matches": 0,
        "manual_registry_matches": 0,
    }
    unresolved = []
    for code, name in sorted(pairs):
        code_candidates = code_map.get(code, set())
        name_candidates = name_map.get(name, set())
        if len(code_candidates) == 1:
            county, method = next(iter(code_candidates)), "school_code"
            summary["school_code_matches"] += 1
        elif len(name_candidates) == 1:
            county, method = next(iter(name_candidates)), "school_name"
            summary["school_name_matches"] += 1
        elif code in MANUAL_SCHOOL_COUNTY_BY_CODE:
            county, method = MANUAL_SCHOOL_COUNTY_BY_CODE[code], "official_registry_fallback"
            summary["manual_registry_matches"] += 1
        else:
            unresolved.append(
                {
                    "school_code": code,
                    "school_name": name,
                    "code_candidates": sorted(code_candidates),
                    "name_candidates": sorted(name_candidates),
                }
            )
            continue
        lookup[(code, name)] = (county, method)
    if unresolved:
        raise ValueError(
            "UDB 檔案有學校無法對到縣市，拒絕靜默丟列："
            f"{unresolved}"
        )
    summary["unmatched"] = 0
    print(f"school county crosswalk =========== {summary}")
    return lookup, summary


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _check_scale_anomalies(metrics):
    usable = {year: value for year, value in metrics.items() if year not in KNOWN_BAD_YEARS}
    for year, value in usable.items():
        others = [other for other_year, other in usable.items() if other_year != year]
        if not others:
            continue
        baseline = _median(others)
        if baseline is None:
            continue
        if baseline == 0:
            anomalous = value != 0
        else:
            anomalous = value > baseline * 3 or value < baseline / 3
        if anomalous:
            raise ValueError(
                f"註冊率年度尺度異常：{year}={value}，其餘年度中位數={baseline}；"
                "若確認為來源範圍異常，請明確加入 KNOWN_BAD_YEARS"
            )
    print(f"registration scale metrics =========== {metrics}")


def transform_frames(source_frames, lookup, data_time, pd_module, json_module):
    rows = []
    reconciliation = {}
    hidden_stats = {"hidden_by_field": {}, "blank_by_field": {}}
    scale_metrics = {}
    for academic_year, frame, source_label in source_frames:
        roles = _resolve_columns(frame.columns)
        period_start, period_end = _academic_period(academic_year)
        rec = reconciliation.setdefault(
            academic_year,
            {
                "source_rows_ntpc": 0,
                "emitted_rate_cells": 0,
                "hidden_rate_cells": 0,
                "formula_checks_passed": 0,
                "formula_checks_skipped": 0,
                "formula_checks_capped": 0,
                "rate_values": [],
            },
        )
        for _, source_row in frame.iterrows():
            school_code = _normalise_school_code(source_row[roles["school_code"]])
            school_name = _normalise_school_name(source_row[roles["school_name"]])
            key = (school_code, school_name)
            if key not in lookup:
                raise ValueError(f"校籍對照遺漏：{key}")
            county, match_method = lookup[key]
            if county != "新北市":
                continue
            rec["source_rows_ntpc"] += 1
            raw = {
                letter: source_row[roles[letter]]
                for letter in ("a", "b", "c", "d")
            }
            counts = {
                letter: _parse_number(raw[letter], roles[letter], hidden_stats)
                for letter in ("a", "b", "c", "d")
            }
            rate = _parse_number(
                source_row[roles["rate"]], roles["rate"], hidden_stats, kind="rate"
            )
            if rate is None:
                rec["hidden_rate_cells"] += 1
                continue
            rec["emitted_rate_cells"] += 1
            rec["rate_values"].append(rate)
            denominator = None
            if all(counts[letter] is not None for letter in ("a", "b", "c", "d")):
                denominator = counts["a"] - counts["b"] + counts["d"]
            if denominator is None or denominator <= 0:
                rec["formula_checks_skipped"] += 1
            else:
                expected = (counts["c"] + counts["d"]) / denominator * 100
                if abs(expected - rate) > 0.1:
                    if rate == 100.0 and expected > 100.0:
                        rec["formula_checks_capped"] += 1
                    else:
                        raise ValueError(
                            f"{academic_year} 學年度註冊率公式對帳失敗："
                            f"來源 {rate}、計算 {expected}、學校 {school_code}/{school_name}"
                        )
                else:
                    rec["formula_checks_passed"] += 1
            breakdown = {
                "school": school_name,
                "school_code": school_code,
                "establishment": _clean(source_row[roles["establishment"]]),
                "school_type": _clean(source_row[roles["school_type"]]),
                "source_academic_year": str(academic_year),
                "source_file": source_label,
                "school_county_match_method": match_method,
                "registration_formula": (
                    f"{('E' if '(E)' in str(roles['rate']) else 'D')}="
                    f"(C+{('D' if '(D)' in str(roles['d']) else 'E')})/"
                    f"(A-B+{('D' if '(D)' in str(roles['d']) else 'E')})*100"
                ),
                "foreign_source_letter": (
                    "D" if "(D)" in str(roles["d"]) else "E"
                ),
                "A_admitted_quota": counts["a"],
                "B_eligible_retained": counts["b"],
                "C_registered_new": counts["c"],
                "D_registered_foreign_new": counts["d"],
                    "source_raw_counts": {
                        "A": _clean(raw["a"]),
                        "B": _clean(raw["b"]),
                        "C": _clean(raw["c"]),
                        ("D" if "(D)" in str(roles["d"]) else "E"):
                            _clean(raw["d"]),
                    },
            }
            rows.append(
                {
                    "indicator_id": INDICATOR_ID,
                    "period_start": period_start,
                    "period_end": period_end,
                    "period_type": "academic_year",
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "gender": "total",
                    "area_code": "65000",
                    "area_level": "city",
                    "breakdown": json_module.dumps(
                        breakdown, ensure_ascii=False
                    ),
                    "value": rate,
                    "unit": "%",
                    "value_type": "rate",
                    "data_time": data_time,
                }
            )
        if rec["emitted_rate_cells"] + rec["hidden_rate_cells"] != rec["source_rows_ntpc"]:
            raise ValueError(
                f"{academic_year} 學年度註冊率列數對帳失敗："
                f"來源 {rec['source_rows_ntpc']}、可見 {rec['emitted_rate_cells']}、"
                f"隱匿 {rec['hidden_rate_cells']}"
            )
        rec["median_rate"] = _median(rec["rate_values"])
        scale_metrics[academic_year] = rec["median_rate"]
        print(
            f"academic year {academic_year}: NTPC source rows={rec['source_rows_ntpc']}, "
            f"emitted rates={rec['emitted_rate_cells']}, hidden rates={rec['hidden_rate_cells']}, "
            f"median={rec['median_rate']}, reconciled"
        )

    _check_scale_anomalies(scale_metrics)
    data = pd_module.DataFrame(rows, columns=CONTRACT_COLUMNS)
    if data.empty:
        raise RuntimeError("註冊率資料轉換後沒有可見的新北市資料列")
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    data.attrs["reconciliation"] = reconciliation
    data.attrs["hidden_stats"] = hidden_stats
    data.attrs["scale_metrics"] = scale_metrics
    return data


def _run_transform(requests_module, pd_module, io_module, quote_module, json_module, data_time):
    source_frames = fetch_source_frames(
        requests_module, pd_module, io_module, quote_module
    )
    lookup, crosswalk_summary = build_school_county_lookup(
        source_frames, requests_module, pd_module, io_module, quote_module
    )
    data = transform_frames(
        source_frames, lookup, data_time, pd_module, json_module
    )
    data.attrs["crosswalk_summary"] = crosswalk_summary
    data.attrs["source_years"] = [year for year, _, _ in source_frames]
    return data


def _transfer(**kwargs):
    import io
    import json
    import urllib.parse as urllib_parse

    import pandas as pd
    import requests
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    ready_data_db_uri = kwargs.get("ready_data_db_uri")
    dag_infos = kwargs.get("dag_infos")
    dag_id = dag_infos.get("dag_id")
    load_behavior = dag_infos.get("load_behavior")
    default_table = dag_infos.get("ready_data_default_table")
    data = _run_transform(
        requests, pd, io, urllib_parse, json, get_tpe_now_time_str(is_with_tz=True)
    )
    print(f"ready_data shape =========== {data.shape}")
    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=load_behavior,
        default_table=default_table,
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_id, data["data_time"].max()
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_edu_registration_ntpc",
)
dag.create_dag(etl_func=_transfer)
