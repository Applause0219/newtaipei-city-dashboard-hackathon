"""勞動部歷年基本工資／最低工資調整，轉成可比較的指數。"""

import html
import json
import re
import statistics
from datetime import datetime
from urllib.parse import urlparse

try:
    from operators.common_pipeline import CommonDag
except ImportError:  # pragma: no cover - verify script does not have Airflow
    CommonDag = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SOURCE_PAGES = (
    "https://www.mol.gov.tw/1607/28162/28166/28180/28182/28184/29016/",
    "https://www.mol.gov.tw/1607/28162/28166/28180/70460/76761/76833/post",
)
AREA_CODE = "TW"
AREA_LEVEL = "country"
KNOWN_BAD_YEARS = set()
BASE_MONTHLY_TWD = 24000
BASE_HOURLY_TWD = 160
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


def _http_get(url, *, timeout=60):
    import requests

    if urlparse(url).hostname != "www.mol.gov.tw":
        raise RuntimeError(f"基本工資來源主機不符：{url}")
    response = requests.get(
        url,
        headers={"User-Agent": UA, "Accept": "text/html,*/*"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response


def _strip_html(fragment):
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def _roc_year(value):
    return int(value) + 1911


def _period_from_text(text, publication_roc_year):
    effective = re.search(
        r"自\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起",
        text,
    )
    if effective:
        year, month, day = map(int, effective.groups())
        date = f"{_roc_year(year):04d}-{month:02d}-{day:02d}"
        return date, date, "effective_date"

    month_day = re.search(
        r"自\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起", text
    )
    if month_day:
        month, day = map(int, month_day.groups())
        date = f"{_roc_year(publication_roc_year):04d}-{month:02d}-{day:02d}"
        return date, date, "effective_date"

    year = _roc_year(publication_roc_year)
    return f"{year:04d}-01-01", f"{year:04d}-12-31", "year"


def _amount(text, pattern, label):
    matches = re.findall(pattern, text)
    if len(matches) > 1:
        raise ValueError(f"基本工資同一段落有多個{label}金額：{text!r}")
    if not matches:
        return None
    return float(matches[0].replace(",", ""))


def _parse_fragment(fragment):
    text = _strip_html(fragment)
    if not ("基本工資" in text or "最低工資" in text):
        return None
    publication = re.search(r"民國\s*(\d{1,3})\s*年", text)
    if not publication:
        return None
    publication_roc_year = int(publication.group(1))
    start, end, period_type = _period_from_text(text, publication_roc_year)
    monthly = _amount(
        text,
        r"每月(?:[^，。；]*?)([\d,]+(?:\.\d+)?)\s*元",
        "月",
    )
    hourly = _amount(
        text,
        r"每小時(?:[^，。；]*?)([\d,]+(?:\.\d+)?)\s*元",
        "時",
    )
    if monthly is None and hourly is None:
        return None
    return {
        "publication_roc_year": publication_roc_year,
        "period_start": start,
        "period_end": end,
        "period_type": period_type,
        "monthly_twd": monthly,
        "hourly_twd": hourly,
        "source_text": text,
    }


def _parse_source_pages(page_texts):
    records = {}
    for page_url, page_html in page_texts:
        fragments = re.findall(
            r"<(?:p|li)[^>]*>(.*?)</(?:p|li)>", page_html, re.I | re.S
        )
        for fragment in fragments:
            parsed = _parse_fragment(fragment)
            if parsed is None:
                continue
            parsed["source_page"] = page_url
            key = (parsed["period_start"], parsed["period_end"])
            previous = records.get(key)
            if previous is None:
                records[key] = parsed
                continue
            for field in ("monthly_twd", "hourly_twd"):
                if (
                    previous[field] is not None
                    and parsed[field] is not None
                    and previous[field] != parsed[field]
                ):
                    raise ValueError(
                        f"基本工資重複來源金額不一致：{key} {field} "
                        f"{previous[field]} != {parsed[field]}"
                    )
                if previous[field] is None:
                    previous[field] = parsed[field]
    if not records:
        raise RuntimeError("勞動部頁面找不到基本工資／最低工資調整紀錄")
    return sorted(records.values(), key=lambda item: (item["period_start"], item["period_end"]))


def _scale_guard(values, label):
    values = {key: value for key, value in values.items() if value > 0}
    # 基本工資是長期趨勢資料，改以相鄰調整點作尺度防護，避免把早期
    # 300 元與近年 29,500 元的合法歷史趨勢誤判成異常年度。
    ordered = sorted(values.items())
    for index, (year, value) in enumerate(ordered):
        neighbours = [item[1] for item in ordered[max(0, index - 2): index + 3] if item[0] != year]
        if not neighbours:
            continue
        median = statistics.median(neighbours)
        if median and (value > median * 3 or value < median / 3):
            roc_year = int(year[:4]) - 1911
            if roc_year in KNOWN_BAD_YEARS:
                continue
            raise ValueError(
                f"基本工資尺度異常：{year} {label}={value}，"
                f"相鄰調整點中位數={median}，超過三倍"
            )


def transform_sources(page_texts, *, data_time):
    records = _parse_source_pages(page_texts)
    if not any(record["monthly_twd"] == BASE_MONTHLY_TWD for record in records):
        raise RuntimeError("找不到指數基準：月基本工資 24,000 元")
    if not any(record["hourly_twd"] == BASE_HOURLY_TWD for record in records):
        raise RuntimeError("找不到指數基準：時基本工資 160 元")

    rows = []
    input_count = 0
    output_count = 0
    input_sum = 0.0
    output_sum = 0.0
    input_index_sums = {"monthly": 0.0, "hourly": 0.0}
    output_index_sums = {"monthly": 0.0, "hourly": 0.0}
    scale_values = {"monthly": {}, "hourly": {}}
    for record in records:
        for metric, raw_key, base, label in (
            ("monthly", "monthly_twd", BASE_MONTHLY_TWD, "月"),
            ("hourly", "hourly_twd", BASE_HOURLY_TWD, "時"),
        ):
            raw_value = record[raw_key]
            if raw_value is None:
                continue
            input_count += 1
            input_sum += raw_value
            index_value = raw_value / base * 100
            input_index_sums[metric] += index_value
            output_index_sums[metric] += index_value
            scale_values[metric][record["period_start"]] = raw_value
            rows.append(
                {
                    "indicator_id": f"minimum_wage_{metric}_index",
                    "period_start": record["period_start"],
                    "period_end": record["period_end"],
                    "period_type": record["period_type"],
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "gender": "total",
                    "area_code": AREA_CODE,
                    "area_level": AREA_LEVEL,
                    "breakdown": json.dumps(
                        {
                            "raw_amount_twd": raw_value,
                            "index_base_amount_twd": base,
                            "index_base_period": "2021-01-01",
                            "metric": label,
                            "source_page": record["source_page"],
                            "source_text": record["source_text"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "value": index_value,
                    "unit": "index_2021=100",
                    "value_type": "index",
                    "data_time": data_time,
                }
            )
            output_count += 1
            output_sum += index_value
    if input_count != output_count or input_count == 0:
        raise ValueError(
            f"基本工資對帳失敗：輸入 {input_count} 筆，輸出 {output_count} 筆"
        )
    if input_index_sums != output_index_sums:
        raise ValueError(
            "基本工資指數對帳失敗："
            f"輸入={input_index_sums}，輸出={output_index_sums}"
        )
    for metric, values in scale_values.items():
        _scale_guard(values, metric)
    return rows, {
        "source_periods": len(records),
        "input_amount_count": input_count,
        "emitted_rows": output_count,
        "input_amount_sum_twd": input_sum,
        "emitted_index_sum": output_sum,
        "input_index_sums": input_index_sums,
        "emitted_index_sums": output_index_sums,
        "period_start": records[0]["period_start"],
        "period_end": records[-1]["period_end"],
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
        if row["gender"] != "total" or row["area_code"] != AREA_CODE:
            raise ValueError(f"契約第 {row_number} 列主體／區域不符")
        if row["age_lower"] is not None or row["age_upper"] is not None or row["age_band_raw"] is not None:
            raise ValueError(f"契約第 {row_number} 列不應帶年齡")
        json.loads(row["breakdown"])
    return True


def fetch_and_transform(*, data_time=None):
    if data_time is None:
        data_time = datetime.now().astimezone().isoformat()
    pages = [(url, _http_get(url).text) for url in SOURCE_PAGES]
    return transform_sources(pages, data_time=data_time)


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
    print(f"minimum wage diagnostics =========== {diagnostics}")
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
        dag_folder="youth_minimum_wage_tw",
    )
    dag.create_dag(etl_func=_transfer)
