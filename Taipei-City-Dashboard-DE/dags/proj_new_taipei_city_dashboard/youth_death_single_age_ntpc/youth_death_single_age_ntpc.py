from operators.common_pipeline import CommonDag

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{dataset}/{year}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
DATASET = "ODRP031"
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

# ODRP031 在不同年度使用英文或中文欄位名；先依角色做別名正規化。
COLUMN_ALIASES = {
    "period_year": ("statistic_yyy", "統計年度"),
    "according": ("according", "按照別"),
    "site_id": ("site_id", "區域別"),
    "death_total": ("death_total", "總計_計"),
    "death_male": ("death_m", "總計_男"),
    "death_female": ("death_f", "總計_女"),
}

# 這份代碼表與既有青年 DAG 完全一致。
# 來源本身缺漏的年度。登記在這裡的年度仍會寫入（28 區的數字是正確的），
# 但每列的 breakdown 會標 source_incomplete，且執行時會印出警告。
# 未登記的缺漏一律 raise —— 寧可失敗，也不要靜默發布少一個區的全市總數。
KNOWN_INCOMPLETE_YEARS = {
    113: {
        "missing_districts": ["板橋區"],
        "reason": (
            "戶政司 ODRP031 民國 113 期只回 28 個新北行政區，板橋區整個不存在"
            "（PAGE=1 全國 367 列，含『新北』的區域別僅 28 筆）。"
            "同期 OAS 11842 戶籍動態的新北死亡人數為 30,213，"
            "本資料集 28 區加總為 26,160，差 4,053 與板橋區規模相符。"
            "另注意民國 113 期的欄位名是中文（區域別／總計_計），"
            "與其他年度的英文欄名（site_id／death_total）不同。"
        ),
    },
}

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


def _normalise_frame(records):
    import pandas as pd

    df = pd.DataFrame(records).copy()
    normalised = [_normalise_key(c) for c in df.columns]
    if len(normalised) != len(set(normalised)):
        raise ValueError(f"欄位清理 BOM 後出現重複欄位：{normalised}")
    df.columns = normalised

    rename = {}
    for column in df.columns:
        for canonical, aliases in COLUMN_ALIASES.items():
            if column in aliases:
                if canonical in rename.values():
                    raise ValueError(f"同一角色出現多個欄位：{canonical}")
                rename[column] = canonical
                break
    df = df.rename(columns=rename)
    missing = [canonical for canonical in COLUMN_ALIASES if canonical not in df.columns]
    if missing:
        raise ValueError(
            f"ODRP031 缺少必要角色欄位 {missing}；實際欄位：{list(df.columns)}"
        )
    return df


def _age_columns(columns):
    """找出核心單齡欄位；刻意不匹配 0 歲的本年／去年出生細分欄位。"""
    import re

    found = {}
    for column in columns:
        name = _normalise_key(column)
        match = re.fullmatch(r"death_age_(\d{3})_([mf])", name)
        if match:
            age = int(match.group(1))
            gender = {"m": "male", "f": "female"}[match.group(2)]
        else:
            match = re.fullmatch(r"death_age_100up_([mf])", name)
            if match:
                age = 100
                gender = {"m": "male", "f": "female"}[match.group(1)]
            else:
                match = re.fullmatch(r"(\d{1,3})歲_([男女])_人數", name)
                if match:
                    age = int(match.group(1))
                    gender = {"男": "male", "女": "female"}[match.group(2)]
                else:
                    match = re.fullmatch(r"100歲以上_([男女])_人數", name)
                    if not match:
                        continue
                    age = 100
                    gender = {"男": "male", "女": "female"}[match.group(1)]

        if age > 100:
            raise ValueError(f"ODRP031 出現無法接受的年齡欄位：{name}")
        key = (age, gender)
        if key in found:
            raise ValueError(f"ODRP031 年齡／性別欄位重複：{key}")
        found[key] = column

    expected = {(age, gender) for age in range(100) for gender in ("male", "female")}
    expected.update({(100, "male"), (100, "female")})
    actual = set(found)
    if actual != expected:
        raise ValueError(
            f"ODRP031 核心年齡欄位不完整或改版；缺少 {sorted(expected - actual)}，"
            f"多出 {sorted(actual - expected)}"
        )

    specs = []
    for age, gender in sorted(found):
        specs.append({
            "age": age,
            "age_lower": age,
            "age_upper": None if age == 100 else age,
            "age_band_raw": "100歲以上" if age == 100 else f"{age}歲",
            "gender": gender,
            "column": found[(age, gender)],
        })
    return specs


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


def _request_json(dataset, year, page, with_county=True):
    import json
    import time
    from http.client import HTTPException
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    params = {"PAGE": str(page)}
    if with_county:
        params["COUNTY"] = COUNTY
    query = urlencode(params)
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


def fetch_year_records(year, dataset=DATASET):
    """抓一個年度的新北市資料；RIS 查無資料以 responseCode 判定。"""
    records = []
    page = 1
    with_county = True
    while True:
        body = _request_json(dataset, year, page, with_county=with_county)
        code = body.get("responseCode")
        if code == "OD-0102-S":
            # ODRP031 民國113的中文欄位年度對 COUNTY 參數回空碼，
            # 不帶 COUNTY 卻有完整年度資料；先用無縣市參數確認是否真的查無資料。
            if page != 1 or not with_county:
                return []
            body = _request_json(dataset, year, page, with_county=False)
            code = body.get("responseCode")
            with_county = False
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


def discover_year_records(anchor=ANCHOR_YEAR):
    """從錨點向前後探測；連續兩年查無資料才停止該方向。"""
    available = {}
    probes = []

    def scan(start, step):
        misses = 0
        year = start
        while misses < 2 and 1 <= year <= 130:
            records = fetch_year_records(year)
            probes.append({"year": year, "has_data": bool(records), "rows": len(records)})
            if records:
                available[year] = records
                misses = 0
            else:
                misses += 1
            year += step

    scan(anchor, -1)
    scan(anchor + 1, 1)
    return sorted(available.items()), probes


def build_ready_data(year_records, data_time):
    """DAG 與 verify 共用的 ODRP031 寬轉長、欄位契約與對帳邏輯。"""
    import json

    import pandas as pd

    rows = []
    reconciliations = []
    for roc_year, records in year_records:
        df = _normalise_frame(records)
        specs = _age_columns(df.columns)

        period_values = {
            _normalise_key(value) for value in df["period_year"].dropna().tolist()
        }
        if period_values != {str(roc_year)}:
            raise ValueError(
                f"ODRP031 民國 {roc_year} 的統計年度欄不一致：{sorted(period_values)}"
            )

        site_values = df["site_id"].map(_normalise_key)
        ntpc_sites = [site for site in sorted(site_values.unique()) if site.startswith(COUNTY)]
        unexpected = sorted(
            site for site in ntpc_sites if site[len(COUNTY):] not in DISTRICTS
        )
        if unexpected:
            raise ValueError(f"ODRP031 出現未建檔的新北行政區：{unexpected}")

        # 原本只檢查「出現未建檔的區」，沒有檢查「少了區」。
        # 民國 113 的來源就少了整個板橋區（來源只有 28 區），
        # 而板橋是新北最大區，缺它會讓該年度全市死亡數少約 4,053 人（-13%）。
        # 對帳比的是「解析後輸入 == 輸出」，兩邊都建立在同一份殘缺來源上，
        # 所以對帳一路通過、沒有人發現——這是靠 OAS 11842 戶籍動態
        # 跨來源比對才浮出來的（OAS 2024 = 30,213，走勢平順無異常）。
        missing = sorted(set(DISTRICTS) - {site[len(COUNTY):] for site in ntpc_sites})
        if missing:
            known = KNOWN_INCOMPLETE_YEARS.get(roc_year)
            if known is None or sorted(known["missing_districts"]) != missing:
                raise ValueError(
                    f"ODRP031 民國 {roc_year} 少了 {len(missing)} 個新北行政區："
                    f"{missing}。全市加總會少掉這些區，不可當成完整年度發布。"
                    "若確認是來源缺漏，請登記到 KNOWN_INCOMPLETE_YEARS 並寫明理由，"
                    "不要放寬這個檢核。"
                )
            print(
                f"ODRP031 民國 {roc_year} 已知來源缺漏 =========== "
                f"缺 {missing}；{known['reason']}"
            )
        elif roc_year in KNOWN_INCOMPLETE_YEARS:
            # 來源補齊了，登記表就該清掉，否則會一直帶著一個不再成立的例外。
            # 靜默留著會讓下次真的出問題時看不出差別，所以這裡也要 raise。
            raise ValueError(
                f"ODRP031 民國 {roc_year} 的已知缺漏本次已補齊"
                f"（登記為缺 {KNOWN_INCOMPLETE_YEARS[roc_year]['missing_districts']}，"
                "實際 29 區齊全）。請從 KNOWN_INCOMPLETE_YEARS 移除這一筆，"
                "並確認 SOURCE_NOTES 的說明同步更新。"
            )

        incomplete_note = (
            {"source_incomplete": True, "missing_districts": missing}
            if missing else {}
        )

        df = df[site_values.str.startswith(COUNTY)].copy()
        if df.empty:
            raise ValueError(f"ODRP031 民國 {roc_year} 沒有新北市行政區資料")
        df["_district"] = df["site_id"].map(_normalise_key).str[len(COUNTY):]

        year_ad = roc_year + 1911
        input_total = 0.0
        emitted_total = 0.0
        for row_number, (_, record) in enumerate(df.iterrows(), start=1):
            district = record["_district"]
            context = f"ODRP031 ROC {roc_year} {district} 第 {row_number} 列"
            death_total = _number(record["death_total"], context + " 總計")
            death_male = _number(record["death_male"], context + " 男總計")
            death_female = _number(record["death_female"], context + " 女總計")
            if abs(death_total - death_male - death_female) > 0.0001:
                raise ValueError(
                    f"{context} 男女合計不等於總計："
                    f"{death_male}+{death_female}!={death_total}"
                )

            age_sums = {"male": 0.0, "female": 0.0}
            according = _normalise_key(record["according"])
            if not according:
                raise ValueError(f"{context} 按照別是空值")
            for spec in specs:
                value = _number(record[spec["column"]], context + f" {spec['column']}")
                age_sums[spec["gender"]] += value
                emitted_total += value
                rows.append({
                    "indicator_id": "death_count_by_age",
                    "period_start": f"{year_ad}-01-01",
                    "period_end": f"{year_ad}-12-31",
                    "period_type": "year",
                    "age_lower": spec["age_lower"],
                    "age_upper": spec["age_upper"],
                    "age_band_raw": spec["age_band_raw"],
                    "gender": spec["gender"],
                    "area_code": DISTRICTS[district],
                    "area_level": "district",
                    "breakdown": json.dumps({
                        "district_name": district,
                        "count_basis": according,
                        "source_column": spec["column"],
                        # 該年度來源缺區時標記，讓下游能把不完整年度排除在
                        # 全市趨勢之外，而不是畫出一個假的下降。
                        **incomplete_note,
                    }, ensure_ascii=False),
                    "value": value,
                    "unit": "人",
                    "value_type": "count",
                })

            if abs(age_sums["male"] - death_male) > 0.0001:
                raise ValueError(
                    f"{context} 男性分齡加總不一致：{age_sums['male']}!={death_male}"
                )
            if abs(age_sums["female"] - death_female) > 0.0001:
                raise ValueError(
                    f"{context} 女性分齡加總不一致：{age_sums['female']}!={death_female}"
                )
            input_total += death_total

        if abs(emitted_total - input_total) > 0.0001:
            raise ValueError(
                f"ODRP031 民國 {roc_year} 對帳失敗：輸入總計 {input_total}，"
                f"輸出分齡總計 {emitted_total}"
            )
        reconciliations.append({
            "roc_year": roc_year,
            "district_rows": len(df),
            "missing_districts": missing,
            "input_total": input_total,
            "output_total": emitted_total,
            "passed": True,
        })

    if not rows:
        raise RuntimeError("ODRP031 沒有產出任何資料列")

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

    year_records, probes = discover_year_records()
    print(f"ODRP031 year probes =========== {probes}")
    print(f"ODRP031 available years =========== {[year for year, _ in year_records]}")
    data, reconciliations = build_ready_data(
        year_records, data_time=get_tpe_now_time_str(is_with_tz=True)
    )
    for check in reconciliations:
        print(f"ODRP031 reconciliation =========== {check}")
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
    dag_folder="youth_death_single_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
