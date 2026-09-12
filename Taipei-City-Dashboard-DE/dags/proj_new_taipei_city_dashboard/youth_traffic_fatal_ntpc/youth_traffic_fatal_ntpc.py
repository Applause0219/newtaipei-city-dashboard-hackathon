from airflow import DAG
from operators.common_pipeline import CommonDag

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CATALOG = "https://data.gov.tw/dataset/{ds}"

# 民國年度 -> data.gov.tw 資料集編號。
# 每個年度是獨立的資料集，且資源 UUID 會變動，所以不寫死 opdadm 下載連結，
# 改成每次執行都從目錄頁重新解析當下的連結。
YEAR_DATASETS = {
    114: "177136",
    113: "172969",
    112: "167905",
    111: "161199",
    109: "158864",
    # 110 年度的資料集編號尚未找到，找到後補在這裡即可自動納入
}

NTPC_UNIT = "新北市政府警察局"

# 行政區代碼與 youth_pop_single_age 一致，確保兩份資料可以直接 join。
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


def _find_col(cols, *musts):
    """依角色比對欄位名，不寫死字面名稱。"""
    for c in cols:
        if all(m in c for m in musts):
            return c
    return None


def _district_of(place):
    """從發生地點字串比對行政區。用已知清單比對，不用正規表示式猜。"""
    s = str(place)
    for name in DISTRICTS:
        if name in s:
            return name
    return None


def _transfer(**kwargs):
    import io
    import json
    import re
    import zipfile

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

    def resolve_download_url(dataset_id):
        resp = requests.get(CATALOG.format(ds=dataset_id), headers=headers, timeout=60)
        resp.raise_for_status()
        m = re.search(r"opdadm\.moi\.gov\.tw[^\"'<> ]{20,200}/download", resp.text)
        if not m:
            raise RuntimeError(f"資料集 {dataset_id} 的目錄頁找不到 opdadm 下載連結")
        return "https://" + m.group(0)

    def fetch_a1(dataset_id):
        """下載 ZIP 但只取 A1。A2（受傷事故）每年解壓約 600MB，刻意不處理。"""
        url = resolve_download_url(dataset_id)
        resp = requests.get(url, headers=headers, timeout=900)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        members = [n for n in zf.namelist() if "A1" in n and n.lower().endswith(".csv")]
        if not members:
            raise RuntimeError(f"ZIP 內找不到 A1 檔，成員：{zf.namelist()[:5]}")
        with zf.open(members[0]) as fh:
            return pd.read_csv(fh, dtype=str, encoding="utf-8-sig")

    # ---------- Extract ----------
    frames = []
    for roc_year, dataset_id in sorted(YEAR_DATASETS.items()):
        df = fetch_a1(dataset_id)
        print(f"ROC {roc_year} (dataset {dataset_id}) =========== {len(df)} rows nationwide")
        frames.append((roc_year, df))

    # ---------- Transform ----------
    rows = []
    for roc_year, df in frames:
        cols = df.columns
        unit_col = _find_col(cols, "處理單位")
        place_col = _find_col(cols, "發生地點")
        age_col = _find_col(cols, "年齡")
        sex_col = _find_col(cols, "性", "別")
        veh_col = _find_col(cols, "車種", "大類別")
        cas_col = _find_col(cols, "死亡受傷人數")
        date_col = _find_col(cols, "發生日期")
        time_col = _find_col(cols, "發生時間")
        missing = [
            name for name, col in [
                ("處理單位", unit_col), ("發生地點", place_col), ("年齡", age_col),
                ("性別", sex_col), ("車種大類別", veh_col), ("死亡受傷人數", cas_col),
            ] if col is None
        ]
        if missing:
            raise ValueError(
                f"{roc_year} 年度缺少欄位 {missing}，實際欄位：{list(cols)}"
            )

        sub = df[df[unit_col] == NTPC_UNIT].copy()
        if sub.empty:
            print(f"ROC {roc_year} =========== no New Taipei rows, skipped")
            continue

        sub["_dist"] = sub[place_col].map(_district_of)
        sub["_age"] = pd.to_numeric(sub[age_col], errors="coerce")
        # pandas groupby 預設丟棄 key 為 NaN 的列。實測 114 年度新北 406 筆當事者中
        # 有 45 筆「車種大類別」是空的，不補值就會靜默少掉 11% 的人。
        sub[veh_col] = sub[veh_col].fillna("未填").replace("", "未填")
        sub[sex_col] = sub[sex_col].fillna("未填").replace("", "未填")

        year_ad = roc_year + 1911
        base = {
            "period_start": f"{year_ad}-01-01",
            "period_end": f"{year_ad}-12-31",
            "period_type": "year",
            "area_level": "district",
            "unit": "人",
            "value_type": "count",
        }

        # 指標一：當事者人數。這是唯一帶單齡年齡的層級，18-35 可 exact 直接篩選。
        party = sub.dropna(subset=["_dist", "_age"])
        by_sex = party.groupby(["_dist", "_age", sex_col, veh_col]).size().reset_index(name="n")
        for _, r in by_sex.iterrows():
            age = int(r["_age"])
            gender = {"男": "male", "女": "female"}.get(str(r[sex_col]).strip(), "other")
            rows.append(dict(
                base,
                indicator_id="traffic_fatal_party_count",
                age_lower=age, age_upper=age, age_band_raw=f"{age}歲",
                gender=gender,
                area_code=DISTRICTS[r["_dist"]],
                breakdown=json.dumps(
                    {"vehicle": str(r[veh_col]).strip(), "district_name": r["_dist"]},
                    ensure_ascii=False,
                ),
                value=float(r["n"]),
            ))

        by_total = party.groupby(["_dist", "_age", veh_col]).size().reset_index(name="n")
        for _, r in by_total.iterrows():
            age = int(r["_age"])
            rows.append(dict(
                base,
                indicator_id="traffic_fatal_party_count",
                age_lower=age, age_upper=age, age_band_raw=f"{age}歲",
                gender="total",
                area_code=DISTRICTS[r["_dist"]],
                breakdown=json.dumps(
                    {"vehicle": str(r[veh_col]).strip(), "district_name": r["_dist"]},
                    ensure_ascii=False,
                ),
                value=float(r["n"]),
            ))

        # 指標二、三：事故層級。
        # 「死亡受傷人數」是事故層級欄位、在同一事故的每一列重複出現，
        # 無法歸屬到個別當事者，所以不能產出「青年死亡人數」。
        # 這兩個指標先依事故去重，年齡一律留空並標 unavailable。
        sub["_acc"] = (
            sub[date_col].astype(str) + "_" + sub[time_col].astype(str)
            + "_" + sub[place_col].astype(str)
        )
        acc = sub.dropna(subset=["_dist"]).drop_duplicates("_acc")

        for _, r in acc.groupby("_dist").size().reset_index(name="n").iterrows():
            rows.append(dict(
                base,
                indicator_id="traffic_fatal_accident_count",
                age_lower=None, age_upper=None, age_band_raw=None, gender="total",
                area_code=DISTRICTS[r["_dist"]],
                breakdown=json.dumps({}, ensure_ascii=False),
                value=float(r["n"]), unit="件",
            ))

        acc["_deaths"] = (
            acc[cas_col].astype(str).str.extract(r"死亡\s*(\d+)")[0].astype(float)
        )
        for _, r in acc.groupby("_dist")["_deaths"].sum().reset_index().iterrows():
            rows.append(dict(
                base,
                indicator_id="traffic_fatal_death_count",
                age_lower=None, age_upper=None, age_band_raw=None, gender="total",
                area_code=DISTRICTS[r["_dist"]],
                breakdown=json.dumps({}, ensure_ascii=False),
                value=float(r["_deaths"]),
            ))

        # 對帳：輸出的當事者總數必須等於輸入。分組欄位若含空值會被 groupby 丟掉，
        # 這個檢核讓它直接炸而不是靜默少人。
        emitted = int(by_total["n"].sum())
        if emitted != len(party):
            raise ValueError(
                f"{roc_year} 年度當事者數不一致：輸入 {len(party)} 但輸出 {emitted}，"
                "可能有分組欄位含空值被 groupby 丟棄"
            )
        print(
            f"ROC {roc_year} =========== NTPC {len(sub)} parties / {len(acc)} accidents "
            f"/ reconciled {emitted}"
        )

    if not rows:
        raise RuntimeError("沒有產出任何資料列，來源或篩選條件可能已變更。")

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
    dag_folder="youth_traffic_fatal_ntpc",
)
dag.create_dag(etl_func=_transfer)
