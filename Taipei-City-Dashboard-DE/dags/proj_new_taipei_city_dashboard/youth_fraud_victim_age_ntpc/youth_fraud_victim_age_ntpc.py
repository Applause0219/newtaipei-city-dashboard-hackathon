from airflow import DAG
from operators.common_pipeline import CommonDag


CATALOG_URL = "https://data.gov.tw/dataset/176657"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
)
ROC_YEAR = 114
QUARTER = 2
SOURCE_PERIOD = "114Q2"
RESIDUAL_LABEL = "年齡不詳（表內總計扣除已列年齡組）"

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

KNOWN_BAD_YEARS = {}
SCALE_ANOMALY_FACTOR = 3.0


def _text(value, field):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        raise ValueError(f"詐欺來源欄位 {field} 為空")
    return value


def _number(value, field, allow_negative=False):
    raw = _text(value, field)
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"詐欺來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number != number or (not allow_negative and number < 0):
        raise ValueError(f"詐欺來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _parse_age(label):
    import re
    import unicodedata

    raw = _text(label, "age_label")
    value = unicodedata.normalize("NFKC", raw).replace(" ", "")
    value = value.replace("－", "-").replace("–", "-").replace("～", "~")
    if "表內總計扣除已列年齡組" in value:
        return None, None, "unknown_age"
    if value in {"未滿18歲", "未滿18"}:
        return None, 17, "age_band"
    match = re.fullmatch(r"(\d{1,3})-(\d{1,3})歲?", value)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"詐欺來源年齡區間上下界顛倒：{raw!r}")
        return lower, upper, "age_band"
    match = re.fullmatch(r"(\d{1,3})歲以上", value)
    if match:
        return int(match.group(1)), None, "age_band"
    raise ValueError(f"無法解析詐欺來源年齡標籤：{raw!r}")


def _pdf_text(content):
    import os
    import shutil
    import subprocess
    import tempfile

    executable = shutil.which("pdftotext")
    if executable is None:
        raise RuntimeError(
            "詐欺 PDF 來源需要 pdftotext（Poppler）；Airflow 映像需額外安裝 "
            "poppler-utils，否則不能安全解析表格。"
        )
    with tempfile.TemporaryDirectory(prefix="youth-fraud-") as directory:
        input_path = os.path.join(directory, "source.pdf")
        output_path = os.path.join(directory, "source.txt")
        with open(input_path, "wb") as file:
            file.write(content)
        completed = subprocess.run(
            [executable, "-raw", input_path, output_path],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"pdftotext 解析詐欺 PDF 失敗：{stderr[:500]}")
        with open(output_path, "r", encoding="utf-8", errors="replace") as file:
            return file.read()


def _parse_age_table(text):
    import re

    expected_labels = [
        "未滿18歲", "18-23歲", "24-29歲", "30-39歲",
        "40-49歲", "50-59歲", "60-64歲", "65歲以上",
    ]
    token_labels = ["18-23", "24-29", "30-39", "40-49", "50-59", "60-64"]
    pages = text.split("\f")
    expected_tokens = {"18", *token_labels, "65"}
    # 表 2 是被害人存量：總數、總數百分比、男性、女性、性別比與人口率。
    # 表 3 也有同一組年齡標籤，但數值是較上季增減；只選表 2，避免把變動量
    # 誤當成被害人數。
    row_pattern = re.compile(
        r"^\s*(未滿\s*18\s*(?:歲)?|18-23\s*(?:歲)?|24-29\s*(?:歲)?|"
        r"30-39\s*(?:歲)?|40-49\s*(?:歲)?|50-59\s*(?:歲)?|"
        r"60-64\s*(?:歲)?|65\s*(?:歲)?以上)\s+"
        r"([\d,]+)\s+[\d.]+\s+([\d,]+)\s+([\d,]+)\s+"
        r"[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s*$"
    )
    total_pattern = re.compile(
        r"^\s*總\s*計\s+([\d,]+)\s+100\.00\s+([\d,]+)\s+([\d,]+)\s+"
        r"[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s*$"
    )
    page_results = []
    for page in pages:
        if not all(token in page for token in token_labels):
            continue
        lines = page.splitlines()
        parsed = {}
        parsed_indexes = {}
        for index, line in enumerate(lines):
            match = row_pattern.fullmatch(line)
            if not match:
                continue
            label = re.sub(r"\s+", "", match.group(1))
            token = {
                "未滿18歲": "18",
                "未滿18": "18",
                "65歲以上": "65",
                "65以上": "65",
            }.get(label, label.removesuffix("歲"))
            if token in parsed:
                raise ValueError(f"詐欺 PDF 年齡列重複：{token!r}")
            parsed[token] = {
                "total": _number(match.group(2), f"age {token} total"),
                "male": _number(match.group(3), f"age {token} male"),
                "female": _number(match.group(4), f"age {token} female"),
            }
            parsed_indexes[token] = index
        if set(parsed) != expected_tokens:
            continue
        first_age_index = min(parsed_indexes.values())
        total = None
        for line in lines[:first_age_index]:
            match = total_pattern.fullmatch(line)
            if match:
                total = {
                    "total": _number(match.group(1), "all_age total"),
                    "male": _number(match.group(2), "all_age male"),
                    "female": _number(match.group(3), "all_age female"),
                }
        if total is not None:
            page_results.append((parsed, total))
    if len(page_results) != 1:
        raise RuntimeError(
            f"詐欺 PDF 找不到唯一的被害人存量年齡表：候選頁數={len(page_results)}"
        )
    parsed, total = page_results[0]

    label_by_token = {
        "18": expected_labels[0],
        "18-23": expected_labels[1],
        "24-29": expected_labels[2],
        "30-39": expected_labels[3],
        "40-49": expected_labels[4],
        "50-59": expected_labels[5],
        "60-64": expected_labels[6],
        "65": expected_labels[7],
    }
    return total, [
        {"age_label": label_by_token[token], "values": parsed[token]}
        for token in ("18", *token_labels, "65")
    ]


def fetch_records():
    import re

    import requests

    with requests.Session() as session:
        page = session.get(CATALOG_URL, headers={"User-Agent": UA}, timeout=120)
        page.raise_for_status()
        resource_urls = []
        for url in re.findall(r"https://opdadm\.moi\.gov\.tw/[^\"<> ]+", page.text):
            if "download" in url and url not in resource_urls:
                resource_urls.append(url)
        if len(resource_urls) != 1:
            raise RuntimeError(
                f"詐欺資料集頁找不到唯一 PDF 資源：{len(resource_urls)}"
            )
        response = session.get(
            resource_urls[0], headers={"User-Agent": UA}, timeout=120
        )
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise RuntimeError("詐欺來源回應不是 PDF。")
        total, age_rows = _parse_age_table(_pdf_text(response.content))
    records = [
        {
            "row_type": "all_age",
            "roc_year": ROC_YEAR,
            "quarter": QUARTER,
            "period_label": SOURCE_PERIOD,
            "age_label": "總計",
            "values": total,
        }
    ]
    records.extend(
        {
            "row_type": "age",
            "roc_year": ROC_YEAR,
            "quarter": QUARTER,
            "period_label": SOURCE_PERIOD,
            "age_label": row["age_label"],
            "values": row["values"],
        }
        for row in age_rows
    )
    # 表內沒有列出不詳年齡；以官方總計扣除已列組別得到 residual，
    # 不猜任何人的年齡，只保留為 unknown_age 以完成對帳。
    residual_values = {
        gender: total[gender] - sum(row["values"][gender] for row in age_rows)
        for gender in ("total", "male", "female")
    }
    if any(value < -0.5 for value in residual_values.values()):
        raise ValueError(f"詐欺 PDF 年齡列加總超過總計：residual={residual_values}")
    if any(abs(value) > 0.5 for value in residual_values.values()):
        records.append(
            {
                "row_type": "age",
                "roc_year": ROC_YEAR,
                "quarter": QUARTER,
                "period_label": SOURCE_PERIOD,
                "age_label": RESIDUAL_LABEL,
                "values": residual_values,
                "derived_residual": True,
            }
        )
    return records


def _period_totals(records):
    totals = {}
    for record in records:
        if record.get("row_type") != "all_age":
            continue
        period = _text(record.get("period_label"), "period_label")
        totals[period] = _number(record["values"]["total"], "all_age total", allow_negative=True)
    return totals


def drop_scope_anomalies(records):
    totals = _period_totals(records)
    if not totals:
        raise RuntimeError("詐欺來源找不到總計列，無法做尺度檢核。")
    by_year = {}
    for period, value in totals.items():
        roc_year = int(period[:3])
        by_year[roc_year] = by_year.get(roc_year, 0.0) + value
    kept = {year: value for year, value in by_year.items() if year not in KNOWN_BAD_YEARS}
    if len(kept) >= 3:
        ordered = sorted(abs(value) for value in kept.values())
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        for year, value in sorted(kept.items()):
            if median and (abs(value) > median * SCALE_ANOMALY_FACTOR
                           or abs(value) * SCALE_ANOMALY_FACTOR < median):
                raise ValueError(
                    f"民國 {year} 年詐欺被害人變動量 {value:.0f}，與其餘年度中位數 "
                    f"{median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                    "請確認來源範圍或口徑後再更新 KNOWN_BAD_YEARS。"
                )
    else:
        print("fraud victim scale guard: fewer than 3 years; comparison not applicable")
    return [
        record for record in records
        if int(_text(record.get("period_label"), "period_label")[:3]) not in KNOWN_BAD_YEARS
    ]


def _period_dates(roc_year, quarter):
    if quarter != 2:
        raise ValueError(f"目前只允許詐欺來源第 2 季，收到 {quarter!r}")
    return f"{roc_year + 1911}-04-01", f"{roc_year + 1911}-06-30"


def _reconcile(records, data):
    headers = [record for record in records if record.get("row_type") == "all_age"]
    details = [record for record in records if record.get("row_type") == "age"]
    if len(headers) != 1:
        raise ValueError(f"詐欺來源總計列數不符：{len(headers)}")
    header = headers[0]
    for gender in ("total", "male", "female"):
        expected = _number(header["values"][gender], f"all_age {gender}", allow_negative=True)
        emitted_all = float(
            data.loc[
                (data["indicator_id"] == "fraud_victim_all_age_count")
                & (data["gender"] == gender), "value"
            ].sum()
        )
        emitted_detail = float(
            data.loc[
                (data["indicator_id"] == "fraud_victim_count")
                & (data["gender"] == gender), "value"
            ].sum()
        )
        if abs(emitted_all - expected) > 0.5 or abs(emitted_detail - expected) > 0.5:
            raise ValueError(
                f"詐欺 {gender} 對帳失敗：輸入 {expected}、"
                f"all_age 輸出 {emitted_all}、分齡輸出 {emitted_detail}"
            )
        print(
            f"{gender}: input total {int(expected)}, all_age output {int(emitted_all)}, "
            f"age output {int(emitted_detail)}, reconciled"
        )
    if len(data) != (len(details) + len(headers)) * 3:
        raise ValueError(
            f"詐欺輸出列數不一致：輸入來源列 {len(records)}、輸出 {len(data)}"
        )


def transform_records(records, data_time=None):
    """DAG 與 verify 共用的詐欺 PDF 年齡表轉換核心。"""
    import json
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    if not records:
        raise RuntimeError("詐欺來源轉換前沒有資料列。")
    rows = []
    for record in records:
        roc_year = int(_text(record.get("roc_year"), "roc_year"))
        start, end = _period_dates(roc_year, int(record.get("quarter")))
        row_type = _text(record.get("row_type"), "row_type")
        if row_type == "all_age":
            indicator_id = "fraud_victim_all_age_count"
            age_lower, age_upper, age_scope = None, None, "all_age"
            age_label = "總計"
        elif row_type == "age":
            indicator_id = "fraud_victim_count"
            age_label = _text(record.get("age_label"), "age_label")
            age_lower, age_upper, age_scope = _parse_age(age_label)
        else:
            raise ValueError(f"未知詐欺來源列類型：{row_type!r}")
        for gender in ("total", "male", "female"):
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "period_start": start,
                    "period_end": end,
                    "period_type": "quarter",
                    "age_lower": age_lower,
                    "age_upper": age_upper,
                    "age_band_raw": age_label,
                    "gender": gender,
                    "area_code": "TW",
                    "area_level": "country",
                    "breakdown": json.dumps(
                        {
                            "age_scope": age_scope,
                            "period_label": _text(record.get("period_label"), "period_label"),
                            "quarter": int(record.get("quarter")),
                            "row_type": row_type,
                            "derived_residual": bool(record.get("derived_residual", False)),
                            "source_note": "114Q2 victim count",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "value": _number(
                        record["values"][gender], f"{age_label} {gender}", allow_negative=True
                    ),
                    "unit": "人",
                    "value_type": "count",
                    "data_time": data_time,
                }
            )
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = pd.array(data["age_lower"], dtype="Int16")
    data["age_upper"] = pd.array(data["age_upper"], dtype="Int16")
    if data_time is None:
        data["data_time"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    _reconcile(records, data)
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos")
    dag_id = dag_infos.get("dag_id")
    data = transform_records(
        drop_scope_anomalies(fetch_records()),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")
    engine = create_engine(kwargs.get("ready_data_db_uri"))
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos.get("load_behavior"),
        default_table=dag_infos.get("ready_data_default_table"),
    )
    update_lasttime_in_data_to_dataset_info(engine, dag_id, data["data_time"].max())


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_fraud_victim_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
