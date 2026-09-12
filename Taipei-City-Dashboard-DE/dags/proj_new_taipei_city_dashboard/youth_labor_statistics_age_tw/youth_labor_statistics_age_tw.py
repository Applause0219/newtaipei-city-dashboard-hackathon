"""勞動部青年（15--29 歲）勞動統計 SDMX-JSON ETL。

本模組只輸出全國青年基準，不把全國數字冒充新北市資料。來源查詢固定
性別與年齡維度、一次取得八項指標，避免該服務將多維度選取壓平成單一
observation 陣列時產生歧義；API 結構或資料期別不完整時直接失敗。
"""

import calendar
import json
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    from operators.common_pipeline import CommonDag
except ImportError:  # pragma: no cover - allows local parser verification
    CommonDag = None


PORTAL_URL = (
    "https://statdb.mol.gov.tw/statiscla/webMain.aspx?sys=100&funid="
    "singleAnyPage&titlename=%E9%9D%92%E5%B9%B4%E5%8B%9E%E5%8B%95%E7%B5%B1%E8%A8%88%E6%9F%A5%E8%A9%A2&pageid=amq01"
)
API_BASE_URL = "https://statdb.mol.gov.tw/statiscla/webMain.aspx"
API_DATASET_ID = "amq01"
API_METADATA_URL = f"{API_BASE_URL}?sdmx/{API_DATASET_ID}/all"
API_DEFINITION_URL = (
    f"{API_BASE_URL}?sys=212&x=2100&funid={API_DATASET_ID}&r=14213847"
)
API_FIELD_IDS = ("1", "2", "3", "4", "5", "6", "7", "8")
API_GENDER_IDS = ("1", "2", "3")
API_AGE_IDS = ("1", "2", "3", "4")
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

INDICATOR_SPECS = {
    "1": ("youth_civilian_population_count", "千人", "count"),
    "2": ("youth_labor_force_count", "千人", "count"),
    "3": ("youth_labor_force_participation_rate", "%", "rate"),
    "4": ("youth_employed_count", "千人", "count"),
    "5": ("youth_employment_rate", "%", "rate"),
    "6": ("youth_unemployed_count", "千人", "count"),
    "7": ("youth_unemployment_rate", "%", "rate"),
    "8": ("youth_non_labor_force_count", "千人", "count"),
}

GENDER_BY_ID = {
    "1": "total",
    "2": "male",
    "3": "female",
}

AGE_BY_ID = {
    "1": ((15, 29), "總計"),
    "2": ((15, 19), "15~19歲"),
    "3": ((20, 24), "20~24歲"),
    "4": ((25, 29), "25~29歲"),
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MONTH_RE = re.compile(r"^(?P<year>\d{4})-M(?P<month>0?[1-9]|1[0-2])$")


def _http_bytes(url, *, accept="application/json,*/*", timeout=180):
    request = Request(url, headers={"User-Agent": UA, "Accept": accept})
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers.get_content_type()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == 2:
                break
    raise RuntimeError(f"官方來源請求失敗 {url}：{last_error}") from last_error


def _api_json(url):
    payload, content_type = _http_bytes(url)
    if content_type not in {"application/json", "text/json"}:
        raise RuntimeError(
            f"勞動部統計 API Content-Type 異常：{content_type}；url={url}"
        )
    try:
        return json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"勞動部統計 API JSON 無法解析：{url}") from exc


def _api_url(filter_expression, start_time, end_time):
    # + 是同一維度多選；放在 query key 中必須 percent-encode，否則會被
    # ASP.NET query parser 當成空白。
    encoded_filter = quote(filter_expression, safe=".")
    url = (
        f"{API_BASE_URL}?sdmx/{API_DATASET_ID}/{encoded_filter}"
        f"&startTime={quote(start_time, safe='-')}"
        f"&endTime={quote(end_time, safe='-')}"
    )
    if len(url) > 1000:
        raise ValueError(f"勞動部統計 API URL 超過官方限制：{len(url)}")
    return url


def _series_dimensions(payload, *, url):
    try:
        dimensions = payload["data"]["structure"]["dimensions"]
        series_dimensions = dimensions["series"]
        observations = dimensions["observation"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"勞動部統計 API 缺少 SDMX 結構：{url}") from exc
    if len(series_dimensions) != 3 or [d.get("id") for d in series_dimensions] != [
        "fldid",
        "code1",
        "code2",
    ]:
        raise RuntimeError(
            "勞動部青年 API 維度已變更；預期 fldid/code1/code2，"
            f"實際={[d.get('id') for d in series_dimensions]}；url={url}"
        )
    if len(observations) != 1 or observations[0].get("id") != "ym":
        raise RuntimeError(f"勞動部青年 API 統計期維度已變更：{url}")
    return series_dimensions, observations[0]["values"]


def _dimension_values(dimension, *, label, expected_ids):
    values = dimension.get("values")
    if not isinstance(values, list):
        raise TypeError(f"勞動部青年 API {label} 維度沒有 values")
    result = [(str(item.get("id")), item.get("name", "")) for item in values]
    if {item[0] for item in result} != set(expected_ids):
        raise RuntimeError(
            f"勞動部青年 API {label} 項目已變更："
            f"實際={[item[0] for item in result]}、預期={list(expected_ids)}"
        )
    return result


def _monthly_period_bounds(period_values):
    periods = []
    for item in period_values:
        period_id = str(item.get("id", ""))
        match = _MONTH_RE.fullmatch(period_id)
        if match:
            periods.append((int(match.group("year")), int(match.group("month")), period_id))
    if not periods:
        raise RuntimeError("勞動部青年 API metadata 沒有月資料期別")
    periods.sort()
    return periods[0][2], periods[-1][2], periods


def _roc_ym_to_api(value, *, label):
    digits = str(int(value)).zfill(4)
    if len(digits) < 4:
        raise RuntimeError(f"勞動部青年 API {label} 年月格式錯誤：{value!r}")
    roc_year = int(digits[:-2])
    month = int(digits[-2:])
    # definition JSON 的 6700 表示頁面所示的 67 年 1 月。
    if month == 0:
        month = 1
    if not 1 <= month <= 12 or not 1 <= roc_year <= 999:
        raise RuntimeError(f"勞動部青年 API {label} 年月超出範圍：{value!r}")
    return f"{roc_year + 1911:04d}-M{month}"


def _definition_history_bounds(definition):
    try:
        minimum = definition["minym"]
        maximum = definition["maxym"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"勞動部青年查詢定義缺少 minym/maxym：{API_DEFINITION_URL}"
        ) from exc
    return (
        _roc_ym_to_api(minimum, label="最早期別"),
        _roc_ym_to_api(maximum, label="最新期別"),
    )


def _period_fields(period_id):
    match = _MONTH_RE.fullmatch(str(period_id))
    if not match:
        raise ValueError(f"非合法的勞動統計月期別：{period_id}")
    year = int(match.group("year"))
    month = int(match.group("month"))
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start.isoformat(), end.isoformat()


def _decimal_value(raw, *, context):
    if isinstance(raw, bool) or raw is None:
        raise ValueError(f"勞動部青年 API 缺少數值：{context}")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"勞動部青年 API 數值格式錯誤：{raw!r}；{context}") from exc
    if not value.is_finite() or value < 0:
        raise ValueError(f"勞動部青年 API 數值非法：{raw!r}；{context}")
    return value


def _append_api_row(
    rows,
    *,
    indicator_id,
    period_id,
    age_id,
    age_label,
    gender_id,
    gender_label,
    value,
    data_time,
    api_url,
    indicator_label,
):
    mapped_indicator, unit, value_type = INDICATOR_SPECS[indicator_id]
    age, fallback_age_label = AGE_BY_ID[age_id]
    if value_type == "rate" and value > 100:
        raise ValueError(
            f"勞動部青年 API 率值超過 100：{value}；"
            f"indicator={indicator_id}, period={period_id}"
        )
    period_start, period_end = _period_fields(period_id)
    rows.append(
        {
            "indicator_id": mapped_indicator,
            "period_start": period_start,
            "period_end": period_end,
            "period_type": "month",
            "age_lower": age[0],
            "age_upper": age[1],
            "age_band_raw": age_label or fallback_age_label,
            "gender": GENDER_BY_ID[gender_id],
            "area_code": AREA_CODE,
            "area_level": AREA_LEVEL,
            "breakdown": json.dumps(
                {
                    "source_api_url": api_url,
                    "source_api_metadata_url": API_METADATA_URL,
                    "source_api_definition_url": API_DEFINITION_URL,
                    "source_dataset_id": API_DATASET_ID,
                    "source_indicator_id": indicator_id,
                    "source_indicator_label": indicator_label,
                    "source_gender_id": gender_id,
                    "source_gender_label": gender_label,
                    "source_age_id": age_id,
                    "source_age_label": age_label or fallback_age_label,
                    "source_age_scope": "15-29歲青年",
                    "source_portal": PORTAL_URL,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "value": float(value),
            "unit": unit,
            "value_type": value_type,
            "data_time": data_time,
        }
    )


def _parse_api_query(
    payload,
    *,
    api_url,
    expected_gender_id,
    expected_age_id,
    data_time,
):
    series_dimensions, period_values = _series_dimensions(payload, url=api_url)
    field_values = _dimension_values(
        series_dimensions[0], label="指標", expected_ids=API_FIELD_IDS
    )
    gender_values = _dimension_values(
        series_dimensions[1], label="性別", expected_ids=(expected_gender_id,)
    )
    age_values = _dimension_values(
        series_dimensions[2], label="年齡", expected_ids=(expected_age_id,)
    )
    if len(gender_values) != 1 or len(age_values) != 1:
        raise RuntimeError(f"勞動部青年 API 查詢未固定性別／年齡：{api_url}")

    period_ids = [str(item.get("id", "")) for item in period_values]
    if not period_ids or any(not _MONTH_RE.fullmatch(item) for item in period_ids):
        raise RuntimeError(f"勞動部青年 API 回傳非月資料或沒有資料：{api_url}")
    series = payload.get("data", {}).get("dataSets", [{}])[0].get("series")
    if not isinstance(series, dict):
        raise TypeError(f"勞動部青年 API 缺少 series：{api_url}")

    rows = []
    seen_fields = set()
    for key, series_data in series.items():
        positions = str(key).split(":")
        if len(positions) != 3:
            raise RuntimeError(f"勞動部青年 API series key 異常：{key!r}；{api_url}")
        try:
            field_index, gender_index, age_index = (int(item) for item in positions)
        except ValueError as exc:
            raise RuntimeError(f"勞動部青年 API series key 非整數：{key!r}") from exc
        if gender_index != 0 or age_index != 0:
            raise RuntimeError(f"勞動部青年 API series 未固定性別／年齡：{key!r}")
        if field_index < 0 or field_index >= len(field_values):
            raise RuntimeError(f"勞動部青年 API 指標索引超出範圍：{key!r}")
        indicator_id, indicator_label = field_values[field_index]
        if indicator_id in seen_fields:
            raise RuntimeError(f"勞動部青年 API 指標重複：{indicator_id}；{api_url}")
        seen_fields.add(indicator_id)
        observations = series_data.get("observations")
        if not isinstance(observations, dict):
            raise TypeError(f"勞動部青年 API 缺少 observations：{key!r}；{api_url}")
        if set(observations) != {str(index) for index in range(len(period_ids))}:
            raise RuntimeError(
                f"勞動部青年 API 期別數與 observation 數不一致："
                f"periods={len(period_ids)}、observations={len(observations)}；{api_url}"
            )
        for index, period_id in enumerate(period_ids):
            observation = observations[str(index)]
            if not isinstance(observation, list) or len(observation) != 1:
                raise RuntimeError(
                    f"勞動部青年 API observation 格式異常：{key}/{index}；{api_url}"
                )
            value = _decimal_value(
                observation[0],
                context=f"{indicator_id}/{expected_gender_id}/{expected_age_id}/{period_id}",
            )
            _append_api_row(
                rows,
                indicator_id=indicator_id,
                period_id=period_id,
                age_id=expected_age_id,
                age_label=age_values[0][1],
                gender_id=expected_gender_id,
                gender_label=gender_values[0][1],
                value=value,
                data_time=data_time,
                api_url=api_url,
                indicator_label=indicator_label,
            )

    if seen_fields != set(API_FIELD_IDS):
        raise RuntimeError(
            f"勞動部青年 API 指標不完整：實際={sorted(seen_fields)}、"
            f"預期={list(API_FIELD_IDS)}；{api_url}"
        )
    return rows, period_ids


def fetch_api_data(*, data_time):
    """抓取 API metadata 與全歷史月資料，回傳契約列及診斷資訊。"""
    metadata = _api_json(API_METADATA_URL)
    definition = _api_json(API_DEFINITION_URL)
    metadata_series, metadata_period_values = _series_dimensions(
        metadata, url=API_METADATA_URL
    )
    _dimension_values(metadata_series[0], label="指標", expected_ids=API_FIELD_IDS)
    _dimension_values(metadata_series[1], label="性別", expected_ids=API_GENDER_IDS)
    _dimension_values(metadata_series[2], label="年齡", expected_ids=API_AGE_IDS)
    metadata_start_time, metadata_end_time, _ = _monthly_period_bounds(
        metadata_period_values
    )
    start_time, end_time = _definition_history_bounds(definition)
    if end_time != metadata_end_time:
        raise RuntimeError(
            "勞動部青年 API 查詢定義與 SDMX metadata 的最新期別不一致："
            f"definition={end_time}、metadata={metadata_end_time}"
        )

    rows = []
    query_diagnostics = []
    expected_period_ids = None
    for gender_id in API_GENDER_IDS:
        for age_id in API_AGE_IDS:
            api_url = _api_url(
                "+".join(API_FIELD_IDS) + f".{gender_id}.{age_id}.M",
                start_time,
                end_time,
            )
            payload = _api_json(api_url)
            query_rows, period_ids = _parse_api_query(
                payload,
                api_url=api_url,
                expected_gender_id=gender_id,
                expected_age_id=age_id,
                data_time=data_time,
            )
            if expected_period_ids is None:
                expected_period_ids = period_ids
            elif period_ids != expected_period_ids:
                raise RuntimeError(
                    "勞動部青年 API 不同性別／年齡查詢的期別不一致："
                    f"{gender_id}/{age_id}"
                )
            rows.extend(query_rows)
            query_diagnostics.append(
                {
                    "gender_id": gender_id,
                    "age_id": age_id,
                    "period_start": period_ids[0],
                    "period_end": period_ids[-1],
                    "periods": len(period_ids),
                    "rows": len(query_rows),
                    "url": api_url,
                }
            )

    expected_rows = len(expected_period_ids or []) * len(API_FIELD_IDS) * len(
        API_GENDER_IDS
    ) * len(API_AGE_IDS)
    if len(rows) != expected_rows:
        raise RuntimeError(
            f"勞動部青年 API 產出列數不完整：實際={len(rows)}、預期={expected_rows}"
        )
    return rows, {
        "source": API_METADATA_URL,
        "definition": API_DEFINITION_URL,
        "dataset_id": API_DATASET_ID,
        "history_period_start": start_time,
        "history_period_end": end_time,
        "metadata_period_start": metadata_start_time,
        "metadata_period_end": metadata_end_time,
        "periods": len(expected_period_ids or []),
        "queries": query_diagnostics,
        "rows": len(rows),
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
        if row["value"] is None or not math.isfinite(float(row["value"])):
            raise ValueError(f"契約第 {row_number} 列 value 無效")
        if row["value"] < 0 or row["value_type"] not in allowed_types:
            raise ValueError(f"契約第 {row_number} 列 value/value_type 無效")
        if row["period_type"] != "month":
            raise ValueError(f"契約第 {row_number} 列 period_type 不符")
        if row["age_lower"] is None or row["age_upper"] is None:
            raise ValueError(f"契約第 {row_number} 列缺少年齡區間")
        if row["gender"] not in {"total", "male", "female"}:
            raise ValueError(f"契約第 {row_number} 列 gender 無效")
        if row["area_code"] != AREA_CODE or row["area_level"] != AREA_LEVEL:
            raise ValueError(f"契約第 {row_number} 列區域不符")
        if row["unit"] not in {"千人", "%"}:
            raise ValueError(f"契約第 {row_number} 列單位不符")
        json.loads(row["breakdown"])
    return True


def fetch_and_transform(*, data_time=None):
    if data_time is None:
        data_time = datetime.now().astimezone().isoformat()
    rows, diagnostics = fetch_api_data(data_time=data_time)
    validate_contract(rows)
    return rows, diagnostics


def _transfer(**kwargs):
    import pandas as pd
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs["dag_infos"]
    data_time = get_tpe_now_time_str(is_with_tz=True)
    rows, diagnostics = fetch_and_transform(data_time=data_time)
    data = pd.DataFrame(rows, columns=list(CONTRACT_COLUMNS))
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    print(f"MOL youth labor API diagnostics =========== {diagnostics}")
    print(f"ready_data shape =========== {data.shape}")

    engine = create_engine(kwargs["ready_data_db_uri"])
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos["load_behavior"],
        default_table=dag_infos["ready_data_default_table"],
    )
    update_lasttime_in_data_to_dataset_info(
        engine,
        dag_infos["dag_id"],
        data["data_time"].max(),
    )


if CommonDag is not None:
    dag = CommonDag(
        proj_folder="proj_new_taipei_city_dashboard",
        dag_folder="youth_labor_statistics_age_tw",
    )
    dag.create_dag(etl_func=_transfer)
