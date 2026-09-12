from airflow import DAG
from operators.common_pipeline import CommonDag


API = (
    "https://data.ntpc.gov.tw/api/datasets/"
    "2fdff679-af9c-46ab-bd2b-4555d24163c6/json"
)
PAGE_SIZE = 5000
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

SOURCE_COLUMNS = ("year", "age", "item", "total", "men person", "female person")


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"犯罪來源欄位 {field} 為空")
    return value


def _number(value, field):
    """嚴格解析計數；來源若有千分位逗號仍要保留正確數字。"""
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"犯罪來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"犯罪來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"犯罪來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _find_gender_mismatches(records):
    """找出來源 total 與男女欄不相等的原始列，保留來源值不自行修正。"""
    mismatches = []
    for row_number, record in enumerate(records, start=1):
        total = _number(record.get("total"), "total")
        male = _number(record.get("men person"), "men person")
        female = _number(record.get("female person"), "female person")
        if abs(total - male - female) > 0.5:
            mismatches.append(
                {
                    "row_number": row_number,
                    "year": str(record.get("year")),
                    "age": str(record.get("age")),
                    "item": str(record.get("item")),
                    "total": total,
                    "male": male,
                    "female": female,
                }
            )
    return mismatches


def _parse_age(label):
    """回傳 (下界、上界、age_scope)，不認得的標籤直接拋錯。"""
    import re

    s = str(label).strip()
    if s == "總計":
        return None, None, "all_age"
    if s == "年齡不詳嫌犯數":
        return None, None, "unknown_age"

    match = re.fullmatch(r"(\d{1,3})-(\d{1,3})歲嫌犯數", s)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"犯罪來源年齡區間上下界顛倒：{label!r}")
        return lower, upper, "age_band"

    match = re.fullmatch(r"(\d{1,3})歲以上嫌犯數", s)
    if match:
        return int(match.group(1)), None, "age_band"

    raise ValueError(f"無法解析犯罪來源年齡標籤：{label!r}")


def _item_scope(label):
    """區分明細、罪名階層合計與全罪名合計，避免下游誤加總。"""
    if label == "總計":
        return "all_items"
    if "總計" in label:
        return "category_total"
    return "crime_detail"


def _indicator_id(age_scope, item_scope):
    ids = {
        ("age_band", "crime_detail"): "crime_suspect_count",
        ("age_band", "category_total"): "crime_suspect_category_total_count",
        ("age_band", "all_items"): "crime_suspect_all_item_count",
        ("unknown_age", "crime_detail"): "crime_suspect_unknown_age_count",
        ("unknown_age", "category_total"):
            "crime_suspect_unknown_age_category_total_count",
        ("unknown_age", "all_items"): "crime_suspect_unknown_age_all_item_count",
        ("all_age", "crime_detail"): "crime_suspect_all_age_count",
        ("all_age", "category_total"):
            "crime_suspect_all_age_category_total_count",
        ("all_age", "all_items"): "crime_suspect_all_age_all_item_count",
    }
    try:
        return ids[(age_scope, item_scope)]
    except KeyError as exc:
        raise ValueError(
            f"未定義的犯罪年齡／罪名層級組合：{age_scope}, {item_scope}"
        ) from exc


def fetch_records():
    """分頁抓到空頁為止；data.ntpc 的頁碼是 0-based。"""
    import requests

    records = []
    page = 0
    with requests.Session() as session:
        while page < 100:
            response = session.get(
                API,
                headers={"User-Agent": UA},
                params={"page": page, "size": PAGE_SIZE},
                timeout=120,
            )
            response.raise_for_status()
            page_records = response.json()
            if not isinstance(page_records, list):
                raise RuntimeError(
                    f"犯罪來源第 {page} 頁不是 JSON 陣列："
                    f"{type(page_records).__name__}"
                )
            print(f"crime page {page}: {len(page_records)} rows")
            if not page_records:
                return records
            records.extend(page_records)
            page += 1
    raise RuntimeError("犯罪來源分頁超過 100 頁，疑似 API 行為改變。")


# ----------------------------------------------------------------------------
# 來源尺度異常防護
#
# 民國 114 年度的數值是全國規模而非新北市：全年齡全罪名 total = 428,284，
# 是前一年 37,221 的 11.5 倍。但它的年齡結構與前兩年幾乎相同
# （18-23 佔 11.3% vs 11.0/12.3、30-39 佔 21.2% vs 21.4/21.2），
# 是「同形狀、不同量級」，代表不是資料損壞而是範圍或口徑改變。
# 且 37,221 / 428,284 = 8.7%，恰與新北市人口佔全國比例相符，
# 研判來源誤將全國數字放進新北市資料集。
#
# 這種列若直接入庫，儀表板會畫出一根 11.5 倍的假尖峰，
# 任何趨勢分析都會失效。因此明確排除，並在來源澄清後移除此設定即可。
# ----------------------------------------------------------------------------
KNOWN_BAD_YEARS = {
    114: "數值為全國規模而非新北市（詳見 SOURCE_NOTES.md）",
}
# 未知的新異常一律拋錯，不要靜默吃進去。
SCALE_ANOMALY_FACTOR = 3.0


def _year_grand_totals(records):
    """各年度的全年齡全罪名 total。"""
    totals = {}
    for record in records:
        if _text(record.get("age"), "age") != "總計":
            continue
        if _text(record.get("item"), "item") != "總計":
            continue
        year = int(_text(record.get("year"), "year"))
        totals[year] = _number(record.get("total"), "total")
    return totals


def drop_scope_anomalies(records):
    """排除已知的尺度異常年度，並對未知的新異常拋錯。"""
    totals = _year_grand_totals(records)
    if not totals:
        raise RuntimeError("找不到任何年度的『總計/總計』列，來源結構可能已變更。")

    kept = {y: v for y, v in totals.items() if y not in KNOWN_BAD_YEARS}
    if len(kept) >= 3:
        ordered = sorted(kept.values())
        median = ordered[len(ordered) // 2]
        for year, value in sorted(kept.items()):
            if median and (value > median * SCALE_ANOMALY_FACTOR
                           or value * SCALE_ANOMALY_FACTOR < median):
                raise ValueError(
                    f"民國 {year} 年度全年齡全罪名 total={value:.0f}，"
                    f"與其餘年度中位數 {median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍。"
                    "這通常代表來源的統計範圍或口徑改變（例如誤放全國數字）。"
                    "請先確認語意，確認後再加入 KNOWN_BAD_YEARS 或修正轉換，"
                    "不要讓它靜默入庫。"
                )

    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if year in totals:
            print(
                f"crime scope guard =========== 排除民國 {year} 年度"
                f"（total={totals[year]:.0f}）：{reason}"
            )

    excluded = set(KNOWN_BAD_YEARS)
    remaining = [
        r for r in records
        if int(_text(r.get("year"), "year")) not in excluded
    ]
    print(
        f"crime scope guard =========== 保留 {len(remaining)} / {len(records)} 列，"
        f"年度 {sorted(set(int(_text(r.get('year'), 'year')) for r in remaining))}"
    )
    return remaining


def _reconcile(records, data):
    """以 total 性別列對帳原始 total，避免 gender 合計列被重複計算。"""
    expected = {}
    for record in records:
        year = int(_text(record.get("year"), "year"))
        expected[year] = expected.get(year, 0.0) + _number(
            record.get("total"), "total"
        )

    total_rows = data[data["gender"] == "total"]
    if len(total_rows) != len(records):
        raise ValueError(
            f"犯罪輸出 total 性別列數不一致：輸入 {len(records)}、"
            f"輸出 {len(total_rows)}"
        )
    if len(data) != len(records) * 3:
        raise ValueError(
            f"犯罪輸出列數不一致：輸入 {len(records)}、輸出 {len(data)}，"
            "每筆來源應有 male/female/total 三列"
        )

    for roc_year, expected_total in sorted(expected.items()):
        period_start = f"{roc_year + 1911}-01-01"
        emitted = float(
            total_rows.loc[total_rows["period_start"] == period_start, "value"].sum()
        )
        if abs(emitted - expected_total) > 0.5:
            raise ValueError(
                f"民國 {roc_year} 犯罪嫌疑犯數對帳失敗："
                f"輸入 {expected_total}、輸出 {emitted}"
            )
        print(
            f"ROC {roc_year}: input total {int(expected_total)}, "
            f"output total {int(emitted)}, reconciled"
        )


def transform_records(records, data_time=None):
    """把 API 原始列轉成青年事實表契約；DAG 與 verify 共用此函式。"""
    import json

    import pandas as pd

    if not records:
        raise RuntimeError("犯罪來源沒有任何資料列。")

    rows = []
    gender_mismatches = []
    item_occurrences = {}
    for row_number, record in enumerate(records, start=1):
        missing = [column for column in SOURCE_COLUMNS if column not in record]
        if missing:
            raise ValueError(
                f"犯罪來源第 {row_number} 列缺少欄位 {missing}；"
                f"實際欄位：{list(record)}"
            )

        roc_year = int(_text(record["year"], "year"))
        age_label = _text(record["age"], "age")
        item = _text(record["item"], "item")
        age_lower, age_upper, age_scope = _parse_age(age_label)
        item_scope = _item_scope(item)
        indicator_id = _indicator_id(age_scope, item_scope)

        counts = {
            "male": _number(record["men person"], "men person"),
            "female": _number(record["female person"], "female person"),
            "total": _number(record["total"], "total"),
        }
        if abs(counts["total"] - counts["male"] - counts["female"]) > 0.5:
            gender_mismatches.append(row_number)

        occurrence_key = (roc_year, age_label, item)
        item_occurrences[occurrence_key] = item_occurrences.get(occurrence_key, 0) + 1
        item_occurrence = item_occurrences[occurrence_key]

        period_start = f"{roc_year + 1911}-01-01"
        period_end = f"{roc_year + 1911}-12-31"
        breakdown = json.dumps(
            {
                "crime_item": item,
                "item_occurrence": item_occurrence,
                "item_scope": item_scope,
                "age_scope": age_scope,
                "source_age_label": age_label,
            },
            ensure_ascii=False,
        )
        for gender, value in counts.items():
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "period_type": "year",
                    "age_lower": age_lower,
                    "age_upper": age_upper,
                    "age_band_raw": age_label,
                    "gender": gender,
                    "area_code": "65000",
                    "area_level": "city",
                    "breakdown": breakdown,
                    "value": value,
                    "unit": "人",
                    "value_type": "count",
                    "data_time": data_time,
                }
            )

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    if data.empty:
        raise RuntimeError("犯罪來源轉換後沒有資料列。")
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if data_time is None:
        from datetime import datetime, timedelta, timezone

        data["data_time"] = datetime.now(
            timezone(timedelta(hours=8))
        ).isoformat()
    if gender_mismatches:
        print(
            "來源警告："
            f"{len(gender_mismatches)} 筆 total != men person + female person；"
            "保留三個來源欄位原值，不自行修正，詳見 SOURCE_NOTES。"
        )
    _reconcile(records, data)
    return data


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

    data = transform_records(
        drop_scope_anomalies(fetch_records()),
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
    update_lasttime_in_data_to_dataset_info(
        engine, dag_id, data["data_time"].max()
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_crime_suspect_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
