"""財政部財政資訊中心：新北市各區綜合所得稅地區脈絡。"""

import io
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

try:
    from operators.common_pipeline import CommonDag
except ModuleNotFoundError:
    CommonDag = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131 Safari/537.36"
)
ROC_YEARS = tuple(range(101, 112))
KNOWN_BAD_YEARS = set()

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


def _url(roc_year):
    return f"https://www.fia.gov.tw/WEB/fia/ias/ias{roc_year}/{roc_year}_165-9.csv"


def _get(url):
    import requests
    import urllib3

    try:
        response = requests.get(url, headers={"User-Agent": UA}, timeout=120)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError:
        urllib3.disable_warnings()
        response = requests.get(
            url, headers={"User-Agent": UA}, timeout=120, verify=False
        )
        response.raise_for_status()
        return response


def fetch_source():
    sources = []
    for roc_year in ROC_YEARS:
        source_url = _url(roc_year)
        response = _get(source_url)
        frame = pd.read_csv(
            io.BytesIO(response.content), encoding="utf-8-sig", dtype=str
        )
        if frame.empty:
            raise RuntimeError(f"財政部所得稅 CSV 為空：ROC {roc_year}")
        sources.append((roc_year, source_url, frame))
    return sources


def _find_one(columns, predicate, role):
    matches = [c for c in columns if predicate(str(c))]
    if len(matches) != 1:
        raise ValueError(f"財政部所得稅找不到唯一{role}欄位：{matches!r}")
    return matches[0]


def _number(value, field, allow_missing=False):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "-"}:
        if allow_missing:
            return None
        raise ValueError(f"財政部所得稅欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"財政部所得稅欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"財政部所得稅欄位 {field} 出現無效數值：{value!r}")
    return number


def _district(value):
    text = str(value).strip()
    for name in DISTRICTS:
        if name in text and "新北市" in text:
            return name
    return None


def _scale_guard(data):
    totals = (
        data.groupby(["indicator_id", "period_start"], dropna=False)["value"]
        .sum()
        .reset_index()
    )
    checked = 0
    for indicator, group in totals.groupby("indicator_id", dropna=False):
        if len(group) < 3:
            continue
        median = group["value"].median()
        if median <= 0:
            continue
        bad = group[
            (group["value"] > median * 3) | (group["value"] < median / 3)
        ]
        bad = bad[~bad["period_start"].astype(str).str[:4].isin(KNOWN_BAD_YEARS)]
        if not bad.empty:
            raise ValueError(f"財政部所得稅尺度異常：{indicator} {bad.to_dict('records')}")
        checked += 1
    print(f"scale_guard =========== checked={checked}, periods={len(totals)}")


def transform(sources, data_time=None):
    if data_time is None:
        data_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
    rows = []
    expected_groups = 0
    input_tax_units = 0.0
    input_income_total = 0.0
    for roc_year, source_url, frame in sources:
        columns = list(frame.columns)
        district_col = _find_one(
            columns,
            lambda c: "鄉鎮市區" in c or "縣市別" in c,
            "行政區",
        )
        village_col = _find_one(columns, lambda c: "村里" in c, "村里")
        tax_unit_col = _find_one(columns, lambda c: "納稅單位" in c, "納稅單位")
        income_total_col = _find_one(
            columns, lambda c: "綜合所得總額" in c, "綜合所得總額"
        )
        # 平均數欄只用來確認欄位結構；區級平均數由總額／戶數加權重算。
        _find_one(columns, lambda c: "平均數" in c or "平均" in c, "平均數")

        work = frame.copy()
        work["_district"] = work[district_col].map(_district)
        work = work[work["_district"].notna()].copy()
        if work.empty:
            raise ValueError(f"ROC {roc_year} 找不到新北市行政區資料")
        work["_tax_units"] = work[tax_unit_col].map(
            lambda value: _number(value, tax_unit_col)
        )
        work["_income_total"] = work[income_total_col].map(
            lambda value: _number(value, income_total_col)
        )
        if work[["_tax_units", "_income_total"]].isna().any().any():
            raise ValueError(f"ROC {roc_year} 新北市資料有無法解析的數值")

        grouped = (
            work.groupby("_district", dropna=False)
            .agg(
                tax_units=("_tax_units", "sum"),
                income_total=("_income_total", "sum"),
                source_rows=(village_col, "count"),
            )
            .reset_index()
        )
        expected_groups += len(grouped)
        input_tax_units += float(work["_tax_units"].sum())
        input_income_total += float(work["_income_total"].sum())
        ad_year = roc_year + 1911
        for _, record in grouped.iterrows():
            district_name = record["_district"]
            breakdown_base = {
                "district_name": district_name,
                "source_roc_year": roc_year,
                "source_village_rows": int(record["source_rows"]),
                "source_url": source_url,
                "age_status": "unavailable",
            }
            values = [
                (
                    "income_tax_tax_unit_count",
                    float(record["tax_units"]),
                    "戶",
                    "count",
                ),
                (
                    "income_tax_total_income",
                    float(record["income_total"]),
                    "千元",
                    "sum",
                ),
                (
                    "income_tax_mean_income",
                    float(record["income_total"]) / float(record["tax_units"]),
                    "千元/戶",
                    "mean",
                ),
            ]
            for indicator_id, value, unit, value_type in values:
                rows.append(
                    {
                        "indicator_id": indicator_id,
                        "period_start": f"{ad_year}-01-01",
                        "period_end": f"{ad_year}-12-31",
                        "period_type": "year",
                        "age_lower": None,
                        "age_upper": None,
                        "age_band_raw": None,
                        "gender": "total",
                        "area_code": DISTRICTS[district_name],
                        "area_level": "district",
                        "breakdown": json.dumps(breakdown_base, ensure_ascii=False),
                        "value": value,
                        "unit": unit,
                        "value_type": value_type,
                        "data_time": data_time,
                    }
                )

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    if data.empty or len(data) != expected_groups * 3:
        raise ValueError(
            f"財政部所得稅群組對帳失敗：輸入群組 {expected_groups}，輸出列 {len(data)}"
        )
    output_tax_units = float(
        data.loc[data["indicator_id"] == "income_tax_tax_unit_count", "value"].sum()
    )
    output_income_total = float(
        data.loc[data["indicator_id"] == "income_tax_total_income", "value"].sum()
    )
    if abs(output_tax_units - input_tax_units) > 0.5:
        raise ValueError(
            f"財政部所得稅納稅單位對帳失敗：輸入 {input_tax_units}，輸出 {output_tax_units}"
        )
    if abs(output_income_total - input_income_total) > 0.5:
        raise ValueError(
            f"財政部所得稅所得總額對帳失敗：輸入 {input_income_total}，輸出 {output_income_total}"
        )
    data["age_lower"] = pd.Series(data["age_lower"], dtype="Int16")
    data["age_upper"] = pd.Series(data["age_upper"], dtype="Int16")
    _scale_guard(data)
    print(
        "reconciliation =========== "
        f"tax_units={output_tax_units:.0f}, income_total={output_income_total:.0f}"
    )
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs["dag_infos"]
    data = transform(
        fetch_source(),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")
    print(data.groupby("indicator_id")["value"].agg(["count", "sum"]))
    engine = create_engine(kwargs["ready_data_db_uri"])
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos.get("load_behavior"),
        default_table=dag_infos.get("ready_data_default_table"),
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_infos["dag_id"], data["data_time"].max()
    )


if CommonDag is not None:
    dag = CommonDag(
        proj_folder="proj_new_taipei_city_dashboard",
        dag_folder="youth_income_tax_ntpc",
    )
    dag.create_dag(etl_func=_transfer)
