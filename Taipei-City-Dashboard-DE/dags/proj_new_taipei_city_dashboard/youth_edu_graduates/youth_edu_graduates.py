from airflow import DAG
from operators.common_pipeline import CommonDag

URL_TMPL = "https://stats.moe.gov.tw/files/detail/{y}/{y}_graduate.csv"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# 起始學年度。實測 103~114 皆回 200；上界每年往後探到 404 為止，不寫死。
FIRST_ACADEMIC_YEAR = 103

# 來源縣市欄位是「改制前舊碼 + 現行名稱」，直轄市合併後同名不同碼並存：
#   06 臺中市 / 19 臺中市、11 臺南市 / 21 臺南市、12 高雄市 / 50 高雄市
# 依代碼分組會把直轄市拆成兩半，所以一律以名稱對應到標準行政區代碼。
COUNTY_CODE = {
    "臺北市": "63000", "新北市": "65000", "桃園市": "68000", "臺中市": "66000",
    "臺南市": "67000", "高雄市": "64000",
    "基隆市": "10017", "新竹市": "10018", "嘉義市": "10020",
    "宜蘭縣": "10002", "新竹縣": "10004", "苗栗縣": "10005", "彰化縣": "10007",
    "南投縣": "10008", "雲林縣": "10009", "嘉義縣": "10010", "屏東縣": "10013",
    "臺東縣": "10014", "花蓮縣": "10015", "澎湖縣": "10016",
    "金門縣": "09020", "連江縣": "09007",
}


def _find_col(cols, *musts, exclude=()):
    """依角色比對欄位名。來源逐年改名，寫死名稱會在某幾年靜默失敗。"""
    for c in cols:
        if all(m in c for m in musts) and not any(e in c for e in exclude):
            return c
    return None


def _strip_code(v):
    """'B 學士' / 'B學士' -> '學士'；'01 新北市' / '01新北市' -> '新北市'。"""
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    parts = s.split(None, 1)
    if len(parts) == 2:
        return parts[1].strip()
    # 無空格時，代碼前綴不是純數字（01新北市）就是單一大寫英文（B學士、X4+X）。
    # X4+X 只能剝一個字元，多剝會把 '4+X' 切成 '+X'。
    if s[0].isascii() and s[0].isupper() and len(s) > 1:
        return s[1:].strip() or s
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    return s[i:].strip() or s


def _code_of(v):
    """取代碼前綴。名稱會漂移（C 二年制 → C 二技），代碼才是穩定識別。"""
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    head = s.split(None, 1)[0] if " " in s or "　" in s else s[:1]
    return head.strip() or None


def _transfer(**kwargs):
    import io
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

    def num(v):
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    # ---------- Extract ----------
    # 逐學年度抓，往後探到連續兩次 404 為止（新學年度上架時自動納入）。
    frames = []
    year, misses = FIRST_ACADEMIC_YEAR, 0
    while misses < 2:
        resp = requests.get(
            URL_TMPL.format(y=year), headers={"User-Agent": UA}, timeout=60
        )
        if resp.status_code == 404:
            misses += 1
            year += 1
            continue
        resp.raise_for_status()
        df = pd.read_csv(io.BytesIO(resp.content), encoding="utf-8-sig", dtype=str)
        frames.append((year, df))
        print(f"academic year {year} =========== {len(df)} rows")
        misses = 0
        year += 1

    if not frames:
        raise RuntimeError("沒有抓到任何學年度的資料，來源網址可能已變更。")

    # ---------- Transform ----------
    rows = []
    expected_total = 0.0
    for file_year, df in frames:
        df = df.rename(columns={c: c.strip() for c in df.columns})
        # 同一機關同一張表，12 個學年度的欄位名改過三次：
        #   103-106 用「日間_進修別」，107 起改成全形除號的「日間∕進修別」
        #   110、111 兩年把「上學年畢業生男/女」改叫「男生/女生」
        #   「學年度」欄位在 103-106、108、110、111 根本不存在
        # 所以依角色比對欄位，並在找不到時直接拋錯而非產出空表。
        male_col = _find_col(df.columns, "男")
        female_col = _find_col(df.columns, "女")
        daynight_col = _find_col(df.columns, "日間")
        if not male_col or not female_col:
            raise ValueError(
                f"學年度 {file_year} 找不到性別欄位，實際欄位：{list(df.columns)}"
            )

        # 欄位是「上學年畢業生」：113 學年度的調查記錄的是 112 學年度畢業的人。
        # 因此實際畢業學年度 = 檔案學年度 - 1，period 依畢業學年度設定，
        # 檔案學年度另存 breakdown.source_academic_year 以便回溯。
        grad_ay = file_year - 1
        period_start = f"{grad_ay + 1911}-08-01"
        period_end = f"{grad_ay + 1912}-07-31"

        for _, r in df.iterrows():
            county = _strip_code(r.get("縣市名稱"))
            area_code = COUNTY_CODE.get(county)
            if area_code is None:
                continue
            m, f = num(r.get(male_col)), num(r.get(female_col))
            if m is None and f is None:
                continue
            expected_total += (m or 0) + (f or 0)
            breakdown = json.dumps({
                "level": _strip_code(r.get("等級別")),
                "level_code": _code_of(r.get("等級別")),
                "day_night": _strip_code(r.get(daynight_col)) if daynight_col else None,
                "system": _strip_code(r.get("體系別")),
                "school": str(r.get("學校名稱", "")).strip(),
                "school_code": str(r.get("學校代碼", "")).strip(),
                "county": county,
                "source_academic_year": str(file_year),
            }, ensure_ascii=False)

            for gender, val in (
                ("male", m),
                ("female", f),
                ("total", (m or 0) + (f or 0)),
            ):
                if val is None:
                    continue
                rows.append({
                    "indicator_id": "college_graduate_count",
                    "period_start": period_start,
                    "period_end": period_end,
                    "period_type": "academic_year",
                    # 教育資料只有學制沒有年齡。學制推年齡是推論不是事實，
                    # 刻意不做——年齡欄位留空，涵蓋度標 unavailable。
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "gender": gender,
                    "area_code": area_code,
                    "area_level": "city",
                    "breakdown": breakdown,
                    "value": val,
                    "unit": "人",
                    "value_type": "count",
                })

    if not rows:
        raise RuntimeError("沒有產出任何資料列，來源結構或縣市對照可能已變更。")

    data = pd.DataFrame(rows)

    # 對帳：輸出的 gender=total 值合計，必須等於輸入中可對應縣市那些列的男女相加。
    # 縣市名稱若因來源改寫而對不上 COUNTY_CODE，這裡會直接炸，
    # 而不是安靜地少掉整個縣市。
    emitted_total = float(
        data.loc[data["gender"] == "total", "value"].sum()
    )
    if abs(emitted_total - expected_total) > 0.5:
        raise ValueError(
            f"畢業生數對帳失敗：輸入 {expected_total:,.0f} 但輸出 {emitted_total:,.0f}。"
            "可能有縣市名稱對不上 COUNTY_CODE，或分組欄位含空值被丟棄。"
        )
    print(
        f"reconciled =========== {expected_total:,.0f} graduates across "
        f"{data['area_code'].nunique()} counties, {len(frames)} academic years"
    )

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
    dag_folder="youth_edu_graduates",
)
dag.create_dag(etl_func=_transfer)
