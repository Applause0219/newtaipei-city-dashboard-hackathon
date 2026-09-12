"""共用的 P0 青年資料來源解析器。

這個模組只放無 Airflow／資料庫依賴的擷取後解析與契約轉換，讓 DAG
可以用小型輸入做離線驗證，也避免每支 DAG 各自處理相同的 XML、年齡
區間與數值防護。
"""

from __future__ import annotations

import calendar
import io
import json
import math
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

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

TAIPEI_TZ = timezone(timedelta(hours=8))
GENDER_LABELS = {"M": "male", "F": "female", "男": "male", "女": "female"}
EDUCATION_LABELS = {
    "E1314": "博士",
    "E1112": "碩士",
    "E2122": "大學院校",
    "E3_4_5": "專科",
    "E6_7": "高中職",
    "E8_9": "國中初職",
    "E1_2": "小學",
    "E03": "自修",
    "E04": "不識字",
    "E99": "不詳",
}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)


def default_data_time() -> str:
    return datetime.now(TAIPEI_TZ).isoformat()


def fetch_data_gov_resource(
    session,
    dataset_id: int,
    *,
    description_contains: str | None = None,
    fallback_url: str | None = None,
):
    """從 data.gov.tw 目錄取得目前資源 URL，失敗時使用已驗證的 fallback。"""

    metadata_url = f"https://data.gov.tw/api/v2/rest/dataset/{dataset_id}"
    try:
        response = session.get(metadata_url, headers={"User-Agent": UA}, timeout=60)
        response.raise_for_status()
        result = response.json().get("result", {})
        for resource in result.get("distribution", []):
            description = str(resource.get("resourceDescription", ""))
            url = resource.get("resourceDownloadUrl")
            if url and (
                description_contains is None
                or description_contains in description
            ):
                return url
    except Exception as exc:
        if fallback_url is None:
            raise RuntimeError(f"無法取得 data.gov.tw {dataset_id} 資源網址") from exc
    if fallback_url is None:
        raise RuntimeError(f"data.gov.tw {dataset_id} 沒有可用資源網址")
    return fallback_url


def fetch_bytes(session, url: str, *, attempts: int = 3, timeout: int = 180) -> bytes:
    """以有限重試抓取來源，HTTP 200 但空內容仍視為錯誤。"""

    import requests

    last_error = None
    for attempt in range(attempts):
        try:
            response = session.get(url, headers={"User-Agent": UA}, timeout=timeout)
            response.raise_for_status()
            if not response.content:
                raise ValueError("來源回傳空內容")
            return response.content
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"抓取來源失敗：{url}") from last_error


def _text(value, field: str) -> str:
    result = "" if value is None else str(value).strip()
    if not result or result.lower() in {"nan", "none", "null"}:
        raise ValueError(f"P0 來源欄位 {field} 為空")
    return result


def number(value, field: str, *, dash_is_zero: bool = False) -> float:
    raw = "" if value is None else str(value).strip()
    if dash_is_zero and raw == "-":
        # 內政部統計服務以「-」表示該單齡沒有個案；轉成數值 0，
        # 讓男女對帳可以保留，並由呼叫端在 breakdown 留下來源符號。
        return 0.0
    if not raw or raw.lower() in {"nan", "none", "null", "-"}:
        raise ValueError(f"P0 來源欄位 {field} 為空或不可用：{value!r}")
    try:
        result = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"P0 來源欄位 {field} 無法轉數值：{value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"P0 來源欄位 {field} 不是有限數值：{value!r}")
    if result < 0:
        raise ValueError(f"P0 來源欄位 {field} 不可為負數：{value!r}")
    return result


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(parent, name: str):
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def parse_stat_xml(payload: bytes):
    """解析 SEGIS XML，回傳欄位名稱、資料列與 Info。

    來源偶爾會以 ``<string>`` 包住 JSON 錯誤訊息；這裡明確失敗，避免
    把錯誤頁當成空資料入庫。
    """

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("統計區來源不是合法 XML") from exc

    if _local_name(root.tag) == "string":
        message = (root.text or "").strip()
        raise ValueError(f"統計區來源回傳錯誤：{message[:500]}")

    info_element = _child(root, "Info")
    column_list = _child(root, "ColumnList")
    row_list = _child(root, "RowDataList")
    if info_element is None or column_list is None or row_list is None:
        raise ValueError("統計區 XML 缺少 Info、ColumnList 或 RowDataList")

    info = {
        _local_name(child.tag): (child.text or "").strip()
        for child in list(info_element)
    }
    columns = []
    for column in list(column_list):
        name = _child(column, "COLUMN_NAME")
        if name is None or not (name.text or "").strip():
            raise ValueError("統計區 XML 出現沒有名稱的欄位")
        columns.append(name.text.strip())

    rows = []
    for row_element in list(row_list):
        row = {
            _local_name(child.tag): (child.text or "").strip()
            for child in list(row_element)
        }
        if row:
            rows.append(row)
    if not rows:
        raise ValueError("統計區 XML 沒有資料列")
    return columns, rows, info


def parse_stat_period(raw_period: str):
    raw = _text(raw_period, "INFO_TIME")
    match = re.fullmatch(r"(\d{3})Y(\d{1,2})M", raw)
    if not match:
        match = re.fullmatch(r"(\d{3})年(?:(\d{1,2})月)?", raw)
    if not match:
        raise ValueError(f"無法解析統計區資料時間：{raw_period!r}")
    roc_year = int(match.group(1))
    month = int(match.group(2) or 0)
    year = roc_year + 1911
    if month == 0:
        return f"{year}-01-01", f"{year}-12-31", roc_year
    if not 1 <= month <= 12:
        raise ValueError(f"統計區月份超出範圍：{raw_period!r}")
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}", roc_year


def parse_age_code(age_code: str):
    raw = _text(age_code, "age_code")
    if raw in {"A65UP", "A65UP_5"}:
        return 65, None, "65歲以上"
    if raw in {"A100UP", "A100UP_5"}:
        return 100, None, "100歲以上"
    match = re.fullmatch(r"A(\d+)A(\d+)", raw)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"統計區年齡欄位上下界顛倒：{age_code!r}")
        return lower, upper, f"{lower}-{upper}歲"
    match = re.fullmatch(r"A(\d+)", raw)
    if match:
        age = int(match.group(1))
        return age, age, f"{age}歲"
    raise ValueError(f"無法解析統計區年齡欄位：{age_code!r}")


def _gender_column(columns, age_code: str, gender_code: str) -> str:
    """找出統計區寬表的性別欄名。

    SEGIS 對一般年齡組使用 ``A20A24_M_CNT``，但對「100歲以上」
    的欄位會把 ``_5`` 放在性別代碼之後，形成
    ``A100UP_M_5_CNT``；兩種命名都要支援。
    """

    candidates = [f"{age_code}_{gender_code}_CNT"]
    if age_code.endswith("_5"):
        candidates.append(f"{age_code[:-2]}_{gender_code}_5_CNT")
    for candidate in candidates:
        if candidate in columns:
            return candidate
    raise ValueError(
        f"統計區來源缺少 {age_code} 的性別欄位：{gender_code}，"
        f"候選欄名={candidates}"
    )


def _base_row(
    *,
    indicator_id: str,
    period_start: str,
    period_end: str,
    period_type: str = "year",
    age_lower,
    age_upper,
    age_band_raw: str,
    gender: str | None,
    area_code: str,
    area_level: str,
    breakdown: dict,
    value: float,
    unit: str = "人",
    value_type: str = "count",
    data_time: str | None = None,
):
    return {
        "indicator_id": indicator_id,
        "period_start": period_start,
        "period_end": period_end,
        "period_type": period_type,
        "age_lower": age_lower,
        "age_upper": age_upper,
        "age_band_raw": age_band_raw,
        "gender": gender,
        "area_code": area_code,
        "area_level": area_level,
        "breakdown": json.dumps(breakdown, ensure_ascii=False, sort_keys=True),
        "value": value,
        "unit": unit,
        "value_type": value_type,
        "data_time": data_time or default_data_time(),
    }


def build_stat_area_population_data(
    columns,
    source_rows,
    *,
    dataset_id: str,
    source_url: str,
    data_time: str | None = None,
):
    """將 18369 的統計區年齡 × 性別寬表轉為長格式。

    18369 已包含總計、男性、女性，因此也涵蓋 18255 的人口總量用途，
    不再把兩份相同人口母體重複寫入事實層。
    """

    import pandas as pd

    required = {"INFO_TIME", "CODE2"}
    if not required.issubset(columns):
        raise ValueError(f"統計區人口來源缺少欄位：{sorted(required - set(columns))}")
    total_columns = [
        column
        for column in columns
        if re.fullmatch(r"A(?:\d+A\d+|\d+|(?:65|100)UP(?:_5)?)_CNT", column)
    ]
    if not total_columns:
        raise ValueError("統計區人口來源找不到年齡總計欄位")

    rows = []
    period_keys = set()
    for source_row in source_rows:
        period_start, period_end, _ = parse_stat_period(source_row.get("INFO_TIME"))
        period_keys.add((period_start, period_end))
        area_code = _text(source_row.get("CODE2"), "CODE2")
        for total_column in total_columns:
            age_code = total_column[:-4]
            male_column = _gender_column(columns, age_code, "M")
            female_column = _gender_column(columns, age_code, "F")
            age_lower, age_upper, age_label = parse_age_code(age_code)
            gender_values = {
                "total": number(source_row.get(total_column), total_column),
                "male": number(source_row.get(male_column), male_column),
                "female": number(source_row.get(female_column), female_column),
            }
            if abs(gender_values["total"] - gender_values["male"] - gender_values["female"]) > 0.5:
                raise ValueError(
                    f"統計區人口男女對帳失敗：{area_code} {age_label} "
                    f"total={gender_values['total']} male={gender_values['male']} "
                    f"female={gender_values['female']}"
                )
            for gender, value in gender_values.items():
                rows.append(
                    _base_row(
                        indicator_id="statistical_area_population_count",
                        period_start=period_start,
                        period_end=period_end,
                        age_lower=age_lower,
                        age_upper=age_upper,
                        age_band_raw=age_label,
                        gender=gender,
                        area_code=area_code,
                        area_level="statistical_area_2",
                        breakdown={
                            "source_dataset": dataset_id,
                            "source_url": source_url,
                            "statistical_area_code": area_code,
                            "source_age_code": age_code,
                        },
                        value=value,
                        data_time=data_time,
                    )
                )

    if len(period_keys) != 1:
        raise ValueError(f"統計區人口單次資源含多個資料時間：{sorted(period_keys)}")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    _assert_nonempty_area_data(data, "統計區人口")
    return data


def build_stat_area_school_age_data(
    columns,
    source_rows,
    *,
    dataset_id: str,
    source_url: str,
    data_time: str | None = None,
):
    """將 18680 的統計區單歲 0–26 歲資料轉為長格式。"""

    import pandas as pd

    required = {"INFO_TIME", "CODE2"}
    if not required.issubset(columns):
        raise ValueError(f"統計區學齡來源缺少欄位：{sorted(required - set(columns))}")
    total_columns = [
        column for column in columns if re.fullmatch(r"A\d+_CNT", column)
    ]
    if not total_columns:
        raise ValueError("統計區學齡來源找不到單歲總計欄位")

    rows = []
    period_keys = set()
    for source_row in source_rows:
        period_start, period_end, _ = parse_stat_period(source_row.get("INFO_TIME"))
        period_keys.add((period_start, period_end))
        area_code = _text(source_row.get("CODE2"), "CODE2")
        for total_column in total_columns:
            age_code = total_column[:-4]
            male_column = _gender_column(columns, age_code, "M")
            female_column = _gender_column(columns, age_code, "F")
            age_lower, age_upper, age_label = parse_age_code(age_code)
            gender_values = {
                "total": number(source_row.get(total_column), total_column),
                "male": number(source_row.get(male_column), male_column),
                "female": number(source_row.get(female_column), female_column),
            }
            if abs(gender_values["total"] - gender_values["male"] - gender_values["female"]) > 0.5:
                raise ValueError(
                    f"統計區學齡人口男女對帳失敗：{area_code} {age_label}"
                )
            for gender, value in gender_values.items():
                rows.append(
                    _base_row(
                        indicator_id="statistical_area_school_age_population_count",
                        period_start=period_start,
                        period_end=period_end,
                        age_lower=age_lower,
                        age_upper=age_upper,
                        age_band_raw=age_label,
                        gender=gender,
                        area_code=area_code,
                        area_level="statistical_area_2",
                        breakdown={
                            "source_dataset": dataset_id,
                            "source_url": source_url,
                            "statistical_area_code": area_code,
                            "source_age_code": age_code,
                        },
                        value=value,
                        data_time=data_time,
                    )
                )

    if len(period_keys) != 1:
        raise ValueError(f"統計區學齡資源含多個資料時間：{sorted(period_keys)}")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    _assert_nonempty_area_data(data, "統計區學齡")
    return data


def build_stat_area_education_data(
    columns,
    source_rows,
    *,
    dataset_id: str,
    source_url: str,
    data_time: str | None = None,
):
    """將 18633 的年齡 × 性別 × 教育程度寬表轉為長格式。"""

    import pandas as pd

    required = {"INFO_TIME", "CODE2"}
    if not required.issubset(columns):
        raise ValueError(f"統計區教育來源缺少欄位：{sorted(required - set(columns))}")
    pattern = re.compile(
        r"(?P<gender>[MF])_(?P<age>A(?:\d+A\d+|65UP))_"
        r"(?P<education>E(?:1314|1112|2122|3_4_5|6_7|8_9|1_2|03|04|99))_CNT"
    )
    specs = [(column, pattern.fullmatch(column)) for column in columns]
    specs = [(column, match) for column, match in specs if match]
    if not specs:
        raise ValueError("統計區教育來源找不到年齡 × 性別 × 教育欄位")

    rows = []
    period_keys = set()
    for source_row in source_rows:
        period_start, period_end, _ = parse_stat_period(source_row.get("INFO_TIME"))
        period_keys.add((period_start, period_end))
        area_code = _text(source_row.get("CODE2"), "CODE2")
        for column, match in specs:
            values = match.groupdict()
            age_lower, age_upper, age_label = parse_age_code(values["age"])
            gender = GENDER_LABELS[values["gender"]]
            education_code = values["education"]
            rows.append(
                _base_row(
                    indicator_id="statistical_area_education_attainment_count",
                    period_start=period_start,
                    period_end=period_end,
                    age_lower=age_lower,
                    age_upper=age_upper,
                    age_band_raw=age_label,
                    gender=gender,
                    area_code=area_code,
                    area_level="statistical_area_2",
                    breakdown={
                        "source_dataset": dataset_id,
                        "source_url": source_url,
                        "statistical_area_code": area_code,
                        "source_age_code": values["age"],
                        "education_code": education_code,
                        "education_label": EDUCATION_LABELS[education_code],
                        "source_field": column,
                    },
                    value=number(source_row.get(column), column),
                    data_time=data_time,
                )
            )

    if len(period_keys) != 1:
        raise ValueError(f"統計區教育資源含多個資料時間：{sorted(period_keys)}")

    # 來源只有男／女欄位；補 total 方便查詢端使用，並在 breakdown 明確標示
    # 這是由來源男女欄位相加的衍生列，而不是第三個原始欄位。
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    totals = []
    data["_education_code"] = data["breakdown"].map(
        lambda value: json.loads(value)["education_code"]
    )
    data["_source_age_code"] = data["breakdown"].map(
        lambda value: json.loads(value)["source_age_code"]
    )
    group_columns = [
        "period_start", "period_end", "age_lower", "age_upper", "age_band_raw",
        "area_code", "area_level", "_source_age_code", "_education_code",
    ]
    for _, group in data.groupby(group_columns, dropna=False, sort=False):
        genders = set(group["gender"])
        if genders != {"male", "female"}:
            raise ValueError("統計區教育來源某格缺少男／女其中一類")
        base = group.iloc[0].to_dict()
        breakdown = json.loads(base["breakdown"])
        breakdown["derived_gender_total"] = True
        base["gender"] = "total"
        base["breakdown"] = json.dumps(breakdown, ensure_ascii=False, sort_keys=True)
        base["value"] = float(group["value"].sum())
        totals.append(base)
    data = data.drop(columns=["_education_code", "_source_age_code"])
    data = pd.concat([data, pd.DataFrame(totals, columns=CONTRACT_COLUMNS)], ignore_index=True)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    _assert_nonempty_area_data(data, "統計區教育")
    return data


def _assert_nonempty_area_data(data, name: str) -> None:
    if data.empty:
        raise RuntimeError(f"{name}轉換後沒有資料")
    if data["area_code"].isna().any() or (data["area_code"].astype(str).str.len() == 0).any():
        raise ValueError(f"{name}出現空的 area_code")
    if (data["value"] < 0).any():
        raise ValueError(f"{name}出現負數")


def _normalise_header(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip()


def _parse_moi_age_header(header: str):
    raw = _normalise_header(header).replace("_統計", "").replace("統計", "")
    raw = raw.replace(" ", "")
    if raw in {"不詳", "不明"}:
        return None
    match = re.fullmatch(r"(\d+)歲以上", raw)
    if match:
        return int(match.group(1)), None, raw
    match = re.fullmatch(r"(\d+)歲", raw)
    if match:
        age = int(match.group(1))
        return age, age, raw
    raise ValueError(f"無法解析內政部單齡欄位：{header!r}")


def _parse_moi_period(raw_period: str):
    """解析戶政統計列的年度、月份及年初至某月累計標籤。"""

    raw = _normalise_header(raw_period).replace(" ", "")
    match = re.fullmatch(r"(\d{2,3})年\((\d{1,2})~(\d{1,2})月\)", raw)
    if match:
        roc_year, start_month, end_month = map(int, match.groups())
        if not 1 <= start_month <= end_month <= 12:
            raise ValueError(f"內政部統計累計月份超出範圍：{raw_period!r}")
        year = roc_year + 1911
        return (
            f"{year}-{start_month:02d}-01",
            f"{year}-{end_month:02d}-{calendar.monthrange(year, end_month)[1]:02d}",
            "cumulative_month",
            roc_year,
        )
    match = re.fullmatch(r"(\d{2,3})年(\d{1,2})月", raw)
    if match:
        roc_year, month = map(int, match.groups())
        if not 1 <= month <= 12:
            raise ValueError(f"內政部統計月份超出範圍：{raw_period!r}")
        year = roc_year + 1911
        return (
            f"{year}-{month:02d}-01",
            f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
            "month",
            roc_year,
        )
    match = re.fullmatch(r"(\d{2,3})年", raw)
    if match:
        roc_year = int(match.group(1))
        year = roc_year + 1911
        return f"{year}-01-01", f"{year}-12-31", "year", roc_year
    raise ValueError(f"無法解析內政部統計資料時間：{raw_period!r}")


def build_moi_single_age_data(
    payload: bytes,
    *,
    dataset_id: str,
    source_url: str,
    data_time: str | None = None,
):
    """解析 14226／9606 的官方 CSV 快照，只保留新北市三種性別列。"""

    import pandas as pd

    try:
        frame = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig")
    except Exception as exc:  # pandas 會依版本丟不同解析例外
        raise ValueError(f"內政部單齡 CSV 無法解析：{exc}") from exc
    if frame.shape[1] < 4:
        raise ValueError("內政部單齡 CSV 欄位數不足")
    frame.columns = [_normalise_header(column) for column in frame.columns]
    label_column = frame.columns[0]
    age_columns = [column for column in frame.columns[2:] if _parse_moi_age_header(column)]
    if not age_columns:
        raise ValueError("內政部單齡 CSV 找不到年齡欄位")

    rows = []
    expected_by_age = {}
    for _, source_row in frame.iterrows():
        label = _text(source_row[label_column], label_column)
        parts = [part.strip() for part in label.split("/")]
        if len(parts) < 3 or parts[1] != "新北市":
            continue
        period_start, period_end, period_type, _roc_year = _parse_moi_period(parts[0])
        gender_label = parts[2]
        gender = {"性別總計": "total", "男": "male", "女": "female"}.get(gender_label)
        if gender is None:
            raise ValueError(f"內政部單齡出現未知性別：{gender_label!r}")
        for column in age_columns:
            parsed_age = _parse_moi_age_header(column)
            if parsed_age is None:
                continue
            age_lower, age_upper, age_label = parsed_age
            raw_value = str(source_row[column]).strip()
            value = number(source_row[column], column, dash_is_zero=True)
            key = (period_type, period_start, period_end, age_lower, age_upper)
            expected_by_age.setdefault(key, {})[gender] = value
            breakdown = {
                "source_dataset": dataset_id,
                "source_url": source_url,
                "source_row_label": label,
                "source_age_field": column,
            }
            if raw_value == "-":
                breakdown["source_value_status"] = "zero_symbol"
            rows.append(
                _base_row(
                    indicator_id=(
                        "population_single_age_count"
                        if dataset_id == "14226"
                        else "indigenous_population_single_age_count"
                    ),
                    period_start=period_start,
                    period_end=period_end,
                    period_type=period_type,
                    age_lower=age_lower,
                    age_upper=age_upper,
                    age_band_raw=age_label,
                    gender=gender,
                    area_code="65000",
                    area_level="city",
                    breakdown=breakdown,
                    value=value,
                    data_time=data_time,
                )
            )

    if not rows:
        raise ValueError("內政部單齡 CSV 找不到新北市資料")
    for key, values in expected_by_age.items():
        if set(values) != {"total", "male", "female"}:
            raise ValueError(f"內政部單齡某年齡缺少性別列：{key} {set(values)}")
        if abs(values["total"] - values["male"] - values["female"]) > 0.5:
            raise ValueError(f"內政部單齡男女對帳失敗：{key} {values}")

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    return data


def _parse_consumption_age(label: str):
    raw = _normalise_header(label)
    match = re.fullmatch(r"未滿(\d+)歲", raw)
    if match:
        return None, int(match.group(1)) - 1, raw
    match = re.fullmatch(r"(\d+)\(含\)-(\d+)歲", raw)
    if match:
        lower, exclusive_upper = int(match.group(1)), int(match.group(2))
        if lower >= exclusive_upper:
            raise ValueError(f"聯卡中心年齡區間無效：{label!r}")
        return lower, exclusive_upper - 1, raw
    match = re.fullmatch(r"(\d+)\(含\)歲以上", raw)
    if match:
        return int(match.group(1)), None, raw
    raise ValueError(f"無法解析聯卡中心年齡層：{label!r}")


def build_card_consumption_data(
    payload: bytes,
    *,
    source_url: str,
    data_time: str | None = None,
):
    """解析 NCCC 38319，輸出新北市持卡人交易筆數與金額兩個指標。"""

    import pandas as pd

    try:
        frame = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig")
    except Exception as exc:
        raise ValueError(f"聯卡中心 CSV 無法解析：{exc}") from exc
    frame.columns = [_normalise_header(column) for column in frame.columns]
    aliases = {
        "年月": "year_month",
        "地區": "area_code",
        "信用卡產業別": "industry",
        "年齡層": "age_label",
        "信用卡交易筆數": "transaction_count",
        "信用卡交易金額[新臺幣]": "transaction_amount",
    }
    missing = set(aliases) - set(frame.columns)
    if missing:
        raise ValueError(f"聯卡中心來源缺少欄位：{sorted(missing)}")
    frame = frame.rename(columns=aliases)
    frame["area_code"] = frame["area_code"].map(
        lambda value: str(int(float(value))) if str(value).strip() else ""
    )
    frame = frame[frame["area_code"] == "65000000"].copy()
    if frame.empty:
        raise ValueError("聯卡中心來源找不到新北市 65000000")

    rows = []
    for _, source_row in frame.iterrows():
        period_raw = _text(source_row["year_month"], "年月")
        if not re.fullmatch(r"\d{6}", period_raw):
            raise ValueError(f"聯卡中心年月格式錯誤：{period_raw!r}")
        year, month = int(period_raw[:4]), int(period_raw[4:])
        if not 1 <= month <= 12:
            raise ValueError(f"聯卡中心月份超出範圍：{period_raw!r}")
        last_day = calendar.monthrange(year, month)[1]
        age_lower, age_upper, age_label = _parse_consumption_age(source_row["age_label"])
        industry = _text(source_row["industry"], "信用卡產業別")
        breakdown = {
            "source_area_code": "65000000",
            "source_url": source_url,
            "industry": industry,
            "source_age_label": age_label,
        }
        common = {
            "period_start": f"{year}-{month:02d}-01",
            "period_end": f"{year}-{month:02d}-{last_day:02d}",
            "age_lower": age_lower,
            "age_upper": age_upper,
            "age_band_raw": age_label,
            "gender": None,
            "area_code": "65000",
            "area_level": "city",
            "breakdown": breakdown,
            "data_time": data_time,
        }
        rows.extend(
            [
                {
                    **common,
                    "indicator_id": "card_transaction_count",
                    "value": number(source_row["transaction_count"], "信用卡交易筆數"),
                    "unit": "筆",
                    "value_type": "count",
                },
                {
                    **common,
                    "indicator_id": "card_transaction_amount",
                    "value": number(source_row["transaction_amount"], "信用卡交易金額[新臺幣]"),
                    "unit": "新臺幣",
                    "value_type": "sum",
                },
            ]
        )

    data = pd.DataFrame(rows)
    data["period_type"] = "month"
    data["breakdown"] = data["breakdown"].map(
        lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    data = data[CONTRACT_COLUMNS]
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    _assert_nonempty_area_data(data, "聯卡中心消費")
    return data


__all__ = [
    "CONTRACT_COLUMNS",
    "EDUCATION_LABELS",
    "UA",
    "build_card_consumption_data",
    "build_moi_single_age_data",
    "build_stat_area_education_data",
    "build_stat_area_population_data",
    "build_stat_area_school_age_data",
    "default_data_time",
    "fetch_bytes",
    "fetch_data_gov_resource",
    "parse_stat_xml",
]
