from airflow import DAG  # noqa: F401
from operators.common_pipeline import CommonDag

BASE = "https://oas.bas.ntpc.gov.tw/DgbasWeb/Page/"
NAV = BASE + "StatFile2.aspx?Mode=3&CateNo=121&ParentNo=null12102"
SHOW = (
    "https://oas.bas.ntpc.gov.tw/"
    "%e7%b6%b2%e9%a0%81%e8%b3%87%e6%96%99%e6%9f%a5%e8%a9%a2/ShowQuery.aspx"
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

REPORT_ID = "11842"
NTPC_QUERY_AREA_CODE = "010000065000"
NTPC_CITY_CODE = "65000"
EXPECTED_COMPLEX_COUNT = 1
EXPECTED_METRIC_COUNT = 52
SCALE_ANOMALY_FACTOR = 3.0
KNOWN_BAD_YEARS = {
    1999: "死亡人數_女=59,806，大於死亡總數 14,609，來源性別細項自相矛盾",
    2000: "死亡人數_女=67,010，大於死亡總數 15,065，來源性別細項自相矛盾",
    2018: "遷入人數總數比 14 個分項少 1，來源分項加總不一致",
}
RATE_TOLERANCE = 0.2

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

PERSON_METRICS = {
    "遷入人數",
    "遷出人數",
    "出生人數",
    "死亡人數",
    "遷入人數_自國外",
    "遷入人數_新北市",
    "遷入人數_台北市",
    "遷入人數_桃園市",
    "遷入人數_台中市",
    "遷入人數_台南市",
    "遷入人數_高雄市",
    "遷入人數_臺灣省",
    "遷入人數_福建省",
    "遷入人數_其他省市",
    "遷入人數_自本省他縣市",
    "遷入人數_自本市他區",
    "初設戶籍",
    "遷入人數_其他",
    "遷出人數_往國外",
    "遷出人數_新北市",
    "遷出人數_台北市",
    "遷出人數_桃園市",
    "遷出人數_台中市",
    "遷出人數_台南市",
    "遷出人數_高雄市",
    "遷出人數_臺灣省",
    "遷出人數_福建省",
    "遷出人數_其他省市",
    "遷出人數_自本省他縣市",
    "遷出人數_往本市他區",
    "廢止戶籍",
    "遷出人數_其他",
    "同一區內住址變更人數_遷入",
    "同一區內住址變更人數_遷出",
}
PERSON_GENDER_METRICS = {
    "遷入人數_男",
    "遷入人數_女",
    "遷出人數_男",
    "遷出人數_女",
    "出生人數_男",
    "出生人數_女",
    "死亡人數_男",
    "死亡人數_女",
}
PAIR_METRICS = {"結婚對數", "離婚/終止結婚對數"}
RATE_METRICS = {
    "粗出生率",
    "粗死亡率",
    "自然增加率",
    "遷入率",
    "遷出率",
    "社會增加率",
    "結婚率",
    "離婚率",
}
EXPECTED_METRICS = (
    PERSON_METRICS | PERSON_GENDER_METRICS | PAIR_METRICS | RATE_METRICS
)
SCOPE_METRICS = ("遷入人數", "遷出人數", "出生人數", "死亡人數")
GENDER_SPLIT_METRICS = ("出生人數", "死亡人數")
INCOMING_PARTS = (
    "遷入人數_自國外",
    "遷入人數_新北市",
    "遷入人數_台北市",
    "遷入人數_桃園市",
    "遷入人數_台中市",
    "遷入人數_台南市",
    "遷入人數_高雄市",
    "遷入人數_臺灣省",
    "遷入人數_福建省",
    "遷入人數_其他省市",
    "遷入人數_自本省他縣市",
    "遷入人數_自本市他區",
    "初設戶籍",
    "遷入人數_其他",
)
OUTGOING_PARTS = (
    "遷出人數_往國外",
    "遷出人數_新北市",
    "遷出人數_台北市",
    "遷出人數_桃園市",
    "遷出人數_台中市",
    "遷出人數_台南市",
    "遷出人數_高雄市",
    "遷出人數_臺灣省",
    "遷出人數_福建省",
    "遷出人數_其他省市",
    "遷出人數_自本省他縣市",
    "遷出人數_往本市他區",
    "廢止戶籍",
    "遷出人數_其他",
)


def _hidden_fields(page):
    import html
    import re

    fields = {}
    patterns = (
        r'<input[^>]+type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"',
        r'<input[^>]+name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"',
    )
    for pattern in patterns:
        for name, value in re.findall(pattern, page, re.IGNORECASE):
            fields.setdefault(name, html.unescape(value))
    return fields


def _select(page, fragment):
    import html
    import re

    match = re.search(
        r'<select[^>]*name="([^" ]*'
        + re.escape(fragment)
        + r'[^" ]*)"[^>]*>(.*?)</select>',
        page,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, []
    options = []
    for option in re.finditer(
        r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>',
        match.group(2),
        re.IGNORECASE | re.DOTALL,
    ):
        label = re.sub(r"<[^>]+>", "", option.group(2))
        options.append((option.group(1), html.unescape(label).strip()))
    return match.group(1), options


def _parse_metric_label(label):
    import unicodedata

    value = unicodedata.normalize("NFKC", str(label))
    value = "".join(value.split())
    gender = "total"
    base = value
    if value.endswith("_男"):
        base, gender = value[:-2], "male"
    elif value.endswith("_女"):
        base, gender = value[:-2], "female"

    if value in RATE_METRICS:
        indicator_id, unit, value_type = (
            "household_dynamics_rate",
            "‰",
            "rate",
        )
    elif base in PAIR_METRICS:
        indicator_id, unit, value_type = (
            "household_dynamics_marriage_pair_count",
            "對",
            "count",
        )
    elif value in PERSON_METRICS or base in PERSON_METRICS:
        indicator_id, unit, value_type = (
            "household_dynamics_person_count",
            "人",
            "count",
        )
    else:
        raise ValueError(f"未知的 OAS 11842 指標標籤：{label!r}")
    return {
        "metric_label": value,
        "gender": gender,
        "indicator_id": indicator_id,
        "unit": unit,
        "value_type": value_type,
    }


def _number(value, field):
    import math
    import unicodedata

    raw = unicodedata.normalize("NFKC", str(value)).strip()
    raw = raw.replace(",", "").replace("−", "-")
    if raw in {"-", "—"}:
        return 0.0
    if not raw or raw.lower() in {"nan", "none", "null", "...", "…"}:
        raise ValueError(f"OAS 11842 欄位 {field} 是空值或佔位符：{value!r}")
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"OAS 11842 欄位 {field} 不是數字：{value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"OAS 11842 欄位 {field} 不是有限數字：{value!r}")
    return number


def _table_cells(page):
    import html
    import re

    tables = re.findall(r"<table.*?</table>", page, re.IGNORECASE | re.DOTALL)
    if not tables:
        raise RuntimeError("OAS 11842 回應沒有資料表")
    table = max(
        tables,
        key=lambda item: len(re.findall(r"<t[dh]", item, re.IGNORECASE)),
    )
    cells = []
    for cell in re.findall(
        r"<t[dh][^>]*>(.*?)</t[dh]>", table, re.IGNORECASE | re.DOTALL
    ):
        text = html.unescape(re.sub(r"<[^>]+>", "", cell))
        text = " ".join(text.replace(" ", " ").split())
        if text:
            cells.append(text)
    return cells


def _result_values(page, expected, date_value):
    import re

    cells = _table_cells(page)
    if len(cells) != expected:
        raise ValueError(
            f"OAS 11842 {date_value} 回傳 {len(cells)} 格，"
            f"但維度乘積是 {expected} 格"
        )
    first = re.search(r"(-|−|[\d,]+(?:\.\d+)?)\s*$", cells[0])
    if not first:
        raise ValueError(f"OAS 11842 {date_value} 首格無法取出數字：{cells[0]!r}")
    raw_values = [first.group(1)] + cells[1:]
    return [
        _number(value, f"{date_value}[{index}]")
        for index, value in enumerate(raw_values)
    ]


def _fetch_records(session):
    import re

    navigation = session.get(NAV, timeout=180)
    navigation.raise_for_status()
    form = _hidden_fields(navigation.text)
    form.update({"__EVENTTARGET": "lbtGUID", "__EVENTARGUMENT": REPORT_ID})
    report = session.post(
        NAV, data=form, headers={"Referer": navigation.url}, timeout=300
    )
    report.raise_for_status()
    report_page = report.text
    print(f"OAS {REPORT_ID} report bytes = {len(report.content):,}")

    complexes = []
    for number in range(1, 6):
        name, options = _select(report_page, f"lisComplex{number}")
        if name and options:
            complexes.append((name, options))
    if len(complexes) != EXPECTED_COMPLEX_COUNT:
        raise RuntimeError(
            f"OAS 11842 複分類數量 {len(complexes)} != "
            f"預期 {EXPECTED_COMPLEX_COUNT}"
        )
    metric_name, metric_options = complexes[0]
    if len(metric_options) != EXPECTED_METRIC_COUNT:
        raise RuntimeError(
            f"OAS 11842 指標數量 {len(metric_options)} != "
            f"預期 {EXPECTED_METRIC_COUNT}"
        )
    parsed_labels = [_parse_metric_label(label) for _, label in metric_options]
    if {item["metric_label"] for item in parsed_labels} != EXPECTED_METRICS:
        raise RuntimeError("OAS 11842 指標清單已變更，停止解析")

    date_name, date_options = _select(report_page, "lisDate")
    if not date_options:
        raise RuntimeError("OAS 11842 找不到日期選項")
    print(
        "OAS 11842 dimensions = "
        f"{[(name.rsplit('$', 1)[-1], len(options)) for name, options in complexes]}, "
        f"dates={len(date_options)}, product={len(metric_options)}"
    )

    fields = _hidden_fields(report_page)
    ax_x = "[Measures];" + metric_options[0][0][:5]
    ax_code = ";".join(code for code, _ in metric_options)
    ax_code += ";" + NTPC_QUERY_AREA_CODE

    def query(date_value):
        payload = dict(fields)
        payload.update(
            {
                "__EVENTTARGET": (
                    "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1$goon"
                ),
                "__EVENTARGUMENT": "",
                "axCycle": fields.get(
                    "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1"
                    "$axCycle_Server",
                    "Y年",
                ),
                "axY": "[Date];[Place]",
                "axDate": date_value,
                "axEffect": fields.get(
                    "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1"
                    "$axEffect_Server",
                    REPORT_ID,
                ),
                "axFunction": "統計數值",
                "axMode": fields.get(
                    "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1"
                    "$axMode_Server",
                    "3",
                ),
                "axSubjectNo": fields.get(
                    "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1"
                    "$axSubjectNo_Server",
                    REPORT_ID,
                ),
                "axX": ax_x,
                "axCode": ax_code,
                "ShowQueryReturnUrl": navigation.url,
                "UrlOrgNo": fields.get("UrlOrgNo", ""),
                "FindString": fields.get("FindString", ""),
                "Complex": fields.get("Complex", ""),
                "Statistic": fields.get("Statistic", ""),
                "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1"
                "$ddlFunctiontype": "統計數值",
                date_name: date_value,
            }
        )
        encoded_payload = list(payload.items())
        encoded_payload.extend(
            (metric_name, value) for value, _ in metric_options
        )
        response = session.post(
            SHOW,
            data=encoded_payload,
            headers={"Referer": navigation.url},
            timeout=300,
        )
        response.raise_for_status()
        if "ShowQuery.aspx" not in response.url:
            raise RuntimeError(f"OAS 11842 查詢被重新導向：{response.url}")
        return response.text

    records = []
    for date_value, date_label in date_options:
        year_match = re.match(r"^(\d{4})/", date_value)
        if not year_match:
            raise ValueError(f"OAS 11842 無法解析日期：{date_value!r}")
        values = _result_values(
            query(date_value), len(metric_options), date_value
        )
        for (code, _), parsed, value in zip(
            metric_options, parsed_labels, values
        ):
            record = dict(parsed)
            record.update(
                {
                    "year": int(year_match.group(1)),
                    "date_value": date_value,
                    "date_label": date_label,
                    "source_code": code,
                    "value": value,
                }
            )
            records.append(record)
        print(f"OAS {date_value} ({date_label}) values = {len(values)}")
    if not records:
        raise RuntimeError("OAS 11842 沒有取得任何資料")
    return records


def drop_scope_anomalies(records):
    import statistics

    totals = {metric: {} for metric in SCOPE_METRICS}
    for record in records:
        metric = record["metric_label"]
        if metric in totals and record["gender"] == "total":
            if record["year"] in totals[metric]:
                raise ValueError(f"OAS 11842 {metric} 有重複年度")
            totals[metric][record["year"]] = record["value"]

    for metric, values in totals.items():
        active = {
            year: value
            for year, value in values.items()
            if year not in KNOWN_BAD_YEARS
        }
        if len(active) < 3:
            raise RuntimeError(f"OAS 11842 {metric} 年度不足，無法做尺度檢查")
        for year, value in sorted(active.items()):
            peers = [
                other
                for other_year, other in active.items()
                if other_year != year
            ]
            median = statistics.median(peers)
            if median == 0 and value != 0:
                raise ValueError(f"OAS 11842 {metric} {year} 相對中位數為異常")
            if median and (
                value > median * SCALE_ANOMALY_FACTOR
                or value * SCALE_ANOMALY_FACTOR < median
            ):
                raise ValueError(
                    f"OAS 11842 {metric} {year}={value:.0f}，"
                    f"同項其他年度中位數 {median:.0f} 超過 "
                    f"{SCALE_ANOMALY_FACTOR} 倍尺度防護"
                )
    print(f"OAS 11842 scale guard: {len(records)} cells passed")
    if KNOWN_BAD_YEARS:
        print(f"OAS 11842 known bad years: {sorted(KNOWN_BAD_YEARS)}")
    return [record for record in records if record["year"] not in KNOWN_BAD_YEARS]


def transform_records(records, data_time):
    import json

    rows = []
    for record in records:
        rows.append(
            {
                "indicator_id": record["indicator_id"],
                "period_start": f"{record['year']}-01-01",
                "period_end": f"{record['year']}-12-31",
                "period_type": "year",
                "age_lower": None,
                "age_upper": None,
                "age_band_raw": None,
                "gender": record["gender"],
                "area_code": NTPC_CITY_CODE,
                "area_level": "city",
                "breakdown": json.dumps(
                    {
                        "age_scope": "unavailable",
                        "source_code": record["source_code"],
                        "source_metric": record["metric_label"],
                        "source_platform": "oas.bas.ntpc.gov.tw",
                        "source_report": REPORT_ID,
                        "youth_18_35_status": "unavailable",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "value": record["value"],
                "unit": record["unit"],
                "value_type": record["value_type"],
                "data_time": data_time,
            }
        )
    return rows


def _reconcile(records, rows):
    import json

    expected = {
        (record["year"], record["source_code"]): record["value"]
        for record in records
    }
    if len(expected) != len(records):
        raise ValueError("OAS 11842 輸入 source cell 不唯一")
    emitted = {}
    for row in rows:
        details = json.loads(row["breakdown"])
        key = (
            int(str(row["period_start"])[:4]),
            details.get("source_code"),
        )
        if key in emitted:
            raise ValueError(f"OAS 11842 輸出 source cell 重複：{key}")
        emitted[key] = float(row["value"])
    if set(expected) != set(emitted):
        raise ValueError("OAS 11842 輸入輸出 source cell 集合不一致")
    for key, value in expected.items():
        if abs(value - emitted[key]) > 0.5:
            raise ValueError(
                f"OAS 11842 source cell 對帳失敗：{key} {value} != {emitted[key]}"
            )
    if abs(sum(expected.values()) - sum(emitted.values())) > 0.5:
        raise ValueError("OAS 11842 輸入總數與輸出總數不一致")

    lookup = {
        (record["year"], record["metric_label"]): record["value"]
        for record in records
    }
    years = sorted({record["year"] for record in records})
    for year in years:
        for base in GENDER_SPLIT_METRICS:
            total = lookup[(year, base)]
            male = lookup[(year, base + "_男")]
            female = lookup[(year, base + "_女")]
            if abs(total - male - female) > 0.5:
                raise ValueError(f"OAS 11842 {year} {base} 性別合計不一致")

        for total_label, parts in (
            ("遷入人數", INCOMING_PARTS),
            ("遷出人數", OUTGOING_PARTS),
        ):
            parts_total = sum(lookup[(year, part)] for part in parts)
            if abs(lookup[(year, total_label)] - parts_total) > 0.5:
                raise ValueError(
                    f"OAS 11842 {year} {total_label} 分項合計不一致"
                )

        if abs(
            lookup[(year, "自然增加率")]
            - (lookup[(year, "粗出生率")] - lookup[(year, "粗死亡率")])
        ) > RATE_TOLERANCE:
            raise ValueError(f"OAS 11842 {year} 自然增加率公式不一致")
        if abs(
            lookup[(year, "社會增加率")]
            - (lookup[(year, "遷入率")] - lookup[(year, "遷出率")])
        ) > RATE_TOLERANCE:
            raise ValueError(f"OAS 11842 {year} 社會增加率公式不一致")

    print(
        f"OAS 11842 reconciliation: source cells={len(expected)}, "
        f"years={len(years)}, emitted_rows={len(rows)}, "
        f"input_total={sum(expected.values()):.2f}, "
        f"output_total={sum(emitted.values()):.2f}"
    )


def _transfer(**kwargs):
    import pandas as pd
    import requests
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs.get("dag_infos") or {}
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    records = drop_scope_anomalies(_fetch_records(session))
    data_time = get_tpe_now_time_str(is_with_tz=True)
    rows = transform_records(records, data_time)
    _reconcile(records, rows)

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    nullable = {"age_lower", "age_upper", "age_band_raw"}
    required = [column for column in CONTRACT_COLUMNS if column not in nullable]
    if list(data.columns) != CONTRACT_COLUMNS or data[required].isna().any().any():
        raise ValueError("OAS 11842 輸出不符合 15 欄事實表契約")
    print(f"OAS 11842 ready_data shape = {data.shape}")

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
    dag_folder="youth_oas_household_dynamics_ntpc",
)
dag.create_dag(etl_func=_transfer)
