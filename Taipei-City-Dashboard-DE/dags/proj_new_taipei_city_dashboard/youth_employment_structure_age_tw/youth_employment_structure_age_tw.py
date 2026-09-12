"""人力資源調查：就業者按行業／職業及年齡。"""

import json
import re
import statistics
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlparse

try:
    from operators.common_pipeline import CommonDag
except ImportError:  # pragma: no cover - verify script does not have Airflow
    CommonDag = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DATASET_PAGES = {
    "industry": "https://data.gov.tw/dataset/36787",
    "occupation": "https://data.gov.tw/dataset/36788",
}
XML_HINTS = {"industry": "mp04057", "occupation": "mp04058"}
TARGET_ROC_YEARS = {110, 111, 112, 113, 114}
KNOWN_BAD_YEARS = set()
AREA_CODE = "TW"
AREA_LEVEL = "country"
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
AGE_BAND_RAW = {
    (15, 19): "15-19歲",
    (20, 24): "20-24歲",
    (25, 29): "25-29歲",
    (30, 34): "30-34歲",
    (35, 39): "35-39歲",
    (40, 44): "40-44歲",
    (45, 49): "45-49歲",
    (50, 54): "50-54歲",
    (55, 59): "55-59歲",
    (60, 64): "60-64歲",
    (65, None): "65歲及以上",
}
_MISSING = {"", "-", "－", "—", "–", "…", "..."}


def _normalise(value):
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


def _resolve_xml_urls(page_html, hint):
    links = re.findall(r"https://ws\.dgbas\.gov\.tw/[^\"'<>\s]+?\.xml", page_html, re.I)
    links = list(dict.fromkeys(unquote(link) for link in links if hint.lower() in link.lower()))
    output = {}
    for link in links:
        match = re.search(r"[aA](\d{3})\.xml$", link, re.I)
        if not match:
            continue
        year = int(match.group(1))
        if year in TARGET_ROC_YEARS:
            if year in output and output[year] != link:
                raise RuntimeError(f"{hint} 年度資源連結重複：民國 {year}")
            output[year] = link
    missing = TARGET_ROC_YEARS - set(output)
    if missing:
        raise RuntimeError(f"{hint} XML 缺少年度資源：{sorted(missing)}")
    return output


def _find_one(tags, *, role, predicate):
    matches = [tag for tag in tags if predicate(_normalise(tag))]
    if len(matches) != 1:
        raise KeyError(f"就業結構欄位角色 {role!r} 不唯一：{matches}")
    return matches[0]


def _age_token(tag, age_range):
    lower, upper = age_range
    if upper is None:
        aliases = ("65歲以上", "65歲及以上", "65_years_and_above", "65_years_and_over")
    else:
        aliases = (f"{lower}-{upper}歲", f"{lower}-{upper}_years")
    normalised = _normalise(tag)
    return any(_normalise(alias) in normalised for alias in aliases)


def _resolve_age_fields(tags):
    fields = {}
    for age_range in AGE_RANGES:
        matches = [
            tag
            for tag in tags
            if "千人" in _normalise(tag)
            and "百分比" not in _normalise(tag)
            and _age_token(tag, age_range)
        ]
        if len(matches) != 1:
            raise KeyError(
                f"就業結構年齡欄位不唯一：age={age_range}, matches={matches}"
            )
        fields[age_range] = matches[0]
    return fields


def _parse_number(value, *, field):
    text = str(value or "").strip().replace(",", "")
    if text in _MISSING:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"就業結構欄位 {field!r} 數值無法解析：{value!r}") from exc


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
                f"就業結構尺度異常：{label} 民國 {year}={value}，"
                f"其餘年度中位數={median}，超過三倍"
            )


def _section_gender(item_raw):
    """區塊標頭列 -> 性別；一般資料列 -> None。

    來源用「總 計 Total」「男 Male」「女 Female」當區塊分隔，
    這些列本身不是資料，而是宣告底下 21 個行業屬於哪個性別。
    """
    import re as _re

    text = _re.sub(r"\s+", "", str(item_raw))
    if text.startswith("總計") or text.lower().startswith("total"):
        return "total"
    if text.startswith("男") or text.lower().startswith("male"):
        return "male"
    if text.startswith("女") or text.lower().startswith("female"):
        return "female"
    return None


def _parse_xml(raw, *, dimension, year, source_url):
    from xml.etree import ElementTree as ET

    root = ET.fromstring(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    records = [record for record in list(root) if list(record)]
    if not records:
        raise RuntimeError(f"{dimension} 民國 {year} XML 沒有資料列")
    parsed = []
    current_gender = ["total"]   # 用 list 讓內層迴圈可改寫
    for row_number, record in enumerate(records, 1):
        values = {child.tag: (child.text or "").strip() for child in list(record)}
        item_field = _find_one(
            list(values),
            role="item",
            predicate=lambda tag: "項目別" in tag or "item" in tag,
        )
        total_field = _find_one(
            list(values),
            role="total",
            predicate=lambda tag: (
                tag in {
                    "總計_total_千人",
                    "total_千人",
                    "total_thousand_persons",
                }
            ),
        )
        age_fields = _resolve_age_fields(list(values))
        item_raw = values[item_field]
        item = _normalise(item_raw)
        if not item:
            raise ValueError(f"{dimension} 民國 {year} 第 {row_number} 列項目空白")
        total = _parse_number(values.get(total_field), field=total_field)
        if total is None:
            raise ValueError(f"{dimension} 民國 {year} 第 {row_number} 列總計缺值")
        age_values = {}
        for age_range, field in age_fields.items():
            value = _parse_number(values.get(field), field=field)
            age_values[age_range] = value

        # ★ 來源是「三個性別區塊串在同一份 XML」，不是單一表。
        # 實測民國 114 年的 mp04057：66 筆記錄 =
        #     第 1 筆「總 計 Total」標頭 + 21 個行業
        #     第 23 筆「男 Male」標頭     + 同樣 21 個行業
        #     第 45 筆「女 Female」標頭   + 同樣 21 個行業
        # 先前沒有偵測區塊標頭，於是同一個行業被輸出三次、
        # 男女全部被錯標成 gender=total，任何加總都會約三倍。
        # 對帳（輸入總和 == 輸出總和）抓不到這種錯，因為兩邊都含三塊。
        # 區塊標頭列（「總 計 Total」/「男 Male」/「女 Female」）**同時也是資料**：
        # 它是該性別的全行業總計。所以不能跳過，只需切換性別並把 item
        # 統一標成「總計」，讓下游的總計查找不受標頭字面影響。
        section = _section_gender(item_raw)
        if section is not None:
            current_gender[0] = section
            item = "總計"

        parsed.append(
            {
                "item": item,
                "item_raw": item_raw,
                "gender": current_gender[0],
                "total": total,
                "total_field": total_field,
                "age_fields": age_fields,
                "age_values": age_values,
                "source_url": source_url,
            }
        )
    if not any(r["gender"] == "male" for r in parsed):
        raise ValueError(
            f"{dimension} 民國 {year} 沒有解析到任何『男』區塊。"
            "來源版面可能已改變；在確認之前不要放行，"
            "否則會重演『三個性別區塊全被標成 total』的錯誤。"
        )
    return parsed


def transform_sources(raw_sources, *, data_time):
    rows = []
    source_records = {}
    source_sums = {}
    source_output_sums = {}
    age_source_sums = {}
    age_output_sums = {}
    missing_age_cells = {}
    scale_values = {}
    for dimension, yearly in raw_sources.items():
        source_records[dimension] = 0
        source_sums[dimension] = Decimal("0")
        source_output_sums[dimension] = Decimal("0")
        age_source_sums[dimension] = Decimal("0")
        age_output_sums[dimension] = Decimal("0")
        missing_age_cells[dimension] = 0
        scale_values[dimension] = {}
        for roc_year in sorted(yearly):
            parsed = _parse_xml(
                yearly[roc_year]["raw"],
                dimension=dimension,
                year=roc_year,
                source_url=yearly[roc_year]["url"],
            )
            year_ad = roc_year + 1911
            base = {
                "period_start": f"{year_ad:04d}-01-01",
                "period_end": f"{year_ad:04d}-12-31",
                "period_type": "year",
                "area_code": AREA_CODE,
                "area_level": AREA_LEVEL,
                # gender 由各列的區塊決定（見 _section_gender），不在此硬編。
                "unit": "千人",
                "value_type": "count",
                "data_time": data_time,
            }
            # 來源文件會把同一個項目列兩次（實測：民國 114 年的
            # 「礦業及土石採取業」在同一份 XML 內出現兩筆，各年齡格全為 0）。
            # 兩筆完全相同時是資訊量為零的重複，合併不損失任何東西；
            # 但若值不同就代表來源有歧義，不該由 ETL 猜哪一筆對——直接拋錯。
            deduped, seen_items = [], {}
            for record in parsed:
                fingerprint = (
                    record["total"],
                    tuple(sorted((k, v) for k, v in record["age_values"].items())),
                )
                key = (record["item"], record["gender"])
                if key not in seen_items:
                    seen_items[key] = fingerprint
                    deduped.append(record)
                elif seen_items[key] != fingerprint:
                    raise ValueError(
                        f"{dimension} 民國 {roc_year} 的項目／性別 {key} 重複出現且數值不同："
                        f"{seen_items[key]} vs {fingerprint}。"
                        "來源有歧義，請人工確認要採用哪一筆，不要讓 ETL 自行選擇。"
                    )
            if len(deduped) != len(parsed):
                print(
                    f"{dimension} 民國 {roc_year} 來源重複項目 =========== "
                    f"合併 {len(parsed) - len(deduped)} 筆完全相同的重複列"
                )
            parsed = deduped

            # 三個性別各有一列全行業總計；尺度檢核固定用 gender=total 那一列。
            total_item = next(
                (record for record in parsed
                 if "總計" in record["item"] and record["gender"] == "total"),
                None,
            )
            if total_item is None:
                raise ValueError(f"{dimension} 民國 {roc_year} 找不到總計項目")
            scale_values[dimension][roc_year] = total_item["total"]
            for record in parsed:
                source_records[dimension] += 1
                source_sums[dimension] += record["total"]
                source_output_sums[dimension] += record["total"]
                rows.append(
                    {
                        **base,
                        "gender": record["gender"],
                        "indicator_id": f"employment_{dimension}_total_count",
                        "age_lower": None,
                        "age_upper": None,
                        "age_band_raw": None,
                        "breakdown": json.dumps(
                            {
                                "employment_dimension": dimension,
                                "item": record["item"],
                                "item_label_raw": record["item_raw"],
                                "source_total": float(record["total"]),
                                "source_total_field": record["total_field"],
                                "source_url": record["source_url"],
                                "unaged_source_total": True,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        "value": float(record["total"]),
                    }
                )
                for age_range, value in record["age_values"].items():
                    if value is None:
                        missing_age_cells[dimension] += 1
                        continue
                    age_source_sums[dimension] += value
                    age_output_sums[dimension] += value
                    rows.append(
                        {
                            **base,
                            "gender": record["gender"],
                            "indicator_id": f"employment_{dimension}_count_by_age",
                            "age_lower": age_range[0],
                            "age_upper": age_range[1],
                            "age_band_raw": record["age_fields"][age_range],
                            "breakdown": json.dumps(
                                {
                                    "employment_dimension": dimension,
                                    "item": record["item"],
                                    "item_label_raw": record["item_raw"],
                                    "source_url": record["source_url"],
                                    "source_age_field": record["age_fields"][age_range],
                                    "source_total": float(record["total"]),
                                    "source_age_field_value_is_raw": True,
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            "value": float(value),
                        }
                    )
        _scale_guard(scale_values[dimension], dimension)
        if source_sums[dimension] != source_output_sums[dimension]:
            raise ValueError(f"{dimension} 來源總計輸入／輸出總和不一致")
        if age_source_sums[dimension] != age_output_sums[dimension]:
            raise ValueError(f"{dimension} 年齡數值輸入／輸出總和不一致")

    if not rows:
        raise RuntimeError("就業者行業／職業沒有產出資料")
    return rows, {
        "source_records": source_records,
        "source_sums": {key: str(value) for key, value in source_sums.items()},
        "output_sums": {
            key: str(value) for key, value in source_output_sums.items()
        },
        "age_source_sums": {
            key: str(value) for key, value in age_source_sums.items()
        },
        "age_output_sums": {
            key: str(value) for key, value in age_output_sums.items()
        },
        "missing_age_cells": missing_age_cells,
        "dimensions": sorted(raw_sources),
        "roc_years": sorted(
            {year for yearly in raw_sources.values() for year in yearly}
        ),
        "industry_total_114_thousand": scale_values["industry"].get(114),
        "occupation_total_114_thousand": scale_values["occupation"].get(114),
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
        if row["indicator_id"].endswith("_total_count"):
            if any(
                row[column] is not None
                for column in ("age_lower", "age_upper", "age_band_raw")
            ):
                raise ValueError(f"契約第 {row_number} 列未分齡總計不應帶年齡")
        elif row["age_lower"] is None or row["age_band_raw"] is None:
            raise ValueError(f"契約第 {row_number} 列缺少年齡區間")
        # 來源分成 總計／男／女 三個區塊，三種 gender 都是合法值。
        # （原本這裡寫死只接受 total，是因為先前沒有偵測區塊標頭、
        #   把三個區塊全當成 total；那個假設已修正。）
        if row["gender"] not in ("total", "male", "female"):
            raise ValueError(
                f"契約第 {row_number} 列 gender 非預期值：{row['gender']!r}"
            )
        if row["area_code"] != AREA_CODE:
            raise ValueError(f"契約第 {row_number} 列區域不符：{row['area_code']!r}")
        json.loads(row["breakdown"])
    return True


def fetch_and_transform(*, data_time=None):
    if data_time is None:
        data_time = datetime.now().astimezone().isoformat()
    raw_sources = {}
    for dimension, page_url in DATASET_PAGES.items():
        page = _http_get(page_url, timeout=60)
        urls = _resolve_xml_urls(page.text, XML_HINTS[dimension])
        raw_sources[dimension] = {}
        for roc_year, xml_url in sorted(urls.items()):
            raw_sources[dimension][roc_year] = {
                "url": xml_url,
                "raw": _http_get(xml_url, timeout=180).content,
            }
    return transform_sources(raw_sources, data_time=data_time)


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
    print(f"employment structure diagnostics =========== {diagnostics}")
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
        dag_folder="youth_employment_structure_age_tw",
    )
    dag.create_dag(etl_func=_transfer)
