import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata

from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
SOURCE_PAGE_URL = "https://www.mol.gov.tw/1607/71771/73009/73034/84578/lpsimplelist"
SOURCE_URL = "https://statdb.mol.gov.tw/html/sex/yearnew/8050.pdf"
SCALE_ANOMALY_FACTOR = 3.0
KNOWN_BAD_YEARS = set()

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

LEVELS = ("all", "甲級", "乙級", "丙級", "單一級")
AGE_LABEL_RE = re.compile(r"^\d{1,2}-\d{1,2}歲$|^\d{1,2}歲(?:以上)?$")
YEAR_LABEL_RE = re.compile(r"^\d{2,3}年$")


def _normalise(text):
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def _parse_number(text, label):
    value = _normalise(text).replace(",", "")
    if value in {"-", "－", "—", "–"}:
        return 0
    if value == "":
        raise ValueError(f"技能檢定 PDF 的 {label} 數值為空白")
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"技能檢定 PDF 的 {label} 數值無法解析：{text!r}")
    return int(value)


def _looks_like_number(text):
    value = _normalise(text).replace(",", "")
    return value in {"", "-", "－", "—", "–"} or bool(
        re.fullmatch(r"\d+", value)
    )


def parse_age_label(label):
    raw = _normalise(label)
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})歲", raw)
    if match:
        lower, upper = map(int, match.groups())
        if lower > upper:
            raise ValueError(f"技能檢定 PDF 年齡區間顛倒：{raw!r}")
        return lower, upper
    match = re.fullmatch(r"(\d{1,2})歲以上", raw)
    if match:
        return int(match.group(1)), None
    match = re.fullmatch(r"(\d{1,2})歲", raw)
    if match:
        age = int(match.group(1))
        return age, age
    raise ValueError(f"技能檢定 PDF 年齡標籤無法解析：{raw!r}")


def parse_pdf_tsv(tsv_text):
    """Parse the one-page official table into annual and latest age rows."""
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    required = {"level", "page_num", "top", "left", "text"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError(
            "pdftotext -tsv 欄位不符預期："
            f"{reader.fieldnames!r}; 請確認 Airflow 映像含支援 -tsv 的 poppler-utils"
        )

    grouped = {}
    for row in reader:
        if row.get("level") != "5":
            continue
        text = row.get("text", "").strip()
        if not text:
            continue
        key = (int(row["page_num"]), round(float(row["top"]), 1))
        grouped.setdefault(key, []).append((float(row["left"]), text))

    annual = []
    age_rows = []
    for _, words in sorted(grouped.items()):
        ordered = [text for _, text in sorted(words)]
        if not ordered:
            continue
        label = _normalise(ordered[0])
        if YEAR_LABEL_RE.fullmatch(label):
            values = ordered[1:]
            if len(values) != 11:
                raise ValueError(
                    f"技能檢定 PDF 年度列 {label!r} 應有 11 個數值，實際 {len(values)}"
                )
            annual.append(
                {
                    "roc_year": int(label[:-1]),
                    "label": label,
                    "values": [_parse_number(value, label) for value in values],
                }
            )
        elif AGE_LABEL_RE.fullmatch(label):
            values = ordered[1:]
            if len(values) != 11:
                raise ValueError(
                    f"技能檢定 PDF 年齡列 {label!r} 應有 11 個數值，實際 {len(values)}"
                )
            age_rows.append(
                {
                    "age_band_raw": label,
                    "values": [_parse_number(value, label) for value in values],
                }
            )
        elif len(ordered) == 12 and all(
            _looks_like_number(value) for value in ordered[1:]
        ):
            raise ValueError(
                f"技能檢定 PDF 發現無法辨識的資料列標籤：{label!r}"
            )

    if not annual:
        raise ValueError("技能檢定 PDF 找不到年度總表")
    if not age_rows:
        raise ValueError("技能檢定 PDF 找不到年齡別表")
    if len({row["roc_year"] for row in annual}) != len(annual):
        raise ValueError("技能檢定 PDF 年度列重複")
    if len({row["age_band_raw"] for row in age_rows}) != len(age_rows):
        raise ValueError("技能檢定 PDF 年齡列重複")

    return {
        "annual": sorted(annual, key=lambda row: row["roc_year"]),
        "age_rows": age_rows,
    }


def _pdf_to_tsv(pdf_bytes):
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError(
            "找不到 pdftotext；技能檢定 DAG 需要 Airflow 映像安裝 poppler-utils"
        )
    file_descriptor, pdf_path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(pdf_bytes)
        result = subprocess.run(
            [executable, "-tsv", pdf_path, "-"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        try:
            os.unlink(pdf_path)
        except FileNotFoundError:
            pass
    if result.returncode != 0:
        raise RuntimeError(
            "pdftotext -tsv 解析技能檢定 PDF 失敗："
            f"{result.stderr.strip()}"
        )
    return result.stdout


def fetch_records():
    import requests

    response = requests.get(
        SOURCE_URL,
        headers={"User-Agent": UA},
        timeout=120,
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(
            f"技能檢定來源不是 PDF：content-type={response.headers.get('content-type')!r}"
        )
    return parse_pdf_tsv(_pdf_to_tsv(response.content))


def drop_scope_anomalies(records):
    annual = [
        row for row in records["annual"]
        if row["roc_year"] not in KNOWN_BAD_YEARS
    ]
    if not annual:
        raise ValueError("技能檢定所有年度都被排除")
    totals = {row["roc_year"]: row["values"][0] for row in annual}
    if len(totals) >= 3:
        for year, value in totals.items():
            peers = [peer for peer_year, peer in totals.items() if peer_year != year]
            median = sorted(peers)[len(peers) // 2]
            if value and median and (
                value / median > SCALE_ANOMALY_FACTOR
                or median / value > SCALE_ANOMALY_FACTOR
            ):
                raise ValueError(
                    f"技能檢定年度規模異常 ROC {year}: {value} vs peer median {median}"
                )
    if KNOWN_BAD_YEARS:
        print(f"技能檢定排除已知異常年度：{sorted(KNOWN_BAD_YEARS)}")
    return {**records, "annual": annual}


def _source_cells(values):
    if len(values) != 11:
        raise ValueError(f"技能檢定資料列應有 11 個值，實際 {len(values)}")
    cells = {
        "all": {"total": values[0], "male": values[1], "female": values[2]},
        "甲級": {"male": values[3], "female": values[4]},
        "乙級": {"male": values[5], "female": values[6]},
        "丙級": {"male": values[7], "female": values[8]},
        "單一級": {"male": values[9], "female": values[10]},
    }
    if cells["all"]["total"] != cells["all"]["male"] + cells["all"]["female"]:
        raise ValueError("技能檢定總計男女人數對不上")
    for level in LEVELS[1:]:
        cells[level]["total"] = cells[level]["male"] + cells[level]["female"]
    return cells


def _assert_source_reconciliation(records):
    for kind in ("annual", "age_rows"):
        for record in records[kind]:
            _source_cells(record["values"])

    latest_year = max(row["roc_year"] for row in records["annual"])
    annual = next(
        row for row in records["annual"] if row["roc_year"] == latest_year
    )
    for index, expected in enumerate(annual["values"]):
        actual = sum(row["values"][index] for row in records["age_rows"])
        if actual != expected:
            raise ValueError(
                f"技能檢定年齡表與 ROC {latest_year} 年度表不一致，"
                f"欄位 {index}: age_sum={actual}, annual={expected}"
            )


def _append_cells(
    rows,
    *,
    roc_year,
    data_time,
    values,
    age_lower,
    age_upper,
    age_band_raw,
    row_kind,
):
    cells = _source_cells(values)
    ad_year = roc_year + 1911
    youth_status = "apportioned" if age_band_raw is not None else "unavailable"
    youth_reason = (
        "15-19 歲與 35-39 歲跨越 18 與 35 邊界；只保留原始年齡組，"
        "不在 ETL 固化分攤比例"
        if age_band_raw is not None
        else "年度總表沒有年齡欄位，不能把年度總數推成青年數"
    )
    base = {
        "period_start": f"{ad_year}-01-01",
        "period_end": f"{ad_year}-12-31",
        "period_type": "year",
        "age_lower": age_lower,
        "age_upper": age_upper,
        "age_band_raw": age_band_raw,
        "area_code": "TW",
        "area_level": "country",
        "unit": "張",
        "value_type": "count",
        "data_time": data_time,
    }
    for level in LEVELS:
        for gender in ("total", "male", "female"):
            breakdown = {
                "certification_level": level,
                "source_row_kind": row_kind,
                "source_roc_year": roc_year,
                "youth_18_35_status": youth_status,
                "youth_18_35_reason": youth_reason,
            }
            if level != "all" and gender == "total":
                breakdown["derived_from"] = ["male", "female"]
            rows.append(
                {
                    **base,
                    "indicator_id": "skill_certification_issued_count",
                    "gender": gender,
                    "breakdown": json.dumps(
                        breakdown,
                        ensure_ascii=False,
                    ),
                    "value": cells[level][gender],
                }
            )


def transform_records(records, data_time):
    import pandas as pd

    records = drop_scope_anomalies(records)
    _assert_source_reconciliation(records)
    rows = []
    for record in records["annual"]:
        _append_cells(
            rows,
            roc_year=record["roc_year"],
            data_time=data_time,
            values=record["values"],
            age_lower=None,
            age_upper=None,
            age_band_raw=None,
            row_kind="annual_total",
        )

    latest_year = max(row["roc_year"] for row in records["annual"])
    for record in records["age_rows"]:
        lower, upper = parse_age_label(record["age_band_raw"])
        _append_cells(
            rows,
            roc_year=latest_year,
            data_time=data_time,
            values=record["values"],
            age_lower=lower,
            age_upper=upper,
            age_band_raw=record["age_band_raw"],
            row_kind="latest_age_band",
        )

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = pd.array(data["age_lower"], dtype="Int64")
    data["age_upper"] = pd.array(data["age_upper"], dtype="Int64")
    if data.empty:
        raise RuntimeError("技能檢定轉換後沒有資料")
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos") or {}
    data = transform_records(fetch_records(), get_tpe_now_time_str(is_with_tz=True))
    engine = create_engine(kwargs.get("ready_data_db_uri"))
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos.get("load_behavior"),
        default_table=dag_infos.get("ready_data_default_table"),
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_infos.get("dag_id"), data["data_time"].max()
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_skill_certification_age_tw",
)
dag.create_dag(etl_func=_transfer)
