import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from airflow import DAG
from operators.common_pipeline import CommonDag


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# 青年局統計年報沒有穩定 API。PDF 連結為青年局統計專區公開的官方附件，
# 每次執行重新下載，避免把探勘時的暫存檔當成資料來源。
PDF_SOURCES = (
    {
        "roc_year": 111,
        "name": "111 年度新北市政府青年局統計年報",
        "url": "https://www.youth.ntpc.gov.tw/youth/ch/app/data/doc?detailNo=1149610994704584704&module=youth0008&type=s",
    },
    {
        "roc_year": 112,
        "name": "112 年度新北市政府青年局統計年報",
        "url": "https://www.youth.ntpc.gov.tw/youth/ch/app/data/doc?detailNo=1252104139530309632&module=youth0008&type=s",
    },
    {
        "roc_year": 113,
        "name": "113 年度新北市政府青年局統計年報",
        "url": "https://www.youth.ntpc.gov.tw/youth/ch/app/data/doc?detailNo=1379997079249620992&module=youth0008&type=s",
    },
    {
        "roc_year": 114,
        "name": "114 年度新北市政府青年局統計年報",
        "url": "https://www.youth.ntpc.gov.tw/youth/ch/app/data/doc?detailNo=1505809476916088832&module=youth0008&type=s",
    },
)

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

# `pdftotext -layout` 可能把年份或一列的數字放到相鄰列；本 DAG 實測後採用
# 使用 Poppler 的 `-layout` 模式，再依活動／身分語意標籤找列，不硬切欄位位置。
TABLE_SPECS = (
    {
        "number": 1,
        "title": "新北市推動青年國際交流與擴展國際視野業務成果統計",
        "indicator_id": "youth_bureau_international_participation_count",
        "session_indicator_id": None,
        "labels": ("總計", "青年國際事務人才培力", "青年國際講座", "國際性活動"),
        "required_labels": ("總計", "青年國際事務人才培力", "青年國際講座", "國際性活動"),
        "has_sessions": False,
    },
    {
        "number": 2,
        "title": "新北市青年職涯發展活動參與概況",
        "indicator_id": "youth_bureau_career_participation_count",
        "session_indicator_id": "youth_bureau_career_activity_session_count",
        "labels": ("總計", "國高中職學生", "大專校院學生", "其他"),
        "required_labels": ("總計", "國高中職學生", "大專校院學生", "其他"),
        "has_sessions": True,
    },
    {
        "number": 3,
        "title": "新北市青年創新創業活動參與概況",
        "indicator_id": "youth_bureau_entrepreneurship_participation_count",
        "session_indicator_id": "youth_bureau_entrepreneurship_activity_session_count",
        "labels": ("總計", "學生", "新創者", "其他"),
        "required_labels": ("總計", "學生", "新創者"),
        "has_sessions": True,
    },
)

NUMBER_RE = re.compile(r"(?<![\d,])\d[\d,]*(?:\.\d+)?")


def _compact(text):
    """去掉中英文空白，讓「總　計」與「總計」可用同一個語意標籤比對。"""
    return re.sub(r"\s+", "", str(text))


def _parse_number(token):
    """解析含千分位逗號的 PDF 數字；遇到非數字不靜默當成 0。"""
    return float(str(token).replace(",", ""))


def _pdf_to_text(pdf_bytes, source_name):
    """用 Poppler pdftotext 的版面模式；缺少工具或文字層時直接失敗。"""
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError(
            "找不到 pdftotext。Airflow 映像需提供 Poppler pdftotext，"
            "才能解析青年局 PDF。"
        )

    with tempfile.TemporaryDirectory(prefix="youth_bureau_pdf_") as temp_dir:
        pdf_path = Path(temp_dir) / "source.pdf"
        pdf_path.write_bytes(pdf_bytes)
        result = subprocess.run(
            [executable, "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"{source_name} 的 pdftotext 失敗：{error}"
            )
        text = result.stdout.decode("utf-8")
        if not text.strip():
            raise RuntimeError(f"{source_name} 沒有文字層，需先做 OCR；本 DAG 不靜默略過。")
        return text


def _fetch_pdf(source):
    import requests

    response = requests.get(
        source["url"],
        headers={"User-Agent": UA},
        timeout=120,
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        content_type = response.headers.get("Content-Type", "")
        raise RuntimeError(
            f"{source['name']} 回應不是 PDF：Content-Type={content_type!r}"
        )
    print(
        f"PDF {source['roc_year']} HTTP {response.status_code} "
        f"bytes {len(response.content)}"
    )
    return response.content


def _table_block(text, table_spec):
    section_start = text.find("參、統計報表")
    if section_start < 0:
        raise ValueError("PDF 找不到「參、統計報表」章節")
    section = text[section_start:]
    title_pattern = re.compile(
        r"表\s*" + str(table_spec["number"]) + r"\s+"
        + re.escape(table_spec["title"])
    )
    match = title_pattern.search(section)
    if not match:
        raise ValueError(
            f"找不到表 {table_spec['number']}：{table_spec['title']}"
        )
    tail = section[match.end():]
    source_match = re.search(r"資料來源\s*[:：]", tail)
    if not source_match:
        raise ValueError(f"表 {table_spec['number']} 找不到資料來源結尾")
    block = tail[:source_match.start()]
    if not re.search(r"\b(?:111|112|113|114)\b", block):
        raise ValueError(f"表 {table_spec['number']} 內找不到民國年度列")
    return block


def _row_numbers(block, label, roc_year, has_sessions, labels):
    """找一個語意列，回傳 participant 五欄與可選的活動場次。"""
    lines = block.splitlines()

    def parse_candidate(candidate):
        tokens = NUMBER_RE.findall(candidate)
        if len(tokens) < 5:
            return None
        token_sets = [tokens]
        # 某些 PDF 把民國年印在前一列，且該列的「-」場次標記又在
        # 下一列；合併兩列後，年度會落在五個資料欄中間，需移除。
        if "-" in candidate and len(tokens) >= 6:
            for token_index, token in enumerate(tokens):
                if token.replace(",", "") == str(roc_year) and token_index > 0:
                    token_sets.insert(0, tokens[:token_index] + tokens[token_index + 1 :])
                    break

        for candidate_tokens in token_sets:
            values = [_parse_number(token) for token in candidate_tokens[-5:]]
            session = None
            if has_sessions and len(candidate_tokens) >= 6:
                possible_session = candidate_tokens[-6]
                # layout 偶爾把年度放在身分別前；若該列有「-」代表
                # 活動場次無數據，不能把年度誤當成場次。
                before_label = candidate[: candidate.find(label)] if label in candidate else ""
                is_year_prefix = (
                    float(_parse_number(possible_session)).is_integer()
                    and int(_parse_number(possible_session)) == roc_year
                    and (str(roc_year) in before_label or "-" in candidate)
                )
                if not is_year_prefix:
                    session = _parse_number(possible_session)
            return values, session
        return None

    for index, line in enumerate(lines):
        if label not in _compact(line):
            continue

        # layout 模式在不同 PDF 會把同一列拆到前後數列。先試同列、
        # 緊鄰列，再試反向相鄰兩列；後者處理 111 年「新創者」列的
        # 人數與百分比分散在標籤前兩列的情形，避免誤抓下一個分類。
        candidate_groups = [
            (0,),
            (-1,),
            (1,),
            (-1, -2),
            (1, 2),
            (-2, -1),
            (2, 1),
            (-1, 1),
            (1, -1),
            (-2,),
            (2,),
            (-3,),
            (3,),
            (-4,),
            (4,),
        ]
        seen = set()
        for offsets in candidate_groups:
            candidate_indices = [index + offset for offset in offsets]
            if any(i < 0 or i >= len(lines) for i in candidate_indices):
                continue
            candidate = " ".join(lines[i] for i in candidate_indices)
            if candidate in seen:
                continue
            seen.add(candidate)
            if any(
                other_label != label and other_label in _compact(candidate)
                for other_label in labels
            ):
                continue
            parsed = parse_candidate(candidate)
            if parsed is not None:
                return parsed

    raise ValueError(f"表格找不到可解析的「{label}」列（民國 {roc_year}）")


def _parse_table(text, source, table_spec):
    block = _table_block(text, table_spec)
    rows = {}
    for label in table_spec["labels"]:
        try:
            rows[label] = _row_numbers(
                block,
                label,
                source["roc_year"],
                table_spec["has_sessions"],
                table_spec["labels"],
            )
        except ValueError:
            if label in table_spec["required_labels"]:
                raise

    missing = [label for label in table_spec["required_labels"] if label not in rows]
    if missing:
        raise ValueError(
            f"民國 {source['roc_year']} 表 {table_spec['number']} 缺少列：{missing}"
        )

    total = rows["總計"][0]
    category_labels = [
        label for label in table_spec["labels"] if label != "總計" and label in rows
    ]
    if len(category_labels) < 2:
        raise ValueError(
            f"民國 {source['roc_year']} 表 {table_spec['number']} 分類列不足"
        )

    # 五個 participant 欄位是總計、男、男百分比、女、女百分比。
    for label, values in rows.items():
        participant_total, male, male_pct, female, female_pct = values[0]
        if int(participant_total) != int(male) + int(female):
            raise ValueError(
                f"民國 {source['roc_year']} 表 {table_spec['number']}「{label}」"
                f"性別對帳失敗：{participant_total} != {male} + {female}"
            )
        if participant_total and abs(male_pct - male / participant_total * 100) > 0.2:
            raise ValueError(
                f"民國 {source['roc_year']} 表 {table_spec['number']}「{label}」男比例不符"
            )
        if participant_total and abs(female_pct - female / participant_total * 100) > 0.2:
            raise ValueError(
                f"民國 {source['roc_year']} 表 {table_spec['number']}「{label}」女比例不符"
            )

    for position, column_name in ((0, "總計"), (1, "男性"), (3, "女性")):
        emitted = sum(rows[label][0][position] for label in category_labels)
        if int(emitted) != int(total[position]):
            raise ValueError(
                f"民國 {source['roc_year']} 表 {table_spec['number']} {column_name}"
                f"對帳失敗：來源總列 {total[position]}，分類加總 {emitted}"
            )

    total_session = rows["總計"][1]
    category_sessions = [rows[label][1] for label in category_labels]
    if total_session is not None and all(value is not None for value in category_sessions):
        emitted_sessions = sum(category_sessions)
        if int(emitted_sessions) != int(total_session):
            raise ValueError(
                f"民國 {source['roc_year']} 表 {table_spec['number']} 活動場次對帳失敗："
                f"來源總列 {total_session}，分類加總 {emitted_sessions}"
            )

    return {
        "rows": rows,
        "category_labels": category_labels,
        "source_total": int(total[0]),
        "category_total": int(sum(rows[label][0][0] for label in category_labels)),
        "source_total_male": int(total[1]),
        "source_total_female": int(total[3]),
        "session_count": total_session,
    }


def _fact_row(source, table_spec, activity_label, gender, indicator_id, value, unit, data_time, metric):
    year_ad = source["roc_year"] + 1911
    return {
        "indicator_id": indicator_id,
        "period_start": f"{year_ad}-01-01",
        "period_end": f"{year_ad}-12-31",
        "period_type": "year",
        # 年報活動表沒有參與者年齡欄；「青年」是業務名稱，不能當年齡。
        "age_lower": None,
        "age_upper": None,
        "age_band_raw": None,
        "gender": gender,
        "area_code": "65000",
        "area_level": "city",
        "breakdown": json.dumps(
            {
                "source_report_year": source["roc_year"],
                "source_pdf": source["url"],
                "table_number": table_spec["number"],
                "activity_or_identity": activity_label,
                "metric": metric,
                "age_coverage": "unavailable",
            },
            ensure_ascii=False,
        ),
        "value": float(value),
        "unit": unit,
        "value_type": "count",
        "data_time": data_time,
    }


def build_facts(fetch_pdf=None, data_time=None):
    """下載、轉換並回傳 (fact DataFrame, reconciliation audit)。"""
    import pandas as pd

    if fetch_pdf is None:
        fetch_pdf = _fetch_pdf
    if data_time is None:
        from utils.get_time import get_tpe_now_time_str

        data_time = get_tpe_now_time_str(is_with_tz=True)

    rows = []
    audit = []
    for source in PDF_SOURCES:
        pdf_bytes = fetch_pdf(source)
        text = _pdf_to_text(pdf_bytes, source["name"])
        for table_spec in TABLE_SPECS:
            parsed = _parse_table(text, source, table_spec)
            if parsed["source_total"] != parsed["category_total"]:
                raise ValueError(
                    f"民國 {source['roc_year']} 表 {table_spec['number']} "
                    "輸入總數與輸出分類總數不一致"
                )

            for activity_label in ["總計", *parsed["category_labels"]]:
                values, session = parsed["rows"][activity_label]
                for gender, position in (("total", 0), ("male", 1), ("female", 3)):
                    rows.append(
                        _fact_row(
                            source,
                            table_spec,
                            activity_label,
                            gender,
                            table_spec["indicator_id"],
                            values[position],
                            "人次",
                            data_time,
                            "participants",
                        )
                    )
                if table_spec["session_indicator_id"] and session is not None:
                    rows.append(
                        _fact_row(
                            source,
                            table_spec,
                            activity_label,
                            "total",
                            table_spec["session_indicator_id"],
                            session,
                            "場",
                            data_time,
                            "activity_sessions",
                        )
                    )

            audit.append(
                {
                    "roc_year": source["roc_year"],
                    "table_number": table_spec["number"],
                    "source_total": parsed["source_total"],
                    "category_total": parsed["category_total"],
                    "reconciled": parsed["source_total"] == parsed["category_total"],
                    "category_rows": len(parsed["category_labels"]),
                }
            )

    if not rows:
        raise RuntimeError("青年局 PDF 沒有產出任何事實列")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(f"事實表欄位不符合契約：{list(data.columns)}")
    if data[CONTRACT_COLUMNS].isna().sum().to_dict()["data_time"]:
        raise ValueError("data_time 不得為空")
    return data, audit


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    ready_data_db_uri = kwargs.get("ready_data_db_uri")
    dag_infos = kwargs.get("dag_infos")
    dag_id = dag_infos.get("dag_id")
    load_behavior = dag_infos.get("load_behavior")
    default_table = dag_infos.get("ready_data_default_table")

    data, audit = build_facts()
    for item in audit:
        print(
            f"ROC {item['roc_year']} table {item['table_number']} "
            f"input_total={item['source_total']} output_category_total={item['category_total']} "
            f"reconciled={item['reconciled']}"
        )
    print(f"ready_data shape =========== {data.shape}")
    print(f"ready_data columns =========== {list(data.columns)}")

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
    dag_folder="youth_bureau_activity_ntpc",
)
dag.create_dag(etl_func=_transfer)
