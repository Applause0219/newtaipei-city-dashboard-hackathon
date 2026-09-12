from airflow import DAG
from operators.common_pipeline import CommonDag

# ---------------------------------------------------------------------------
# 新北市政府統計資料庫查詢平台（oas.bas.ntpc.gov.tw）
#
# 這是命題文件明列的第一個建議來源。它是 ASP.NET WebForms，沒有 API，
# 先前兩輪探勘都卡在「查詢回 200 但每個數值都是 ...」。
#
# 原因不是介面拒絕返回數值，而是**少送了一個維度**。
# 報表頁自己的 JS 是這樣組查詢參數的：
#     sComplexs += ";" + 該 lisComplex 第一個 option 值的前 5 碼
#     axX    = "[Measures]" + sComplexs
#     axCode = 各 lisComplex 已選值(以;連接) + ";" … + 地區代碼
# 報表 11843 有兩個複分類：lisComplex1 = 年齡組（P0203…），
# lisComplex2 = 性別（P0204…）。先前只送了 P0203，
# 於是 axX 少了 ";P0204"、axCode 也少了性別那一段，伺服器解不出格子就回 "..."。
#
# 補上第二個維度後取得真實數值，並與 youth_pop_single_age（戶政司 RIS）
# 交叉驗證：2025 年新北市總人口與 15-19／20-24／25-29／30-34 四組
# **逐一完全相同，差異為 0**。
# ---------------------------------------------------------------------------

BASE = "https://oas.bas.ntpc.gov.tw/DgbasWeb/Page/"
NAV = BASE + "StatFile2.aspx?Mode=3&CateNo=121&ParentNo=null12102"
SHOW = "https://oas.bas.ntpc.gov.tw/網頁資料查詢/ShowQuery.aspx"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

REPORT_ID = "11843"          # 現住人口之年齡分配
NTPC_AREA_CODE = "010000065000"
NTPC_CITY = "65000"


def _hidden_fields(html):
    import re
    out = {}
    for rx in (
        re.compile(r'<input[^>]+type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', re.I),
        re.compile(r'<input[^>]+name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', re.I),
    ):
        for k, v in rx.findall(html):
            out.setdefault(k, v)
    return out


def _select(html, fragment):
    """回傳 (select 的 name, [(value, 顯示文字), …])。"""
    import re
    m = re.search(r'<select[^>]*name="([^"]*' + fragment + r')"[^>]*>(.*?)</select>',
                  html, re.S | re.I)
    if not m:
        return None, []
    opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>',
                      m.group(2), re.S | re.I)
    return m.group(1), [(v, re.sub(r"<[^>]+>", "", t).strip()) for v, t in opts]


def _parse_age_label(label):
    """'0_4歲' -> (0, 4)；'100歲以上' -> (100, None)；'總計' -> (None, None)。"""
    import re
    s = str(label).strip()
    if s in ("總計", "計"):
        return None, None
    m = re.fullmatch(r"(\d{1,3})_(\d{1,3})歲", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{1,3})歲以上", s)
    if m:
        return int(m.group(1)), None
    m = re.fullmatch(r"未滿(\d{1,3})歲", s)
    if m:
        return None, int(m.group(1)) - 1
    raise ValueError(f"無法解析 oas.bas 年齡標籤：{label!r}")


def _transfer(**kwargs):
    import json
    import re

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

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    # ---------- 進入報表，取得可用維度與日期 ----------
    nav = session.get(NAV, timeout=180)
    nav.raise_for_status()
    body = _hidden_fields(nav.text)
    body["__EVENTTARGET"] = "lbtGUID"
    body["__EVENTARGUMENT"] = REPORT_ID
    rpt = session.post(NAV, data=body, timeout=300)
    rpt.raise_for_status()
    html = rpt.text
    print(f"report {REPORT_ID} =========== {len(rpt.content):,} bytes")

    complexes = []
    for i in range(1, 6):
        name, opts = _select(html, f"lisComplex{i}")
        if name and opts:
            complexes.append((name, opts))
    if len(complexes) < 2:
        raise RuntimeError(
            f"報表 {REPORT_ID} 只找到 {len(complexes)} 個複分類。"
            "少送任何一個維度，ShowQuery 會回傳全是 '...' 的空表，"
            "所以這裡直接失敗而不是產出空資料。"
        )
    date_name, date_opts = _select(html, "lisDate")
    if not date_opts:
        raise RuntimeError("找不到日期選項，來源頁結構可能已改變。")
    print(f"dimensions =========== "
          f"{[(n.split('$')[-1], len(o)) for n, o in complexes]}, "
          f"dates={len(date_opts)}")

    age_opts = complexes[0][1]
    sex_opts = complexes[1][1]
    # complexes 的元素是 (select_name, [(value, text), …])。
    # 前綴要取「第一個 option 的 value 前 5 碼」，不是 select name。
    ax_x = "[Measures]" + "".join(";" + opts[0][0][:5] for _, opts in complexes)
    ax_code = "".join(";".join(v for v, _ in o) + ";" for _, o in complexes) \
        + NTPC_AREA_CODE

    fields = _hidden_fields(html)

    def query(date_value):
        q = dict(fields)
        q.update({
            "__EVENTTARGET": "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1$goon",
            "__EVENTARGUMENT": "",
            "axCycle": "Y年", "axY": "[Date];[Place]", "axDate": date_value,
            "axEffect": REPORT_ID, "axFunction": "統計數值", "axMode": "3",
            "axSubjectNo": REPORT_ID, "axX": ax_x, "axCode": ax_code,
            "ShowQueryReturnUrl": NAV,
            "UrlOrgNo": "", "FindString": "", "Complex": "", "Statistic": "",
            "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1$ddlFunctiontype": "統計數值",
            date_name: date_value,
        })
        payload = list(q.items())
        for name, opts in complexes:
            payload.extend((name, v) for v, _ in opts)
        resp = session.post(SHOW, data=payload, timeout=300)
        resp.raise_for_status()
        return resp.text

    def cells_of(result_html):
        tables = re.findall(r"<table.*?</table>", result_html, re.S | re.I)
        if not tables:
            return []
        big = max(tables, key=lambda t: len(re.findall(r"<t[dh]", t)))
        raw = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", big, re.S | re.I)
        vals = []
        for c in raw:
            t = re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
            if t:
                vals.append(t)
        return vals

    # ---------- 逐年度查詢 ----------
    expected = len(age_opts) * len(sex_opts)
    rows = []
    for date_value, date_label in date_opts:
        page = query(date_value)
        vals = cells_of(page)
        if not vals:
            print(f"{date_value} =========== 無資料表，跳過")
            continue
        # 第一格是「年份+地區+第一個數值」黏在一起，切出結尾的數值
        head = re.search(r"([\d,]+(?:\.\d+)?)\s*$", vals[0])
        if not head:
            print(f"{date_value} =========== 首格無數值（{vals[0][:40]!r}），跳過")
            continue
        numbers = [head.group(1)] + vals[1:]
        numbers = [v for v in numbers if re.fullmatch(r"[\d,]+(\.\d+)?", v)]
        if len(numbers) != expected:
            raise ValueError(
                f"{date_value} 取得 {len(numbers)} 個數值，"
                f"但維度是 {len(age_opts)} 年齡組 × {len(sex_opts)} 性別 = {expected}。"
                "來源版面可能已改變，先確認再調整解析，不要讓它靜默對錯格。"
            )

        year_ad = int(date_value[:4])
        for idx, raw in enumerate(numbers):
            age_label = age_opts[idx // len(sex_opts)][1]
            sex_label = sex_opts[idx % len(sex_opts)][1]
            lo, hi = _parse_age_label(age_label)
            rows.append({
                "indicator_id": "population_count",
                "period_start": f"{year_ad}-01-01",
                "period_end": f"{year_ad}-12-31",
                "period_type": "year",
                "age_lower": lo, "age_upper": hi,
                "age_band_raw": age_label,
                "gender": {"計": "total", "男": "male", "女": "female"}.get(
                    sex_label, "other"),
                "area_code": NTPC_CITY, "area_level": "city",
                "breakdown": json.dumps(
                    {"source_report": REPORT_ID, "source_platform": "oas.bas.ntpc.gov.tw"},
                    ensure_ascii=False),
                "value": float(raw.replace(",", "")),
                "unit": "人", "value_type": "count",
            })
        print(f"{date_value} ({date_label}) =========== {len(numbers)} values")

    if not rows:
        raise RuntimeError("沒有取得任何年度的資料。")

    data = pd.DataFrame(rows)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    data["data_time"] = get_tpe_now_time_str(is_with_tz=True)

    # 對帳：每個年度的「總計/計」必須等於各分齡組「計」的加總
    for period, grp in data[data.gender == "total"].groupby("period_start"):
        total = grp[grp.age_band_raw == "總計"]["value"]
        parts = grp[grp.age_band_raw != "總計"]["value"].sum()
        if len(total) == 1 and abs(float(total.iloc[0]) - parts) > 0.5:
            raise ValueError(
                f"{period} 總計 {float(total.iloc[0]):.0f} "
                f"與分齡加總 {parts:.0f} 不符，解析可能對錯格。"
            )
    print(f"reconciliation =========== passed for "
          f"{data.period_start.nunique()} periods")
    print(f"ready_data shape =========== {data.shape}")

    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine, data=data, load_behavior=load_behavior,
        default_table=default_table,
    )
    update_lasttime_in_data_to_dataset_info(engine, dag_id, data["data_time"].max())


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_oas_population_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
