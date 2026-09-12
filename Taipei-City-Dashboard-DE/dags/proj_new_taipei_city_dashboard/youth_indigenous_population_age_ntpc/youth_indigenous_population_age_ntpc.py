from operators.common_pipeline import CommonDag

DATASET = "ODRP015"
API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{dataset}/{period}"
COUNTY = "新北市"
MAX_REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
ANCHOR_ROC_YEAR = 114
SCALE_ANOMALY_FACTOR = 3.0
KNOWN_BAD_YEARS = {}
INDICATOR_ID = "indigenous_population_count"
CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

DISTRICTS = {
    "板橋區": "65000010", "三重區": "65000020", "中和區": "65000030", "永和區": "65000040",
    "新莊區": "65000050", "新店區": "65000060", "樹林區": "65000070", "鶯歌區": "65000080",
    "三峽區": "65000090", "淡水區": "65000100", "汐止區": "65000110", "瑞芳區": "65000120",
    "土城區": "65000130", "蘆洲區": "65000140", "五股區": "65000150", "泰山區": "65000160",
    "林口區": "65000170", "深坑區": "65000180", "石碇區": "65000190", "坪林區": "65000200",
    "三芝區": "65000210", "石門區": "65000220", "八里區": "65000230", "平溪區": "65000240",
    "雙溪區": "65000250", "貢寮區": "65000260", "金山區": "65000270", "萬里區": "65000280",
    "烏來區": "65000290",
}
DISTRICT_NAMES_BY_CODE = {code: name for name, code in DISTRICTS.items()}
DISTRICT_CODES = set(DISTRICT_NAMES_BY_CODE)
INDIGENOUS_TYPES = {
    "山地原住民",
    "平地原住民",
    "平埔原住民",
}


def _number(value, field):
    import math

    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"ODRP015 欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"ODRP015 欄位 {field} 無法轉數值：{value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"ODRP015 欄位 {field} 出現無效數值：{value!r}")
    return number


def _parse_age(label):
    """把單齡／開放區間解析成整數上下界；未知標籤直接失敗。"""
    import re

    raw = "" if label is None else str(label).strip()
    match = re.fullmatch(r"(\d{1,3})歲", raw)
    if match:
        age = int(match.group(1))
        if age > 100:
            raise ValueError(f"ODRP015 年齡超出已知範圍：{label!r}")
        return age, age

    match = re.fullmatch(r"(\d{1,3})歲以上", raw)
    if match:
        age = int(match.group(1))
        if age > 100:
            raise ValueError(f"ODRP015 年齡超出已知範圍：{label!r}")
        return age, None

    match = re.fullmatch(r"未滿(\d{1,3})歲", raw)
    if match:
        upper = int(match.group(1)) - 1
        if upper < 0:
            raise ValueError(f"ODRP015 年齡區間無效：{label!r}")
        return None, upper

    raise ValueError(f"無法解析 ODRP015 年齡標籤：{label!r}")


def _gender(value):
    try:
        return {"男": "male", "女": "female"}[str(value).strip()]
    except KeyError as exc:
        raise ValueError(f"ODRP015 出現未知性別：{value!r}") from exc


def _area_code(record):
    """用來源區域代碼篩新北；非新北列在頁面處理當下直接丟棄。"""
    raw = "" if record.get("district_code") is None else str(record["district_code"]).strip()
    code = raw[:8]
    if code in DISTRICT_CODES:
        return code
    if raw.startswith("65000"):
        raise ValueError(f"ODRP015 出現未列入契約的新北行政區代碼：{raw!r}")
    return None


def _normalise_record(record):
    """ODRP015 舊年度有 BOM 與 aborigine_* 欄名，依角色統一。"""
    aliases = {
        "\ufeffstatistic_yyymm": "statistic_yyymm",
        "aborigine_registered": "indigenous_registered",
        "aborigine_count": "indigenous_count",
    }
    return {aliases.get(key, key): value for key, value in record.items()}


def _fetch_page(session, period, page):
    import time

    import requests

    request = session.get if session is not None else requests.get
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        try:
            response = request(
                API.format(dataset=DATASET, period=period),
                headers={"User-Agent": UA},
                params={"PAGE": page, "COUNTY": COUNTY},
                timeout=120,
            )
            response.raise_for_status()
            body = response.json()
            break
        except (requests.RequestException, ValueError):
            if attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    code = body.get("responseCode")
    if code == "OD-0102-S":
        return None
    if code != "OD-0101-S":
        raise RuntimeError(
            f"ODRP015 {period} PAGE={page} 回應碼異常："
            f"{code} {body.get('responseMessage')}"
        )
    records = body.get("responseData")
    if not isinstance(records, list):
        raise TypeError(f"ODRP015 {period} PAGE={page} responseData 不是陣列")
    try:
        total_pages = int(body.get("totalPage", 1))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"ODRP015 {period} totalPage 無法解析") from exc
    return body, total_pages


def _probe_period(session, period):
    return _fetch_page(session, period, 1) is not None


def _latest_month_for_year(session, roc_year):
    """ODRP015 是年月 API；每個民國年取最後一個有資料的月份。"""
    for month in range(12, 0, -1):
        period = f"{roc_year:03d}{month:02d}"
        if _probe_period(session, period):
            return period
    return None


def discover_periods():
    """從錨點向前後探測，連續兩個無資料年度才停止。"""
    import requests

    found = {}
    with requests.Session() as session:
        anchor = _latest_month_for_year(session, ANCHOR_ROC_YEAR)
        if anchor is None:
            raise RuntimeError(f"ODRP015 錨點民國 {ANCHOR_ROC_YEAR} 年沒有資料")
        found[ANCHOR_ROC_YEAR] = anchor

        for direction in (1, -1):
            misses = 0
            roc_year = ANCHOR_ROC_YEAR + direction
            while misses < 2:
                period = _latest_month_for_year(session, roc_year)
                if period is None:
                    misses += 1
                else:
                    found[roc_year] = period
                    misses = 0
                roc_year += direction

    periods = [found[year] for year in sorted(found)]
    print(f"ODRP015 discovered periods: {periods}")
    return periods


def fetch_period_aggregates(period):
    """逐頁抓取；每頁只留下新北資料並立即聚合，不保留全國資料。"""
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import requests

    aggregates = defaultdict(float)
    national_rows = 0
    ntpc_rows = 0
    source_total = 0.0
    total_pages = None

    def consume(fetched, page_number):
        nonlocal national_rows, ntpc_rows, source_total
        if fetched is None:
            raise RuntimeError(f"ODRP015 {period} 中途回傳查無資料：PAGE={page_number}")
        body, _ = fetched
        records = body["responseData"]
        national_rows += len(records)

        for source_record in records:
            record = _normalise_record(source_record)
            area_code = _area_code(record)
            if area_code is None:
                continue
            required = [
                "district_code", "site_id", "village", "sex",
                "indigenous_registered", "age", "indigenous_count",
            ]
            missing = [field for field in required if field not in record]
            if missing:
                raise ValueError(
                    f"ODRP015 {period} 新北資料缺少欄位 {missing}：{list(record)}"
                )
            indigenous_type = str(record["indigenous_registered"]).strip()
            if indigenous_type not in INDIGENOUS_TYPES:
                raise ValueError(
                    f"ODRP015 {period} 出現未知原住民身分：{indigenous_type!r}"
                )
            age_lower, age_upper = _parse_age(record["age"])
            gender = _gender(record["sex"])
            value = _number(record["indigenous_count"], "indigenous_count")
            key = (
                area_code,
                age_lower,
                age_upper,
                str(record["age"]).strip(),
                gender,
                indigenous_type,
            )
            aggregates[key] += value
            source_total += value
            ntpc_rows += 1

    with requests.Session() as session:
        first = _fetch_page(session, period, 1)
        if first is None:
            return None
        _, total_pages = first
        consume(first, 1)
        print(
            f"ODRP015 {period} page 1/{total_pages}: "
            f"national={national_rows:,}, ntpc={ntpc_rows:,}"
        )

        # API 每頁約 0.25 秒；限制在小批次併發，仍逐頁篩選、逐頁聚合，
        # 不把全國 responseData 累積到記憶體。
        workers = 8
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for start in range(2, total_pages + 1, workers):
                page_numbers = range(start, min(start + workers, total_pages + 1))
                futures = {
                    executor.submit(_fetch_page, None, period, page_number): page_number
                    for page_number in page_numbers
                }
                for future in as_completed(futures):
                    consume(future.result(), futures[future])
                end = max(page_numbers)
                if end == total_pages or end % 100 < workers:
                    print(
                        f"ODRP015 {period} page {start}-{end}/{total_pages}: "
                        f"national={national_rows:,}, ntpc={ntpc_rows:,}"
                    )

    if not aggregates:
        raise RuntimeError(f"ODRP015 {period} 沒有新北市資料")
    return {
        "period": period,
        "roc_year": int(period[:3]),
        "aggregates": dict(aggregates),
        "source_total": source_total,
        "national_rows": national_rows,
        "ntpc_rows": ntpc_rows,
    }


def drop_scope_anomalies(period_results):
    """已知壞年度明確排除；未知年度尺度超過 3 倍時直接失敗。"""
    totals = {result["roc_year"]: result["source_total"] for result in period_results}
    if not totals:
        raise RuntimeError("ODRP015 沒有可供尺度檢核的年度")

    active = {year: total for year, total in totals.items() if year not in KNOWN_BAD_YEARS}
    for year, value in sorted(active.items()):
        others = [other for other_year, other in active.items() if other_year != year]
        if not others:
            continue
        others.sort()
        median = others[len(others) // 2]
        if median and (value > median * SCALE_ANOMALY_FACTOR
                       or value * SCALE_ANOMALY_FACTOR < median):
            raise ValueError(
                f"ODRP015 民國 {year} 年新北原住民人口 {value:,.0f}，"
                f"與其餘年度中位數 {median:,.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                "可能是來源範圍或統計口徑改變，請先查證，不可靜默入庫。"
            )

    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if year in totals:
            print(f"ODRP015 scale guard: 排除民國 {year}（{reason}）")
    return [result for result in period_results if result["roc_year"] not in KNOWN_BAD_YEARS]


def _period_dates(roc_year):
    year = roc_year + 1911
    return f"{year}-01-01", f"{year}-12-31"


def _breakdown(area_name, category, source_period, area_level):
    import json

    return json.dumps(
        {
            "district_name": area_name,
            "indigenous_registered": category,
            "source_period": source_period,
            "source_grain": "village",
            "aggregated_area_level": area_level,
        },
        ensure_ascii=False,
    )


def build_ready_data(period_results, data_time=None):
    """將已逐頁聚合的結果轉成事實表；DAG 與 verify 共用此函式。"""
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    period_results = drop_scope_anomalies(period_results)
    rows = []
    reconciliations = []

    for result in period_results:
        period = result["period"]
        period_start, period_end = _period_dates(result["roc_year"])
        aggregates = result["aggregates"]
        district_totals = defaultdict(float)
        city_totals = defaultdict(float)

        for key, value in sorted(
            aggregates.items(),
            key=lambda item: (
                item[0][0],
                -1 if item[0][1] is None else item[0][1],
                item[0][3],
                item[0][4],
                item[0][5],
            ),
        ):
            area_code, age_lower, age_upper, age_raw, gender, category = key
            district_name = f"新北市{DISTRICT_NAMES_BY_CODE[area_code]}"
            district_totals[(area_code, age_lower, age_upper, age_raw, category)] += value
            city_totals[(age_lower, age_upper, age_raw, gender, category)] += value
            period_base = {
                "indicator_id": INDICATOR_ID,
                "period_start": period_start,
                "period_end": period_end,
                "period_type": "year",
                "age_lower": age_lower,
                "age_upper": age_upper,
                "age_band_raw": age_raw,
                "gender": gender,
                "area_code": area_code,
                "area_level": "district",
                "breakdown": _breakdown(district_name, category, period, "district"),
                "value": value,
                "unit": "人",
                "value_type": "count",
                "data_time": data_time,
            }
            rows.append(period_base)

        for key, value in district_totals.items():
            area_code, age_lower, age_upper, age_raw, category = key
            district_name = f"新北市{DISTRICT_NAMES_BY_CODE[area_code]}"
            rows.append(
                {
                    "indicator_id": INDICATOR_ID,
                    "period_start": period_start,
                    "period_end": period_end,
                    "period_type": "year",
                    "age_lower": age_lower,
                    "age_upper": age_upper,
                    "age_band_raw": age_raw,
                    "gender": "total",
                    "area_code": area_code,
                    "area_level": "district",
                    "breakdown": _breakdown(district_name, category, period, "district"),
                    "value": value,
                    "unit": "人",
                    "value_type": "count",
                    "data_time": data_time,
                }
            )

        for (area_code, age_lower, age_upper, age_raw, category), value in district_totals.items():
            city_totals[(age_lower, age_upper, age_raw, "total", category)] += value

        for key, value in sorted(
            city_totals.items(), key=lambda item: (item[0][0] if item[0][0] is not None else -1,
                                                   item[0][2], item[0][3], item[0][4])
        ):
            age_lower, age_upper, age_raw, gender, category = key
            rows.append(
                {
                    "indicator_id": INDICATOR_ID,
                    "period_start": period_start,
                    "period_end": period_end,
                    "period_type": "year",
                    "age_lower": age_lower,
                    "age_upper": age_upper,
                    "age_band_raw": age_raw,
                    "gender": gender,
                    "area_code": "65000",
                    "area_level": "city",
                    "breakdown": _breakdown("新北市", category, period, "city"),
                    "value": value,
                    "unit": "人",
                    "value_type": "count",
                    "data_time": data_time,
                }
            )

        district = pd.DataFrame(rows)
        district = district[
            (district["period_start"] == period_start)
            & (district["area_level"] == "district")
        ]
        emitted_sexes = float(district[district["gender"].isin(["male", "female"])]
                               ["value"].sum())
        emitted_total = float(district[district["gender"] == "total"]["value"].sum())
        expected = float(result["source_total"])
        if abs(emitted_sexes - expected) > 0.5 or abs(emitted_total - expected) > 0.5:
            raise ValueError(
                f"ODRP015 民國 {result['roc_year']} 對帳失敗："
                f"輸入 {expected}, 輸出男女 {emitted_sexes}, total {emitted_total}"
            )
        city = pd.DataFrame(rows)
        city = city[
            (city["period_start"] == period_start) & (city["area_level"] == "city")
        ]
        emitted_city = float(city[city["gender"] == "total"]["value"].sum())
        if abs(emitted_city - expected) > 0.5:
            raise ValueError(
                f"ODRP015 民國 {result['roc_year']} 全市對帳失敗："
                f"輸入 {expected}, 輸出 {emitted_city}"
            )
        reconciliations.append(
            {
                "roc_year": result["roc_year"],
                "period": period,
                "source_rows": result["ntpc_rows"],
                "input_total": expected,
                "district_total": emitted_total,
                "city_total": emitted_city,
            }
        )

    if not rows:
        raise RuntimeError("ODRP015 轉換後沒有資料列")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")

    # 來源是村里 x 單齡 x 性別 x 類別的完整交叉表，絕大多數格子是 0
    # (原住民人口雖較稠密，交叉表仍以零值為主)。
    # 上面的逐期對帳比的是「數值總和」，且已在濾零前完成，所以濾零不影響其
    # 正確性：0 對總和沒有貢獻，下游一律 GROUP BY ... SUM(value) 也不受影響。
    # 需要顯式零的視覺化（例如行政區熱區圖要畫空白格）應在查詢端 LEFT JOIN
    # 維度表補零，而不是在事實表存數萬列的 0。
    before = data.groupby("period_start").size().to_dict()
    data = data[data["value"] != 0].reset_index(drop=True)
    after = data.groupby("period_start").size().to_dict()
    for check in reconciliations:
        start = _period_dates(check["roc_year"])[0]
        check["emitted_rows"] = before.get(start, 0)
        check["emitted_rows_nonzero"] = after.get(start, 0)
    if data.empty:
        raise RuntimeError("ODRP015 濾除零值後沒有資料列")
    if data_time is None:
        data["data_time"] = datetime.now(
            timezone(timedelta(hours=8))
        ).isoformat()
    return data, reconciliations


def _transfer(**kwargs):
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

    periods = discover_periods()
    period_results = [fetch_period_aggregates(period) for period in periods]
    period_results = [result for result in period_results if result is not None]
    data, _ = build_ready_data(
        period_results,
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")

    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=load_behavior,
        default_table=default_table,
    )
    update_lasttime_in_data_to_dataset_info(engine, dag_id, data["data_time"].max())


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_indigenous_population_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
