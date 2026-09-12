from airflow import DAG
from operators.common_pipeline import CommonDag

API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/{ds}/{year}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# ODRP056 = 按發生日期分。ODRP055 是按登記日期分，欄位相同但目前只到 113 年度。
DATASET = "ODRP056"
FIRST_YEAR = 108  # 實測 108~114 皆有資料

# 同一個資料集，民國 113 年度用中文欄位名，108-112 與 114 年度用英文欄位名。
# 值的格式兩者完全相同，只有 key 不一樣。統一正規化成中文名。
COLUMN_ALIASES = {
    "statistic_yyy": "統計年度",
    "according": "按照別",
    "site_id": "區域別",
    "mother_age": "生母年齡",
    "birth_sex": "出生者性別",
    "birth_count": "嬰兒出生數",
}

# 行政區代碼與 youth_pop_single_age、youth_traffic_fatal_ntpc 一致，三張表可直接 join。
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


def _parse_age(label):
    """回傳 (age_lower, age_upper)。單齡兩者相同；開放區間有一端為 None。

    來源除了 '15歲' 這種單齡，還有 '未滿15歲' 與 'N歲以上' 的開放區間。
    這些落在 18-35 之外，但仍要輸出，否則各年度出生總數會對不起來。
    """
    import re

    s = str(label).strip()
    m = re.fullmatch(r"(\d{1,3})歲", s)
    if m:
        n = int(m.group(1))
        return n, n
    m = re.fullmatch(r"未滿(\d{1,3})歲", s)
    if m:
        return None, int(m.group(1)) - 1
    m = re.fullmatch(r"(\d{1,3})歲以上", s)
    if m:
        return int(m.group(1)), None
    return None, None


def _transfer(**kwargs):
    import json

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

    headers = {"User-Agent": UA}

    def fetch_year(year):
        """抓完整年度。

        RIS 查無資料時回 HTTP 200 + responseCode OD-0102-S，**不是 404**，
        所以絕不能用 HTTP 狀態判斷有無資料，否則會靜默吃進空結果。
        """
        out, page = [], 1
        while True:
            resp = requests.get(
                API.format(ds=DATASET, year=year),
                headers=headers, params={"PAGE": page}, timeout=120,
            )
            resp.raise_for_status()
            body = resp.json()
            code = body.get("responseCode")
            if code == "OD-0102-S":  # 查無資料
                return []
            if code != "OD-0101-S":
                raise RuntimeError(
                    f"{year} 年度回應碼異常：{code} {body.get('responseMessage')}"
                )
            out.extend(body.get("responseData") or [])
            total = int(body.get("totalPage", 1))
            if page >= total:
                return out
            page += 1

    def num(v):
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    # ---------- Extract ----------
    frames, year, misses = [], FIRST_YEAR, 0
    while misses < 2:
        records = fetch_year(year)
        if not records:
            misses += 1
        else:
            frames.append((year, records))
            print(f"ROC {year} =========== {len(records)} rows nationwide")
            misses = 0
        year += 1

    if not frames:
        raise RuntimeError("沒有抓到任何年度的資料，來源或資料集編號可能已變更。")

    # ---------- Transform ----------
    rows, unparsed_labels = [], set()
    for roc_year, records in frames:
        df = pd.DataFrame(records).rename(columns=COLUMN_ALIASES)
        need = ["區域別", "生母年齡", "出生者性別", "嬰兒出生數", "按照別"]
        missing = [c for c in need if c not in df.columns]
        if missing:
            raise ValueError(
                f"{roc_year} 年度缺少欄位 {missing}，實際欄位：{list(df.columns)}"
            )

        df = df[df["區域別"].astype(str).str.startswith("新北市")].copy()
        if df.empty:
            continue
        df["_dist"] = df["區域別"].astype(str).str.replace("新北市", "", n=1)
        df = df[df["_dist"].isin(DISTRICTS)]

        parsed = df["生母年齡"].map(_parse_age)
        df["_lo"] = [p[0] for p in parsed]
        df["_hi"] = [p[1] for p in parsed]
        bad = df["_lo"].isna() & df["_hi"].isna()
        unparsed_labels |= set(df.loc[bad, "生母年齡"].unique())
        df = df[~bad]
        df["_n"] = df["嬰兒出生數"].map(num)
        df = df.dropna(subset=["_n"])

        year_ad = roc_year + 1911
        base = {
            "period_start": f"{year_ad}-01-01",
            "period_end": f"{year_ad}-12-31",
            "period_type": "year",
            "area_level": "district",
            "unit": "人",
            "value_type": "count",
            # 年齡的主體是生母，而生母皆為女性，所以 gender 固定 female。
            # 出生者性別是另一個維度（嬰兒的性別），放進 breakdown，
            # 不要和生母年齡混在同一個 gender 欄位裡。
            "gender": "female",
        }

        for _, r in df.iterrows():
            rows.append(dict(
                base,
                indicator_id="birth_count_by_mother_age",
                age_lower=r["_lo"], age_upper=r["_hi"],
                age_band_raw=str(r["生母年齡"]),
                area_code=DISTRICTS[r["_dist"]],
                breakdown=json.dumps({
                    "baby_gender": str(r["出生者性別"]).strip(),
                    "district_name": r["_dist"],
                    "count_basis": str(r["按照別"]).strip(),
                }, ensure_ascii=False),
                value=float(r["_n"]),
            ))

        agg = (
            df.groupby(["_dist", "生母年齡", "按照別"], dropna=False)
              .agg(_n=("_n", "sum"), _lo=("_lo", "first"), _hi=("_hi", "first"))
              .reset_index()
        )
        for _, r in agg.iterrows():
            rows.append(dict(
                base,
                indicator_id="birth_count_by_mother_age",
                age_lower=r["_lo"], age_upper=r["_hi"],
                age_band_raw=str(r["生母年齡"]),
                area_code=DISTRICTS[r["_dist"]],
                breakdown=json.dumps({
                    "baby_gender": "total",
                    "district_name": r["_dist"],
                    "count_basis": str(r["按照別"]).strip(),
                }, ensure_ascii=False),
                value=float(r["_n"]),
            ))

        emitted = float(agg["_n"].sum())
        actual = float(df["_n"].sum())
        if abs(emitted - actual) > 0.5:
            raise ValueError(
                f"{roc_year} 年度出生數不一致：輸入 {actual} 但輸出 {emitted}"
            )
        print(
            f"ROC {roc_year} =========== NTPC {len(df)} rows, "
            f"births {int(actual)}, reconciled"
        )

    if unparsed_labels:
        raise ValueError(
            f"出現無法解析的生母年齡標籤 {sorted(unparsed_labels)}，"
            "請先確認語意再擴充 _parse_age，不要讓它靜默消失。"
        )

    data = pd.DataFrame(rows)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    data["data_time"] = get_tpe_now_time_str(is_with_tz=True)

    print(f"ready_data =========== {data.head(10)}")
    print(f"ready_data shape =========== {data.shape}")

    # ---------- Load ----------
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
    dag_folder="youth_birth_mother_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
