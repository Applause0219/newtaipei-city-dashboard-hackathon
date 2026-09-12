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
CURRENT_FILENAME = (
    "學14-2.退學人數-以「校(含學制班別)」統計(111學年度起).csv"
)
LEGACY_FILENAME = (
    "學14-2.退學人數-以「校(含學制班別)」統計(110學年度以前).csv"
)
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

# student.csv 沒有涵蓋部分宗教研修學院；這些是教育部校籍代碼的官方校址縣市。
# 先走 student.csv 的代碼／名稱對照，只有確實不在該檔案的代碼才走這張 allow-list。
MANUAL_SCHOOL_COUNTY_BY_CODE = {
    "1R01": "新北市",
    "1R02": "臺北市",
    "1R03": "新北市",
    "1R04": "高雄市",
    "1R05": "臺北市",
    "1R06": "南投縣",
    "1R07": "臺南市",
    "1R08": "桃園市",
    "1R09": "南投縣",
    "1R10": "雲林縣",
    "1085": "新北市",
}

# 來源沒有已知的壞年度；保留明確的清單，避免未來新增異常時默默吞掉。
KNOWN_BAD_YEARS = frozenset()
CROSSWALK_FIRST_YEAR = 103
CROSSWALK_LAST_YEAR = 114
INDICATOR_ID = "college_dropout_count"


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
    text = re.sub(r"^\s*\d+\s*", "", text)
    text = text.replace("台", "臺")
    aliases = {
        "桃園縣": "桃園市", "臺中縣": "臺中市", "臺南縣": "臺南市",
        "高雄縣": "高雄市",
    }
    return aliases.get(text, text)


def _find_first(columns, *musts, exclude=()):
    matches = [
        column for column in columns
        if all(must in str(column) for must in musts)
        and not any(token in str(column) for token in exclude)
    ]
    return matches[0] if matches else None


def _find_all(columns, *musts, exclude=()):
    return [
        column for column in columns
        if all(must in str(column) for must in musts)
        and not any(token in str(column) for token in exclude)
    ]


def _resolve_columns(columns):
    columns = list(columns)
    roles = {
        "academic_year": _find_first(columns, "學年度"),
        # 「學期」必須進 breakdown，否則上下學期兩列會在所有維度上完全相同。
        # 實測：漏掉它會讓 41,795 列中的 41,372 列（99%）變成無法區分的重複對。
        # period 是整個學年，所以學期是唯一能區分兩者的維度。
        # 歷史檔（110 學年以前）可能沒有這一欄，因此允許 None。
        "semester": _find_first(columns, "學期"),
        "school_code": (
            _find_first(columns, "學校", "統計處", "代碼")
            or _find_first(columns, "學校", "代碼")
            or _find_first(columns, "學校", "代號")
        ),
        "school_name": _find_first(columns, "學校", "名稱"),
        "establishment": _find_first(columns, "設立別"),
        "school_type": _find_first(columns, "學校類別"),
        "study_system": _find_first(columns, "學制"),
        "gender": _find_first(columns, "性別"),
        "total": _find_first(columns, "退學人數", "總計"),
        "student_initiated_total": _find_first(
            columns, "學生自請退學", "小計"
        ),
        "school_ordered_total": _find_first(
            columns, "學校勒令退學", "小計"
        ),
    }
    required = [
        "academic_year", "school_code", "school_name", "establishment",
        "school_type", "study_system", "gender",
    ]
    missing = [role for role in required if roles[role] is None]
    if missing:
        raise ValueError(
            f"退學來源缺少必要欄位角色 {missing}；實際欄位：{columns}"
        )

    if roles["student_initiated_total"] and roles["school_ordered_total"]:
        if roles["total"] is None:
            raise ValueError(f"退學新格式缺少退學總計欄：{columns}")
        roles["mode"] = "split"
        roles["student_initiated_reasons"] = _find_all(
            columns, "學生自請退學", exclude=("小計",)
        )
        roles["school_ordered_reasons"] = _find_all(
            columns, "學校勒令退學", exclude=("小計",)
        )
        if not roles["student_initiated_reasons"] or not roles["school_ordered_reasons"]:
            raise ValueError(f"退學新格式找不到自請／勒令原因欄：{columns}")
    else:
        roles["mode"] = "legacy"
        roles["total"] = _find_first(columns, "退學人數", "小計")
        if roles["total"] is None:
            raise ValueError(f"退學歷史格式缺少小計欄：{columns}")
        roles["legacy_reasons"] = [
            column for column in columns
            if "退學人數" in str(column)
            and column != roles["total"]
        ]
        if not roles["legacy_reasons"]:
            raise ValueError(f"退學歷史格式找不到原因欄：{columns}")
    return roles


def _reason_label(source_column):
    text = str(source_column).rsplit("-", 1)[-1].strip()
    text = text.removeprefix("因")
    aliases = {
        "工作需求": "工作",
        "就讀學校、科系不符期待": "就讀學校、科系不符期待",
        "其他(不含死亡)": "其他(不含死亡)",
    }
    return aliases.get(text, text)


def _gender(value):
    aliases = {
        "男": "male", "男生": "male", "男性": "male",
        "女": "female", "女生": "female", "女性": "female",
        "合計": "total", "總計": "total", "不分性別": "total",
    }
    text = _clean(value)
    if text not in aliases:
        raise ValueError(f"退學來源出現未知性別值：{value!r}")
    return aliases[text]


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
        raise ValueError(f"退學來源欄位 {field} 無法轉數值：{value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"退學來源欄位 {field} 出現無效數值：{value!r}")
    if kind == "rate" and number > 100:
        raise ValueError(f"退學來源率超過100%：{field}={value!r}")
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
    current_paths = [
        (directory, f"/download/udata/static_file/{directory}/{CURRENT_FILENAME}")
        for directory in CURRENT_DIRECTORIES
    ]
    for directory, path in current_paths:
        frame = _fetch_csv(path, requests_module, pd_module, io_module, quote_module)
        if frame is None:
            print(f"dropout source missing: static_file/{directory}")
            continue
        year_col = _find_first(frame.columns, "學年度")
        if year_col is None:
            raise ValueError(f"退學 CSV 缺少學年度欄：{path}")
        for year_text in sorted(frame[year_col].astype(str).str.strip().unique()):
            year = int(year_text)
            if year not in selected:
                selected[year] = (
                    year,
                    frame[frame[year_col].astype(str).str.strip() == year_text].copy(),
                    f"static_file/{directory}",
                )

    legacy_path = f"/download/udata/static_file/un_update/{LEGACY_FILENAME}"
    legacy = _fetch_csv(
        legacy_path, requests_module, pd_module, io_module, quote_module
    )
    if legacy is None:
        raise RuntimeError(f"找不到退學歷史檔：{legacy_path}")
    year_col = _find_first(legacy.columns, "學年度")
    if year_col is None:
        raise ValueError("退學歷史 CSV 缺少學年度欄")
    for year_text in sorted(legacy[year_col].astype(str).str.strip().unique()):
        year = int(year_text)
        if year not in selected:
            selected[year] = (
                year,
                legacy[legacy[year_col].astype(str).str.strip() == year_text].copy(),
                "static_file/un_update",
            )
    if not selected:
        raise RuntimeError("沒有抓到任何退學學年度資料")
    return [selected[year] for year in sorted(selected)]


def _fetch_student_frames(
    requests_module, pd_module, io_module, quote_module, last_year
):
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
    last_year = max(year for year, _, _ in source_frames)
    student_frames = _fetch_student_frames(
        requests_module, pd_module, io_module, quote_module, last_year
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
        method = None
        county = None
        if len(code_candidates) == 1:
            county = next(iter(code_candidates))
            method = "school_code"
            summary["school_code_matches"] += 1
        elif len(name_candidates) == 1:
            county = next(iter(name_candidates))
            method = "school_name"
            summary["school_name_matches"] += 1
        elif code in MANUAL_SCHOOL_COUNTY_BY_CODE:
            county = MANUAL_SCHOOL_COUNTY_BY_CODE[code]
            method = "official_registry_fallback"
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
        if county is not None:
            lookup[(code, name)] = (county, method)

    if unresolved:
        raise ValueError(
            "UDB 檔案有學校無法對到縣市，拒絕靜默丟列："
            f"{unresolved}"
        )
    summary["unmatched"] = 0
    print(f"school county crosswalk =========== {summary}")
    return lookup, summary


def _source_specs(roles):
    specs = [("total", roles["total"], "overall")]
    if roles["mode"] == "split":
        specs.append(("subtotal", roles["student_initiated_total"], "student_initiated"))
        specs.extend(
            ("reason", column, "student_initiated")
            for column in roles["student_initiated_reasons"]
        )
        specs.append(("subtotal", roles["school_ordered_total"], "school_ordered"))
        specs.extend(
            ("reason", column, "school_ordered")
            for column in roles["school_ordered_reasons"]
        )
    else:
        specs.extend(
            ("reason", column, "legacy_total")
            for column in roles["legacy_reasons"]
        )
    return specs


def _assert_reconciliation(values, roles, year, context, rec):
    def check(parts, total, label):
        if total is None or any(value is None for value in parts):
            rec["checks_skipped_hidden"] += 1
            return
        if abs(sum(parts) - total) > 0.5:
            raise ValueError(
                f"{year} 學年度退學對帳失敗（{context}／{label}）："
                f"分項 {parts}、小計 {total}"
            )
        rec["checks_passed"] += 1

    if roles["mode"] == "split":
        self_total = values[roles["student_initiated_total"]]
        order_total = values[roles["school_ordered_total"]]
        check(
            [values[column] for column in roles["student_initiated_reasons"]],
            self_total,
            "學生自請",
        )
        check(
            [values[column] for column in roles["school_ordered_reasons"]],
            order_total,
            "學校勒令",
        )
        check([self_total, order_total], values[roles["total"]], "總計")
    else:
        parts = [values[column] for column in roles["legacy_reasons"]]
        total = values[roles["total"]]
        if total is None or any(value is None for value in parts):
            rec["checks_skipped_hidden"] += 1
        elif abs(sum(parts) - total) <= 0.5:
            rec["checks_passed"] += 1
        else:
            rec["checks_skipped_incomplete_schema"] += 1


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
                f"退學年度尺度異常：{year}={value}，其餘年度中位數={baseline}；"
                "若確認為來源範圍異常，請明確加入 KNOWN_BAD_YEARS"
            )
    print(f"dropout scale metrics =========== {metrics}")


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
                "emitted_cells": 0,
                "hidden_cells": 0,
                "input_total": 0.0,
                "emitted_total": 0.0,
                "input_by_reason": {},
                "emitted_by_reason": {},
                "checks_passed": 0,
                "checks_skipped_hidden": 0,
                "checks_skipped_incomplete_schema": 0,
            },
        )
        hidden_before = sum(hidden_stats["hidden_by_field"].values())
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
            raw_gender = source_row[roles["gender"]]
            gender = _gender(raw_gender)
            values = {}
            for spec_type, column, _ in _source_specs(roles):
                values[column] = _parse_number(
                    source_row[column], column, hidden_stats, kind="count"
                )
                if values[column] is not None:
                    rec["emitted_cells"] += 1
                    rec["input_by_reason"][column] = (
                        rec["input_by_reason"].get(column, 0.0) + values[column]
                    )
                    rec["emitted_by_reason"][column] = (
                        rec["emitted_by_reason"].get(column, 0.0) + values[column]
                    )
            _assert_reconciliation(
                values,
                roles,
                academic_year,
                f"{school_code}/{school_name}/{raw_gender}",
                rec,
            )
            total = values[roles["total"]]
            if total is not None:
                rec["input_total"] += total
                rec["emitted_total"] += total
            base_breakdown = {
                "school": school_name,
                "school_code": school_code,
                "establishment": _clean(source_row[roles["establishment"]]),
                "school_type": _clean(source_row[roles["school_type"]]),
                "study_system": _clean(source_row[roles["study_system"]]),
                "source_academic_year": str(academic_year),
                "semester": (
                    _clean(source_row[roles["semester"]])
                    if roles.get("semester") else None
                ),
                "source_file": source_label,
                "school_county_match_method": match_method,
            }
            for spec_type, column, scope in _source_specs(roles):
                value = values[column]
                if value is None:
                    continue
                reason = (
                    "total" if spec_type == "total"
                    else "subtotal" if spec_type == "subtotal"
                    else _reason_label(column)
                )
                breakdown = dict(
                    base_breakdown,
                    gender_raw=_clean(raw_gender),
                    reason=reason,
                    reason_scope=scope,
                    source_column=str(column),
                )
                rows.append(
                    {
                        "indicator_id": INDICATOR_ID,
                        "period_start": period_start,
                        "period_end": period_end,
                        "period_type": "academic_year",
                        "age_lower": None,
                        "age_upper": None,
                        "age_band_raw": None,
                        "gender": gender,
                        "area_code": "65000",
                        "area_level": "city",
                        "breakdown": json_module.dumps(
                            breakdown, ensure_ascii=False
                        ),
                        "value": value,
                        "unit": "人",
                        "value_type": "count",
                        "data_time": data_time,
                    }
                )
        rec["hidden_cells"] = (
            sum(hidden_stats["hidden_by_field"].values()) - hidden_before
        )
        if rec["input_total"] != rec["emitted_total"]:
            raise ValueError(
                f"{academic_year} 學年度退學總計輸出對帳失敗："
                f"輸入 {rec['input_total']}、輸出 {rec['emitted_total']}"
            )
        scale_metrics[academic_year] = rec["input_total"]
        print(
            f"academic year {academic_year}: NTPC source rows={rec['source_rows_ntpc']}, "
            f"emitted cells={rec['emitted_cells']}, total={rec['input_total']}, "
            f"reconciled"
        )

    _check_scale_anomalies(scale_metrics)
    data = pd_module.DataFrame(rows, columns=CONTRACT_COLUMNS)
    if data.empty:
        raise RuntimeError("退學資料轉換後沒有新北市資料列")
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
    dag_folder="youth_edu_dropout_ntpc",
)
dag.create_dag(etl_func=_transfer)
