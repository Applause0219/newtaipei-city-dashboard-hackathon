"""工業及服務業受僱員工全年總薪資（按年齡別）。"""

import json
import posixpath
import re
import statistics
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO
from urllib.parse import unquote, urlparse

try:
    from operators.common_pipeline import CommonDag
except ImportError:  # pragma: no cover - verify script does not have Airflow
    CommonDag = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SOURCE_PAGE = "https://www.stat.gov.tw/News_Content.aspx?n=4580&s=232642"
AREA_CODE = "TW"
AREA_LEVEL = "country"
TARGET_ROC_YEARS = {110, 111, 112, 113}
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

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_PKG_REL_NS = {
    "p": "http://schemas.openxmlformats.org/package/2006/relationships"
}
_MISSING = {"", "-", "－", "—", "–", "…", "..."}


def _normalise_text(value):
    return re.sub(r"[\s　]+", "", str(value or "").strip())


def _http_get(url, *, timeout=180):
    import requests

    headers = {"User-Agent": UA, "Accept": "text/html,application/octet-stream,*/*"}
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


def _resolve_table2_url(page_html):
    links = re.findall(r'href=["\']([^"\']+?\.xlsx)["\']', page_html, re.I)
    candidates = [
        unquote(link)
        for link in links
        if "表2" in unquote(link) or "表２" in unquote(link)
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "主計總處薪資頁面應找到唯一的表2 Excel 連結，"
            f"實際找到 {len(candidates)} 個"
        )
    if urlparse(candidates[0]).hostname != "ws.dgbas.gov.tw":
        raise RuntimeError(f"表2 連結不是官方 ws.dgbas.gov.tw：{candidates[0]}")
    return candidates[0]


def _column_number(cell_ref):
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        raise ValueError(f"Excel 儲存格座標無法解析：{cell_ref!r}")
    number = 0
    for char in match.group(1):
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _shared_strings(archive):
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(text.text or "" for text in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
        for item in root.findall("m:si", _NS)
    ]


def _workbook_sheets(archive):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall("p:Relationship", _PKG_REL_NS)
    }
    output = []
    for sheet in workbook.findall("m:sheets/m:sheet", _NS):
        target = rel_map.get(sheet.attrib.get("{%s}id" % _REL_NS["r"]))
        if not target:
            raise RuntimeError(f"Excel 工作表缺少 relationship：{sheet.attrib}")
        target = posixpath.normpath(posixpath.join("xl", target))
        output.append((sheet.attrib.get("name", ""), target))
    return output


def _read_sheet(archive, path, shared_strings):
    root = ET.fromstring(archive.read(path))
    rows = {}
    for row in root.findall(".//m:sheetData/m:row", _NS):
        values = {}
        for cell in row.findall("m:c", _NS):
            value = cell.find("m:v", _NS)
            text = "" if value is None else (value.text or "")
            if cell.attrib.get("t") == "s":
                text = shared_strings[int(text)]
            elif cell.attrib.get("t") == "inlineStr":
                text = "".join(
                    node.text or ""
                    for node in cell.iter(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                    )
                )
            values[_column_number(cell.attrib.get("r"))] = text
        rows[int(row.attrib["r"])] = values
    return rows


def _parse_number(value, *, field):
    text = str(value or "").strip().replace(",", "")
    if text in _MISSING:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"薪資 Excel 欄位 {field!r} 數值無法解析：{value!r}") from exc


def _parse_age_label(label):
    raw = _normalise_text(label)
    if raw == "全體":
        return None, None
    match = re.fullmatch(r"未滿(\d{1,3})歲?", raw)
    if match:
        return None, int(match.group(1)) - 1
    match = re.fullmatch(r"(\d{1,3})[-~](\d{1,3})歲?", raw)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"薪資 Excel 年齡區間上下界顛倒：{label!r}")
        return lower, upper
    match = re.fullmatch(r"(\d{1,3})歲?(?:以上|及以上)", raw)
    if match:
        return int(match.group(1)), None
    raise ValueError(f"薪資 Excel 年齡標籤無法解析：{label!r}")


def _header_columns(rows):
    measure_by_column = {}
    age_by_column = {}
    measure = ""
    age = ""
    for column in range(2, 80):
        row4 = _normalise_text(rows.get(4, {}).get(column, ""))
        row5 = _normalise_text(rows.get(5, {}).get(column, ""))
        row6 = _normalise_text(rows.get(6, {}).get(column, ""))
        if row4:
            measure = row4
        if row6:
            age = row6
        elif row5:
            age = row5
        if measure and age:
            if measure not in {"平均數", "中位數"}:
                raise ValueError(f"薪資 Excel 出現未知統計量：{measure!r}")
            measure_by_column[column] = measure
            age_by_column[column] = age
    if not measure_by_column:
        raise RuntimeError("薪資 Excel 找不到平均數／中位數表頭")
    return measure_by_column, age_by_column


def _sheet_year(rows, sheet_name):
    sample = " ".join(
        str(rows.get(number, {}).get(column, ""))
        for number in range(1, 7)
        for column in range(1, 20)
    )
    match = re.search(r"(\d{3})年", sample)
    if not match:
        raise ValueError(f"薪資 Excel 工作表 {sheet_name!r} 找不到民國年度")
    return int(match.group(1))


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
                f"薪資尺度異常：{label} 民國 {year}={value}，"
                f"其餘年度中位數={median}，超過三倍"
            )


def transform_xlsx(raw, *, data_time):
    rows = []
    numeric_input = 0
    numeric_output = 0
    input_sum = 0.0
    output_sum = 0.0
    year_values = {"mean": {}, "median": {}}
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        shared_strings = _shared_strings(archive)
        sheets = _workbook_sheets(archive)
        if not sheets:
            raise RuntimeError("薪資 Excel 沒有工作表")
        found_years = set()
        for sheet_name, sheet_path in sheets:
            sheet_rows = _read_sheet(archive, sheet_path, shared_strings)
            year = _sheet_year(sheet_rows, sheet_name)
            if year not in TARGET_ROC_YEARS:
                continue
            found_years.add(year)
            measure_by_column, age_by_column = _header_columns(sheet_rows)
            year_ad = year + 1911
            base = {
                "period_start": f"{year_ad:04d}-01-01",
                "period_end": f"{year_ad:04d}-12-31",
                "period_type": "year",
                "area_code": AREA_CODE,
                "area_level": AREA_LEVEL,
                "gender": "total",
                "unit": "萬元",
                "data_time": data_time,
            }
            for row_number in sorted(sheet_rows):
                if row_number < 7:
                    continue
                raw_item = str(sheet_rows[row_number].get(1, "")).strip()
                item = _normalise_text(raw_item)
                if not item:
                    continue
                for column, measure in measure_by_column.items():
                    age_raw = age_by_column[column]
                    age_lower, age_upper = _parse_age_label(age_raw)
                    value = _parse_number(
                        sheet_rows[row_number].get(column, ""),
                        field=f"{sheet_name}!{column}:{row_number}",
                    )
                    if value is None:
                        continue
                    numeric_input += 1
                    input_sum += value
                    value_type = "mean" if measure == "平均數" else "median"
                    year_values[value_type][year] = (
                        value
                        if item == "全體" and age_raw == "全體"
                        else year_values[value_type].get(year, 0.0)
                    )
                    rows.append(
                        {
                            **base,
                            "indicator_id": f"employee_salary_{value_type}_by_age",
                            "age_lower": age_lower,
                            "age_upper": age_upper,
                            "age_band_raw": None if age_raw == "全體" else age_raw,
                            "breakdown": json.dumps(
                                {
                                    "industry_scope": item,
                                    "industry_label_raw": raw_item,
                                    "age_scope": age_raw,
                                    "measure_group": measure,
                                    "source_sheet": sheet_name,
                                    "source_cell": f"{column}:{row_number}",
                                    "source_unit": "萬元",
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            "value": value,
                            "value_type": value_type,
                        }
                    )
                    numeric_output += 1
                    output_sum += value
        if found_years != TARGET_ROC_YEARS:
            raise RuntimeError(
                "薪資表2 缺少指定年度："
                f"expected={sorted(TARGET_ROC_YEARS)}, found={sorted(found_years)}"
            )

    if numeric_input != numeric_output or abs(input_sum - output_sum) > 1e-9:
        raise ValueError(
            "受僱員工薪資對帳失敗："
            f"輸入 {numeric_input}/{input_sum}，輸出 {numeric_output}/{output_sum}"
        )
    for value_type, values in year_values.items():
        _scale_guard(values, value_type)
    if not rows:
        raise RuntimeError("受僱員工薪資沒有產出資料")
    return rows, {
        "source_years": sorted(found_years),
        "input_numeric_cells": numeric_input,
        "emitted_rows": numeric_output,
        "input_value_sum": input_sum,
        "emitted_value_sum": output_sum,
        "industry_scopes": len(
            {
                json.loads(row["breakdown"])["industry_scope"] for row in rows
            }
        ),
        "mean_total_salary_2024_wan": year_values["mean"].get(113),
        "median_total_salary_2024_wan": year_values["median"].get(113),
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
        if row["age_lower"] is None and row["age_upper"] is None:
            if row["age_band_raw"] is not None:
                raise ValueError(f"契約第 {row_number} 列無年齡時不可保留年齡原始標籤")
        elif row["age_band_raw"] is None:
            raise ValueError(f"契約第 {row_number} 列有年齡區間卻缺少原始標籤")
        if row["gender"] != "total" or row["area_code"] != AREA_CODE:
            raise ValueError(f"契約第 {row_number} 列主體／區域不符")
        json.loads(row["breakdown"])
    return True


def fetch_and_transform(*, data_time=None):
    if data_time is None:
        data_time = datetime.now().astimezone().isoformat()
    page = _http_get(SOURCE_PAGE, timeout=60)
    xlsx_url = _resolve_table2_url(page.text)
    return transform_xlsx(_http_get(xlsx_url, timeout=180).content, data_time=data_time)


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
    print(f"employee salary diagnostics =========== {diagnostics}")
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
        dag_folder="youth_employee_salary_age_tw",
    )
    dag.create_dag(etl_func=_transfer)
