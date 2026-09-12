import io
import json
import re
import statistics
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from xml.etree import ElementTree

from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36"
)
SOURCE_URL = "https://web.cec.gov.tw/api/file/57480d24-9b5f-42c6-8752-fff879dea939.zip"
SOURCE_PAGE = "https://web.cec.gov.tw/central/article/58559"
ELECTION_NAME = "第16任總統副總統及第11屆立法委員選舉"
ELECTION_DATE = "2024-01-13"
NTPC_CITY_ID = "65"
NTPC_AREA_CODE = "65000"
KNOWN_BAD_YEARS = {}
SCALE_ANOMALY_FACTOR = 3.0
CEC_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
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


def _normalize_header(value):
    return re.sub(r"[\s_\-]+", "", str(value or "")).casefold()


def _resolve_column(headers, aliases, role):
    normalized = {
        _normalize_header(header): column
        for column, header in headers.items()
    }
    for alias in aliases:
        key = normalized.get(_normalize_header(alias))
        if key is not None:
            return key
    raise ValueError(
        f"CEC source role {role!r} is missing; headers={sorted(headers)!r}"
    )


def _cell_value(cell, shared_strings):
    value = cell.find(f"{{{CEC_NS}}}v")
    raw = "" if value is None else value.text or ""
    cell_type = cell.attrib.get("t")
    if cell_type == "s" and raw:
        return shared_strings[int(raw)]
    if cell_type == "inlineStr":
        return "".join(
            text.text or ""
            for text in cell.iter(f"{{{CEC_NS}}}t")
        )
    return raw


def _column_name(cell_ref):
    match = re.match(r"[A-Z]+", cell_ref or "")
    if not match:
        raise ValueError(f"Invalid XLSX cell reference: {cell_ref!r}")
    return match.group(0)


def _iter_xlsx_rows(xlsx_bytes):
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as workbook:
        if "xl/sharedStrings.xml" not in workbook.namelist():
            raise ValueError("CEC XLSX has no sharedStrings.xml")
        shared_root = ElementTree.fromstring(
            workbook.read("xl/sharedStrings.xml")
        )
        shared_strings = [
            "".join(text.text or "" for text in item.iter(f"{{{CEC_NS}}}t"))
            for item in shared_root
        ]
        worksheet_names = [
            name
            for name in workbook.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        ]
        if len(worksheet_names) != 1:
            raise ValueError(
                f"Expected one CEC worksheet, got {worksheet_names!r}"
            )
        with workbook.open(worksheet_names[0]) as worksheet:
            for _, element in ElementTree.iterparse(
                worksheet, events=("end",)
            ):
                if element.tag != f"{{{CEC_NS}}}row":
                    continue
                yield {
                    _column_name(cell.attrib.get("r")): _cell_value(
                        cell, shared_strings
                    )
                    for cell in element.findall(f"{{{CEC_NS}}}c")
                }
                element.clear()


def _parse_age(raw, source_row_id):
    text = str(raw or "").strip()
    if not re.fullmatch(r"\d+", text):
        raise ValueError(
            f"CEC row {source_row_id}: unparseable single age {raw!r}"
        )
    age = int(text)
    if age < 0 or age > 130:
        raise ValueError(f"CEC row {source_row_id}: invalid age {age}")
    return age


def _check_scale_guard(period_totals):
    kept = {
        int(year): float(total)
        for year, total in period_totals.items()
        if int(year) not in KNOWN_BAD_YEARS
    }
    if KNOWN_BAD_YEARS:
        print(f"CEC known bad years excluded from scale guard: {KNOWN_BAD_YEARS}")
    applicable = False
    for year, value in sorted(kept.items()):
        peers = [
            peer
            for peer_year, peer in kept.items()
            if peer_year != year and peer > 0
        ]
        if len(peers) < 3 or value <= 0:
            continue
        applicable = True
        peer_median = statistics.median(peers)
        if peer_median <= 0:
            continue
        if value > SCALE_ANOMALY_FACTOR * peer_median or (
            value * SCALE_ANOMALY_FACTOR < peer_median
        ):
            raise ValueError(
                f"CEC scale anomaly in {year}: {value} vs peer median "
                f"{peer_median} (factor={SCALE_ANOMALY_FACTOR})"
            )
    if not applicable:
        print(
            "CEC scale guard not applicable: fewer than four comparable "
            "election periods are available."
        )
    else:
        print("CEC scale guard passed.")


def fetch_source_rows():
    import requests

    response = requests.get(SOURCE_URL, headers={"User-Agent": UA}, timeout=900)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as outer:
        xlsx_members = [
            name
            for name in outer.namelist()
            if name.lower().endswith(".xlsx")
            and "sps" not in name.lower()
            and "spss" not in name.lower()
        ]
        if not xlsx_members:
            raise ValueError(
                f"Expected one raw CEC XLSX, found {xlsx_members!r}"
            )
        # The official ZIP also contains a small XLSX codebook.  The raw
        # observation sheet is the largest XLSX by uncompressed member size;
        # selecting it by size avoids
        # relying on unstable, encoded filenames.
        raw_member = max(
            xlsx_members,
            key=lambda name: outer.getinfo(name).file_size,
        )
        xlsx_bytes = outer.read(raw_member)
    rows = iter(_iter_xlsx_rows(xlsx_bytes))
    try:
        headers = next(rows)
    except StopIteration as exc:
        raise ValueError("CEC XLSX is empty") from exc
    headers = {key: value for key, value in headers.items() if value}
    required = {
        "city": _resolve_column(headers, ["cityid"], "cityid"),
        "gender": _resolve_column(headers, ["SEX", "sex"], "SEX"),
        "vote": _resolve_column(
            headers, ["AVOTE", "presidential_vote"], "AVOTE"
        ),
        "age": _resolve_column(headers, ["age", "AGE"], "age"),
        "source_id": _resolve_column(headers, ["ID", "id"], "ID"),
    }
    output = []
    for row in rows:
        if not row:
            continue
        if row.get(required["city"], "").strip() != NTPC_CITY_ID:
            continue
        output.append(
            {
                "source_id": row.get(required["source_id"], ""),
                "age": row.get(required["age"], ""),
                "gender": row.get(required["gender"], ""),
                "vote": row.get(required["vote"], ""),
            }
        )
    if not output:
        raise ValueError("CEC source has no New Taipei City records")
    return output


def transform_records(source_rows, data_time=None):
    effective_data_time = data_time or datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")
    stats = defaultdict(
        lambda: {
            "source_record_count": 0,
            "valid_vote_count": 0,
            "voted_count": 0,
            "not_voted_count": 0,
            "missing_vote_count": 0,
        }
    )
    input_by_gender = defaultdict(int)
    for row in source_rows:
        source_id = row["source_id"] or "<unknown>"
        age = _parse_age(row["age"], source_id)
        sex = str(row["gender"] or "").strip()
        gender = {"1": "male", "2": "female"}.get(sex)
        if gender is None:
            raise ValueError(
                f"CEC row {source_id}: unknown SEX value {row['gender']!r}"
            )
        vote = str(row["vote"] or "").strip()
        if vote not in {"0", "1", "#NULL!"}:
            raise ValueError(
                f"CEC row {source_id}: unknown AVOTE value {row['vote']!r}"
            )
        for group_gender in ("total", gender):
            group = stats[(group_gender, age)]
            group["source_record_count"] += 1
            input_by_gender[group_gender] += 1
            if vote == "#NULL!":
                group["missing_vote_count"] += 1
            else:
                group["valid_vote_count"] += 1
                group["voted_count"] += int(vote == "1")
                group["not_voted_count"] += int(vote == "0")

    rows = []
    for (gender, age), group in sorted(stats.items()):
        if group["valid_vote_count"] <= 0:
            raise ValueError(
                f"CEC {gender}/{age} has no valid AVOTE denominator; "
                "a rate cannot be emitted."
            )
        if (
            group["voted_count"] + group["not_voted_count"]
            != group["valid_vote_count"]
        ):
            raise ValueError(f"CEC vote reconciliation failed for {gender}/{age}")
        rate = group["voted_count"] / group["valid_vote_count"] * 100
        rows.append(
            {
                "indicator_id": "youth_election_turnout_age_ntpc",
                "period_start": ELECTION_DATE,
                "period_end": ELECTION_DATE,
                "period_type": "election",
                "age_lower": age,
                "age_upper": age,
                "age_band_raw": f"{age}歲",
                "gender": gender,
                "area_code": NTPC_AREA_CODE,
                "area_level": "city",
                "breakdown": json.dumps(
                    {
                        "area_name": "新北市",
                        "election_name": ELECTION_NAME,
                        "vote_variable": "AVOTE",
                        "source_record_count": group["source_record_count"],
                        "valid_vote_count": group["valid_vote_count"],
                        "voted_count": group["voted_count"],
                        "not_voted_count": group["not_voted_count"],
                        "missing_vote_count": group["missing_vote_count"],
                        "rate_basis": "unweighted known-response sample rate",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "value": round(rate, 8),
                "unit": "%",
                "value_type": "rate",
                "data_time": effective_data_time,
            }
        )

    for gender in ("total", "male", "female"):
        emitted = sum(
            json.loads(row["breakdown"])["source_record_count"]
            for row in rows
            if row["gender"] == gender
        )
        if emitted != input_by_gender[gender]:
            raise ValueError(
                f"CEC source/output reconciliation failed for {gender}: "
                f"input={input_by_gender[gender]}, output={emitted}"
            )
    _check_scale_guard({2024: len(source_rows)})
    data = _as_dataframe(rows)
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(
            f"CEC contract columns mismatch: {list(data.columns)!r}"
        )
    return data


def _as_dataframe(rows):
    import pandas as pd

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    return data


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos") or {}
    data = transform_records(
        fetch_source_rows(),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"ready_data shape =========== {data.shape}")
    print(
        "CEC New Taipei known-response sample voters =========== "
        f"{data['value'].count()} rate rows"
    )
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
    dag_folder="youth_election_turnout_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
