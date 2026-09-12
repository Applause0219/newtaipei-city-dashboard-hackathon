from airflow import DAG
from operators.common_pipeline import CommonDag


# 來源欄位無語意（field1 / percent2 … percent17）。
# 對應取自資料集頁面的「主要欄位說明」，但該說明在 percent15 之後被截斷。
# percent16/17 以「各年齡組男女分別加總 == 合計男/合計女」反推確認為 65 歲以上，
# 19 個年度 × 男女 = 38 組對帳全數相符（見 SOURCE_NOTES.md）。
AGE_COLS = [
    # (男欄, 女欄, age_lower, age_upper, 原始標籤)
    ("percent6", "percent7", None, 11, "未滿12歲"),
    ("percent8", "percent9", 12, 17, "12-17歲"),
    ("percent10", "percent11", 18, 29, "18-29歲"),
    ("percent12", "percent13", 30, 49, "30-49歲"),
    ("percent14", "percent15", 50, 64, "50-64歲"),
    ("percent16", "percent17", 65, None, "65歲以上"),
]
TOTAL_COLS = ("percent4", "percent5")   # 志工人數合計 男 / 女
CHAIR_COLS = ("percent2", "percent3")   # 社區發展協會理事長人數 男 / 女

RID = "06f26ed3-13e0-4650-b767-fb91d665ceff"


def _transfer(**kwargs):
    import json

    import pandas as pd
    from sqlalchemy import create_engine
    from utils.extract_stage import NewTaipeiAPIClient
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

    # ---------- Extract ----------
    client = NewTaipeiAPIClient(RID, input_format="json")
    records = client.get_all_data(size=1000)
    print(f"raw records =========== {len(records)}")

    def num(v):
        """來源數值為字串且可能含千分位或空值。"""
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    # ---------- Validate ----------
    # 對帳：各年齡組加總必須等於來源自帶的合計欄。
    # 這同時驗證了 percent16/17 的語意推論，來源若改版而欄位語意飄移，這裡會先炸。
    mismatches = 0
    for r in records:
        for idx, total_col in enumerate(TOTAL_COLS):
            parts = [num(r.get(c[idx])) for c in AGE_COLS]
            if any(p is None for p in parts):
                continue
            if abs(sum(parts) - (num(r.get(total_col)) or 0)) >= 0.5:
                mismatches += 1
    if mismatches:
        raise ValueError(
            f"年齡組加總與合計欄不符 {mismatches} 組。"
            "來源欄位語意可能已變動，請重新核對 AGE_COLS 對應後再執行。"
        )
    print("column mapping reconciliation =========== passed")

    # ---------- Transform ----------
    rows = []
    for r in records:
        year = str(r.get("field1", "")).strip()
        if not year.isdigit():
            continue
        y = int(year)
        base = {
            "period_start": f"{y}-01-01",
            "period_end": f"{y}-12-31",
            "period_type": "year",
            "area_code": "65000",
            "area_level": "city",
            "unit": "人",
            "value_type": "count",
        }
        agency = json.dumps({"agency": "社會局"}, ensure_ascii=False)

        # 志工人數（分年齡組）
        for male_col, female_col, lo, hi, label in AGE_COLS:
            m, f = num(r.get(male_col)), num(r.get(female_col))
            if m is None and f is None:
                continue
            for gender, val in (
                ("male", m),
                ("female", f),
                ("total", (m or 0) + (f or 0)),
            ):
                if val is None:
                    continue
                rows.append(dict(
                    base,
                    indicator_id="volunteer_count",
                    age_lower=lo, age_upper=hi, age_band_raw=label,
                    gender=gender, value=val, breakdown=agency,
                ))

        # 志工人數合計（來源自帶，保留供對帳；年齡欄位留空避免與分組列重複加總）
        m, f = num(r.get(TOTAL_COLS[0])), num(r.get(TOTAL_COLS[1]))
        for gender, val in (
            ("male", m),
            ("female", f),
            ("total", (m or 0) + (f or 0) if m is not None else None),
        ):
            if val is None:
                continue
            rows.append(dict(
                base,
                indicator_id="volunteer_count",
                age_lower=None, age_upper=None, age_band_raw="合計",
                gender=gender, value=val, breakdown=agency,
            ))

        # 社區發展協會理事長人數：同一份來源的另一個指標，無年齡別
        m, f = num(r.get(CHAIR_COLS[0])), num(r.get(CHAIR_COLS[1]))
        for gender, val in (
            ("male", m),
            ("female", f),
            ("total", (m or 0) + (f or 0) if m is not None else None),
        ):
            if val is None:
                continue
            rows.append(dict(
                base,
                indicator_id="community_association_chair_count",
                age_lower=None, age_upper=None, age_band_raw=None,
                gender=gender, value=val,
                breakdown=json.dumps({}, ensure_ascii=False),
            ))

    data = pd.DataFrame(rows)
    # age_lower / age_upper 對應 smallint。「未滿12歲」的下界與「65歲以上」的上界都是 None，
    # 不指定 nullable 整數型別的話 pandas 會把整欄推成 float64。
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
    dag_folder="youth_volunteer_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
