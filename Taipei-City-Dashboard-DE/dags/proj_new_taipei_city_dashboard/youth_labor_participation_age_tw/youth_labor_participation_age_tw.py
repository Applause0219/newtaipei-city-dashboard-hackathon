"""人力資源調查：年齡別民間人口、勞動力與勞參率。"""

import calendar
import json
import re
import statistics
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

try:
    from operators.common_pipeline import CommonDag
except ImportError:  # pragma: no cover - verify script does not have Airflow
    CommonDag = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DATASET_PAGES = {
    "civilian_population": "https://data.gov.tw/dataset/31061",
    "labor_force": "https://data.gov.tw/dataset/31063",
}
XML_HINTS = {
    "civilian_population": "mp04016",
    "labor_force": "mp04018",
}
AREA_CODE = "TW"
AREA_LEVEL = "country"
KNOWN_BAD_YEARS = set()
CONTRACT_COLUMNS = (
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
)
AGE_RANGES = (
    (15, 19),
    (20, 24),
    (25, 29),
    (30, 34),
    (35, 39),
    (40, 44),
    (45, 49),
    (50, 54),
    (55, 59),
    (60, 64),
    (65, None),
)
GENDERS = ("total", "male", "female")
_MISSING = {"", "-", "－", "—", "–", "…", "..."}


def _normalise_tag(value):
    return unicodedata.normalize("NFKC", str(value or "")).replace(" ", "").replace("　", "").lower()


def _http_get(url, *, timeout=180):
    import requests

    headers = {"User-Agent": UA, "Accept": "text/html,application/xml,text/xml,*/*"}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.SSLError as exc:
        if urlparse(url).hostname != "ws.dgbas.gov.tw":
            raise RuntimeError(f"來源 TLS 請求失敗：{url}：{exc}") from exc
        import urllib3

        urllib3.disable_warnings()
        response = requests.get(url, headers=headers, timeout=timeout, verify=False)
    response.raise_for_status()
    return response


def _resolve_xml_url(page_html, hint):
    links = re.findall(r"https://ws\.dgbas\.gov\.tw/[^\"'<>\s]+?\.xml", page_html, re.I)
    links = list(dict.fromkeys(links))
    candidates = [link for link in links if hint.lower() in link.lower()]
    if len(candidates) != 1:
        raise RuntimeError(
            f"data.gov.tw 頁面找不到唯一的 {hint} XML 資源：{candidates}"
        )
    return candidates[0]


def _record_values(record):
    return {child.tag: (child.text or "").strip() for child in list(record)}


def _period_key(raw, *, state):
    text = unicodedata.normalize("NFKC", str(raw or "")).strip()
    year_match = re.search(r"((?:19|20)\d{2})", text)
    if "平均" in text or "Ave" in text:
        if not year_match:
            raise ValueError(f"年齡勞動力資料年度無法解析：{raw!r}")
        year = int(year_match.group(1))
        state["annual_year"] = year
        state["month_year"] = year + 1
        return f"{year:04d}-01-01", f"{year:04d}-12-31", "year"

    numeric_month_match = re.search(r"(\d{1,2})\s*月", text)
    english_month_match = None
    if not numeric_month_match:
        english_month_match = re.search(
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
            text,
            re.I,
        )
    if numeric_month_match or english_month_match:
        if state.get("month_year") is None:
            raise ValueError(f"月資料前沒有可推導的年度：{raw!r}")
        if numeric_month_match:
            month = int(numeric_month_match.group(1))
        else:
            month = {
                "jan": 1,
                "feb": 2,
                "mar": 3,
                "apr": 4,
                "may": 5,
                "jun": 6,
                "jul": 7,
                "aug": 8,
                "sep": 9,
                "oct": 10,
                "nov": 11,
                "dec": 12,
            }[english_month_match.group(0).lower()[:3]]
        year = int(state["month_year"])
        last_day = calendar.monthrange(year, month)[1]
        return (
            f"{year:04d}-{month:02d}-01",
            f"{year:04d}-{month:02d}-{last_day:02d}",
            "month",
        )
    raise ValueError(f"年齡勞動力年月別無法解析：{raw!r}")


def _age_token(tag, age_range):
    lower, upper = age_range
    if upper is None:
        aliases = ("65歲以上", "65歲及以上", "65_years_and_above", "65_years_and_over")
    else:
        aliases = (f"{lower}-{upper}歲", f"{lower}-{upper}_years")
    normalised = _normalise_tag(tag)
    return any(_normalise_tag(alias) in normalised for alias in aliases)


def _gender_of_tag(tag):
    normalised = _normalise_tag(tag)
    if "female" in normalised or "女" in normalised:
        return "female"
    if "male" in normalised or "男" in normalised:
        return "male"
    if "total" in normalised or "合計" in normalised:
        return "total"
    return None


def _resolve_age_fields(tags):
    fields = {}
    for age_range in AGE_RANGES:
        for gender in GENDERS:
            matches = []
            for tag in tags:
                normalised = _normalise_tag(tag)
                if "千人" not in normalised or "百分比" in normalised:
                    continue
                if _age_token(tag, age_range) and _gender_of_tag(tag) == gender:
                    matches.append(tag)
            if len(matches) != 1:
                raise KeyError(
                    "年齡勞動力資料欄位角色不唯一："
                    f"age={age_range}, gender={gender}, matches={matches}"
                )
            fields[(age_range, gender)] = matches[0]
    return fields


def _parse_number(value, *, field):
    text = str(value or "").strip().replace(",", "")
    if text in _MISSING:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"年齡勞動力欄位 {field!r} 數值無法解析：{value!r}") from exc


def _parse_source(raw, *, source_name):
    from xml.etree import ElementTree as ET

    root = ET.fromstring(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    records = [record for record in list(root) if list(record)]
    if not records:
        raise RuntimeError(f"{source_name} XML 沒有資料列")
    output = {}
    state = {"annual_year": None, "month_year": None}
    for row_number, record in enumerate(records, 1):
        values = _record_values(record)
        period_fields = [
            tag
            for tag in values
            if "年月別" in tag or "year_and_month" in _normalise_tag(tag)
        ]
        if len(period_fields) != 1:
            raise KeyError(f"{source_name} 第 {row_number} 列年月別欄位不唯一：{period_fields}")
        period_raw = values[period_fields[0]]
        period_start, period_end, period_type = _period_key(period_raw, state=state)
        key = (period_start, period_type)
        if key in output:
            raise ValueError(f"{source_name} 出現重複期別：{key}")
        fields = _resolve_age_fields(list(values))
        output[key] = {
            "period_start": period_start,
            "period_end": period_end,
            "period_type": period_type,
            "period_raw": period_raw,
            "values": values,
            "fields": fields,
        }
    return output


def _scale_guard(year_values, label):
    values = {year: value for year, value in year_values.items() if value > 0}
    for year, value in values.items():
        others = [other for other_year, other in values.items() if other_year != year]
        if not others:
            continue
        median = statistics.median(others)
        if median and (value > median * 3 or value < median / 3):
            if year in KNOWN_BAD_YEARS:
                continue
            raise ValueError(
                f"年齡勞動力尺度異常：{label} {year}={value}，"
                f"其餘年度中位數={median}，超過三倍"
            )


def transform_sources(civilian_raw, labor_raw, *, data_time):
    civilian = _parse_source(civilian_raw, source_name="15歲以上民間人口")
    labor = _parse_source(labor_raw, source_name="勞動力")
    if set(civilian) != set(labor):
        raise ValueError(
            "民間人口與勞動力期別不一致："
            f"only_population={sorted(set(civilian) - set(labor))[:5]}, "
            f"only_labor={sorted(set(labor) - set(civilian))[:5]}"
        )

    rows = []
    count_input_sum = {"civilian_population": Decimal("0"), "labor_force": Decimal("0")}
    count_output_sum = {"civilian_population": Decimal("0"), "labor_force": Decimal("0")}
    count_input_cells = {"civilian_population": 0, "labor_force": 0}
    count_output_rows = {"civilian_population": 0, "labor_force": 0}
    annual_anchors = {"civilian_population": {}, "labor_force": {}}

    for key in sorted(civilian):
        pop_record = civilian[key]
        labor_record = labor[key]
        base = {
            "period_start": pop_record["period_start"],
            "period_end": pop_record["period_end"],
            "period_type": pop_record["period_type"],
            "area_code": AREA_CODE,
            "area_level": AREA_LEVEL,
            "gender": None,
            "unit": "千人",
            "data_time": data_time,
        }
        annual_sum = {"civilian_population": Decimal("0"), "labor_force": Decimal("0")}
        for age_range in AGE_RANGES:
            for gender in GENDERS:
                pop_field = pop_record["fields"][(age_range, gender)]
                labor_field = labor_record["fields"][(age_range, gender)]
                pop_value = _parse_number(pop_record["values"].get(pop_field), field=pop_field)
                labor_value = _parse_number(
                    labor_record["values"].get(labor_field), field=labor_field
                )
                if (pop_value is None) != (labor_value is None):
                    raise ValueError(
                        f"期別 {key} 年齡 {age_range} 性別 {gender} 分子／分母一邊缺值"
                    )
                if pop_value is None:
                    continue
                for source_key, value, field, indicator in (
                    (
                        "civilian_population",
                        pop_value,
                        pop_field,
                        "civilian_population_count_by_age",
                    ),
                    ("labor_force", labor_value, labor_field, "labor_force_count_by_age"),
                ):
                    count_input_cells[source_key] += 1
                    count_input_sum[source_key] += value
                    count_output_rows[source_key] += 1
                    count_output_sum[source_key] += value
                    annual_sum[source_key] += value if gender == "total" else Decimal("0")
                    rows.append(
                        {
                            **base,
                            "indicator_id": indicator,
                            "age_lower": age_range[0],
                            "age_upper": age_range[1],
                            "age_band_raw": field,
                            "gender": gender,
                            "breakdown": json.dumps(
                                {
                                    "source_dataset": source_key,
                                    "source_field": field,
                                    "source_period": pop_record["period_raw"],
                                    "age_group_is_raw": True,
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            "value": float(value),
                            "value_type": "count",
                        }
                    )
                if pop_value <= 0:
                    raise ValueError(f"期別 {key} 民間人口分母非正值：{pop_value}")
                rate = labor_value / pop_value * Decimal("100")
                rows.append(
                    {
                        **base,
                        "indicator_id": "labor_force_participation_rate_by_age",
                        "age_lower": age_range[0],
                        "age_upper": age_range[1],
                        "age_band_raw": pop_field,
                        "gender": gender,
                        "breakdown": json.dumps(
                            {
                                "numerator_dataset": "labor_force",
                                "numerator_field": labor_field,
                                "denominator_dataset": "civilian_population",
                                "denominator_field": pop_field,
                                "source_period": pop_record["period_raw"],
                                "derived_from_counts": True,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        "value": float(rate),
                        "unit": "%",
                        "value_type": "rate",
                    }
                )
        if pop_record["period_type"] == "year":
            year = int(pop_record["period_start"][:4])
            for source_key in annual_anchors:
                annual_anchors[source_key][year] = annual_sum[source_key]

    for source_key in count_input_sum:
        if (
            count_input_cells[source_key] != count_output_rows[source_key]
            or count_input_sum[source_key] != count_output_sum[source_key]
        ):
            raise ValueError(
                f"{source_key} 對帳失敗："
                f"輸入 {count_input_cells[source_key]}/{count_input_sum[source_key]}，"
                f"輸出 {count_output_rows[source_key]}/{count_output_sum[source_key]}"
            )
        _scale_guard(annual_anchors[source_key], source_key)

    if not rows:
        raise RuntimeError("年齡勞動力沒有產出資料")
    return rows, {
        "periods": len(civilian),
        "source_period_start": min(civilian),
        "source_period_end": max(civilian),
        "count_input_cells": count_input_cells,
        "count_output_rows": count_output_rows,
        "count_input_sums": {key: str(value) for key, value in count_input_sum.items()},
        "count_output_sums": {key: str(value) for key, value in count_output_sum.items()},
        "latest_annual_labor_force_thousand": max(
            annual_anchors["labor_force"].items()
        ),
    }


def validate_contract(rows):
    allowed_types = {"count", "rate", "median", "mean", "index"}
    for row_number, row in enumerate(rows, 1):
        missing = [column for column in CONTRACT_COLUMNS if column not in row]
        if missing:
            raise ValueError(f"契約第 {row_number} 列缺欄位：{missing}")
        extra = [column for column in row if column not in CONTRACT_COLUMNS]
        if extra:
            raise ValueError(f"契約第 {row_number} 列多出欄位：{extra}")
        if row["value"] is None or row["value_type"] not in allowed_types:
            raise ValueError(f"契約第 {row_number} 列 value/value_type 無效")
        if row["age_lower"] is None or row["age_band_raw"] is None:
            raise ValueError(f"契約第 {row_number} 列缺少年齡區間")
        if row["area_code"] != AREA_CODE or row["area_level"] != AREA_LEVEL:
            raise ValueError(f"契約第 {row_number} 列區域不符")
        json.loads(row["breakdown"])
    return True


def fetch_and_transform(*, data_time=None):
    if data_time is None:
        data_time = datetime.now().astimezone().isoformat()
    raw = {}
    for source_name, page_url in DATASET_PAGES.items():
        page = _http_get(page_url, timeout=60)
        xml_url = _resolve_xml_url(page.text, XML_HINTS[source_name])
        raw[source_name] = _http_get(xml_url, timeout=180).content
    return transform_sources(
        raw["civilian_population"], raw["labor_force"], data_time=data_time
    )


def _transfer(**kwargs):
    import pandas as pd
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    ready_data_db_uri = kwargs.get("ready_data_db_uri")
    dag_infos = kwargs.get("dag_infos")
    dag_id = dag_infos.get("dag_id")
    rows, diagnostics = fetch_and_transform(
        data_time=get_tpe_now_time_str(is_with_tz=True)
    )
    validate_contract(rows)
    data = pd.DataFrame(rows, columns=list(CONTRACT_COLUMNS))
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    print(f"labor participation diagnostics =========== {diagnostics}")
    print(f"ready_data shape =========== {data.shape}")

    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos.get("load_behavior"),
        default_table=dag_infos.get("ready_data_default_table"),
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_id, data["data_time"].max()
    )


if CommonDag is not None:
    dag = CommonDag(
        proj_folder="proj_new_taipei_city_dashboard",
        dag_folder="youth_labor_participation_age_tw",
    )
    dag.create_dag(etl_func=_transfer)
