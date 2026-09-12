from operators.common_pipeline import CommonDag

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{dataset}/{year}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
COUNTY = "新北市"
ANCHOR_YEAR = 114

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

SOURCE_CONFIG = {
    "marriage": {
        "dataset": "ODRP069",
        "indicator_id": "marriage_count",
        "value_aliases": (
            "number_of_marry",
            "marry_count",
            "結婚人數",
        ),
    },
    "divorce": {
        "dataset": "ODRP071",
        "indicator_id": "divorce_count",
        "value_aliases": (
            "divorce_count",
            "離婚人數",
            "離婚／終止結婚人數",
        ),
    },
}

# ODRP069／071 以不同年度的欄位名發布相同角色，先正規化角色再轉換。
COMMON_COLUMN_ALIASES = {
    "period_year": ("year", "statistic_yyy", "統計年度"),
    "according": ("according", "按照別"),
    "site_id": ("site_id", "區域別"),
    "marriage_type": ("marriage_type", "婚姻類型"),
    "sex": ("sex", "性別"),
    "nation": ("nation", "原屬國籍", "原屬國籍（地區）"),
    "education": ("edu", "education", "教育程度", "教育程度別"),
    "age": ("age", "年齡"),
}

# 這份代碼表與既有青年 DAG 完全一致。
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
CITY_CODE = "65000"


def _normalise_key(value):
    text = str(value).replace("\ufeff", "").strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _normalise_frame(records, value_aliases):
    import pandas as pd

    aliases = dict(COMMON_COLUMN_ALIASES)
    aliases["value"] = value_aliases
    df = pd.DataFrame(records).copy()
    normalised = [_normalise_key(c) for c in df.columns]
    if len(normalised) != len(set(normalised)):
        raise ValueError(f"欄位清理 BOM 後出現重複欄位：{normalised}")
    df.columns = normalised

    rename = {}
    for column in df.columns:
        for canonical, column_aliases in aliases.items():
            if column in column_aliases:
                if canonical in rename.values():
                    raise ValueError(f"同一角色出現多個欄位：{canonical}")
                rename[column] = canonical
                break
    df = df.rename(columns=rename)
    missing = [canonical for canonical in aliases if canonical not in df.columns]
    if missing:
        raise ValueError(
            f"資料集缺少必要角色欄位 {missing}；實際欄位：{list(df.columns)}"
        )
    return df


def _parse_age(label):
    """解析來源年齡標籤，回傳 (age_lower, age_upper)。"""
    import re

    raw = _normalise_key(label)
    compact = re.sub(r"\s+", "", raw)
    match = re.fullmatch(r"未滿(\d{1,3})歲", compact)
    if match:
        upper = int(match.group(1)) - 1
        if upper < 0:
            raise ValueError(f"年齡標籤上下界不合法：{raw!r}")
        return None, upper

    match = re.fullmatch(r"(\d{1,3})[~～\-－–—至](\d{1,3})歲", compact)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"年齡標籤上下界不合法：{raw!r}")
        return lower, upper

    match = re.fullmatch(r"(\d{1,3})歲以上", compact)
    if match:
        return int(match.group(1)), None

    raise ValueError(
        f"出現無法解析的年齡標籤 {raw!r}；不准靜默略過，請先確認來源語意"
    )


def _number(value, context):
    import math

    if value is None:
        raise ValueError(f"{context} 是空值，不能靜默當成 0")
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "null", "<na>"}:
        raise ValueError(f"{context} 是空值，不能靜默當成 0")
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"{context} 不是數值：{value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{context} 不是有限數值：{value!r}")
    return number


def _dimension(value):
    text = _normalise_key(value)
    return text or "未填"


def _request_json(dataset, year, page):
    import json
    import time
    from http.client import HTTPException
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    query = urlencode({"PAGE": str(page), "COUNTY": COUNTY})
    url = API.format(dataset=dataset, year=year) + "?" + query
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
    raise RuntimeError(f"RIS 回應讀取失敗，重試三次仍失敗：{url}") from last_error


def fetch_year_records(dataset, year):
    """抓一個資料集／年度的新北市資料；以 RIS responseCode 判定查無資料。"""
    records = []
    page = 1
    while True:
        body = _request_json(dataset, year, page)
        code = body.get("responseCode")
        if code == "OD-0102-S":
            return []
        if code != "OD-0101-S":
            raise RuntimeError(
                f"{dataset} 民國 {year} 第 {page} 頁回應碼異常："
                f"{code} {body.get('responseMessage')}"
            )
        page_data = body.get("responseData") or []
        if not page_data:
            raise RuntimeError(f"{dataset} 民國 {year} 第 {page} 頁成功但沒有 responseData")
        records.extend(page_data)
        try:
            total_page = int(body.get("totalPage", 1))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{dataset} 民國 {year} totalPage 不是整數") from exc
        if page >= total_page:
            return records
        page += 1


def discover_source_records(dataset, anchor=ANCHOR_YEAR):
    """從錨點向前後探測；連續兩年查無資料才停止該方向。"""
    available = {}
    probes = []

    def scan(start, step):
        misses = 0
        year = start
        while misses < 2 and 1 <= year <= 130:
            records = fetch_year_records(dataset, year)
            probes.append({
                "dataset": dataset,
                "year": year,
                "has_data": bool(records),
                "rows": len(records),
            })
            if records:
                available[year] = records
                misses = 0
            else:
                misses += 1
            year += step

    scan(anchor, -1)
    scan(anchor + 1, 1)
    return sorted(available.items()), probes


def discover_all_source_records():
    source_records = []
    probes = []
    for source_key, config in SOURCE_CONFIG.items():
        year_records, source_probes = discover_source_records(config["dataset"])
        probes.extend(source_probes)
        source_records.extend(
            (source_key, roc_year, records) for roc_year, records in year_records
        )
    return source_records, probes


def build_ready_data(source_records, data_time):
    """DAG 與 verify 共用的婚姻／離婚轉換、欄位契約與對帳邏輯。"""
    import json

    import pandas as pd

    rows = []
    reconciliations = []
    for source_key, roc_year, records in source_records:
        config = SOURCE_CONFIG[source_key]
        df = _normalise_frame(records, config["value_aliases"])
        period_values = {
            _normalise_key(value) for value in df["period_year"].dropna().tolist()
        }
        if period_values != {str(roc_year)}:
            raise ValueError(
                f"{config['dataset']} 民國 {roc_year} 的統計年度欄不一致："
                f"{sorted(period_values)}"
            )

        site_values = df["site_id"].map(_normalise_key)
        ntpc_sites = [site for site in sorted(site_values.unique()) if site.startswith(COUNTY)]
        unexpected = sorted(
            site for site in ntpc_sites if site[len(COUNTY):] not in DISTRICTS
        )
        if unexpected:
            raise ValueError(f"{config['dataset']} 出現未建檔的新北行政區：{unexpected}")
        df = df[site_values.str.startswith(COUNTY)].copy()
        if df.empty:
            raise ValueError(
                f"{config['dataset']} 民國 {roc_year} 沒有新北市行政區資料"
            )
        df["_district"] = df["site_id"].map(_normalise_key).str[len(COUNTY):]

        # 先補齊所有會參與 groupby／breakdown 的維度，避免 pandas 預設丟掉 NaN key。
        for column in ("according", "marriage_type", "nation", "education"):
            df[column] = df[column].map(_dimension)
        sex_values = df["sex"].map(_normalise_key)
        bad_sex = sorted(set(sex_values) - {"男", "女"})
        if bad_sex:
            raise ValueError(
                f"{config['dataset']} 民國 {roc_year} 出現無法對應到 gender 的性別：{bad_sex}"
            )
        df["_gender"] = sex_values.map({"男": "male", "女": "female"})

        parsed_ages = df["age"].map(_parse_age)
        df["_age_lower"] = [age[0] for age in parsed_ages]
        df["_age_upper"] = [age[1] for age in parsed_ages]
        df["_age_raw"] = df["age"].map(_normalise_key)
        df["_value"] = [
            _number(value, f"{config['dataset']} ROC {roc_year} 第 {idx} 列計數")
            for idx, value in enumerate(df["value"], start=1)
        ]

        group_columns = [
            "_district",
            "_age_raw",
            "_gender",
            "according",
            "marriage_type",
            "nation",
            "education",
        ]
        grouped = (
            df.groupby(group_columns, as_index=False, dropna=False)
            .agg(
                value=("_value", "sum"),
                age_lower=("_age_lower", "first"),
                age_upper=("_age_upper", "first"),
            )
        )
        input_total = float(df["_value"].sum())
        emitted_total = float(grouped["value"].sum())
        if abs(emitted_total - input_total) > 0.0001:
            raise ValueError(
                f"{config['dataset']} 民國 {roc_year} 對帳失敗：輸入 {input_total}，"
                f"輸出 {emitted_total}"
            )

        # 來源是六維交叉表（行政區 × 婚姻類型 × 性別 × 國籍 × 教育程度 × 年齡），
        # 維度相乘後絕大多數組合在單一年度沒有案例。
        # 實測民國 114 年新北市 48,720 列中有 44,076 列是 0（90.5%）。
        # 事實表存這些零沒有意義：下游一律 GROUP BY … SUM(value)，
        # 缺列與零值列的結果完全相同；需要顯式零的圖表（例如熱區圖要畫空白格）
        # 應在查詢端 LEFT JOIN 維度表補零，而不是在事實表存 44 萬列的零。
        #
        # 上面的對帳比的是「數值總和」而非列數，所以濾零不影響其正確性——
        # 零對總和沒有貢獻。列數僅記錄於 reconciliations 供追蹤。
        nonzero = grouped[grouped["value"] != 0]

        year_ad = roc_year + 1911
        for _, record in nonzero.iterrows():
            rows.append({
                "indicator_id": config["indicator_id"],
                "period_start": f"{year_ad}-01-01",
                "period_end": f"{year_ad}-12-31",
                "period_type": "year",
                "age_lower": record["age_lower"],
                "age_upper": record["age_upper"],
                "age_band_raw": record["_age_raw"],
                "gender": record["_gender"],
                "area_code": DISTRICTS[record["_district"]],
                "area_level": "district",
                "breakdown": json.dumps({
                    "district_name": record["_district"],
                    "marriage_type": record["marriage_type"],
                    "nation": record["nation"],
                    "education": record["education"],
                    "count_basis": record["according"],
                    "source_dataset": config["dataset"],
                }, ensure_ascii=False),
                "value": float(record["value"]),
                "unit": "人",
                "value_type": "count",
            })

        reconciliations.append({
            "source": source_key,
            "dataset": config["dataset"],
            "roc_year": roc_year,
            "input_rows": len(df),
            "grouped_rows": len(grouped),
            "output_rows": len(nonzero),
            "dropped_zero_rows": len(grouped) - len(nonzero),
            "input_total": input_total,
            "output_total": emitted_total,
            "passed": True,
        })

    if not rows:
        raise RuntimeError("ODRP069／ODRP071 沒有產出任何資料列")

    data = pd.DataFrame(rows, columns=[c for c in CONTRACT_COLUMNS if c != "data_time"])
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    data["data_time"] = data_time
    return data[CONTRACT_COLUMNS], reconciliations


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

    source_records, probes = discover_all_source_records()
    print(f"ODRP069／ODRP071 year probes =========== {probes}")
    available_years = {
        source_key: sorted(
            {year for source, year, _ in source_records if source == source_key}
        )
        for source_key in SOURCE_CONFIG
    }
    print(f"available years =========== {available_years}")
    data, reconciliations = build_ready_data(
        source_records, data_time=get_tpe_now_time_str(is_with_tz=True)
    )
    for check in reconciliations:
        print(f"marriage reconciliation =========== {check}")
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
    dag_folder="youth_marriage_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
