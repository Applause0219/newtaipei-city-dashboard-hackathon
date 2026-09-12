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

REPORT_ID = "11844"
NTPC_QUERY_AREA_CODE = "010000065000"
NTPC_CITY_CODE = "65000"
EXPECTED_COMPLEX_COUNT = 2
EXPECTED_GENDER_COUNT = 3
EXPECTED_METRIC_COUNT = 10
SCALE_ANOMALY_FACTOR = 3.0
KNOWN_BAD_YEARS = {}
FORMULA_TOLERANCE = 0.2
REFERENCE_2025_TOTAL = 4_044_831.0

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

GENDER_LABELS = {"計": "total", "男": "male", "女": "female"}
METRIC_SPECS = {
    "0_14歲人口數": {
        "age_lower": 0,
        "age_upper": 14,
        "age_scope": "age_band",
        "indicator_id": "resident_age_structure_population_count",
        "unit": "人",
        "value_type": "count",
    },
    "0_14歲比率": {
        "age_lower": 0,
        "age_upper": 14,
        "age_scope": "age_band",
        "indicator_id": "resident_age_structure_percentage",
        "unit": "%",
        "value_type": "rate",
    },
    "15_64歲人口數": {
        "age_lower": 15,
        "age_upper": 64,
        "age_scope": "age_band",
        "indicator_id": "resident_age_structure_population_count",
        "unit": "人",
        "value_type": "count",
    },
    "15_64歲比率": {
        "age_lower": 15,
        "age_upper": 64,
        "age_scope": "age_band",
        "indicator_id": "resident_age_structure_percentage",
        "unit": "%",
        "value_type": "rate",
    },
    "65歲以上人口數": {
        "age_lower": 65,
        "age_upper": None,
        "age_scope": "age_band",
        "indicator_id": "resident_age_structure_population_count",
        "unit": "人",
        "value_type": "count",
    },
    "65歲以上比率": {
        "age_lower": 65,
        "age_upper": None,
        "age_scope": "age_band",
        "indicator_id": "resident_age_structure_percentage",
        "unit": "%",
        "value_type": "rate",
    },
    "扶老比": {
        "age_lower": None,
        "age_upper": None,
        "age_scope": "derived",
        "indicator_id": "resident_age_structure_dependency_ratio",
        "unit": "人/百人",
        "value_type": "rate",
    },
    "扶幼比": {
        "age_lower": None,
        "age_upper": None,
        "age_scope": "derived",
        "indicator_id": "resident_age_structure_dependency_ratio",
        "unit": "人/百人",
        "value_type": "rate",
    },
    "扶養比": {
        "age_lower": None,
        "age_upper": None,
        "age_scope": "derived",
        "indicator_id": "resident_age_structure_dependency_ratio",
        "unit": "人/百人",
        "value_type": "rate",
    },
    "老化指數": {
        "age_lower": None,
        "age_upper": None,
        "age_scope": "derived",
        "indicator_id": "resident_age_structure_aging_index",
        "unit": "人/百人",
        "value_type": "index",
    },
}
COUNT_LABELS = ("0_14歲人口數", "15_64歲人口數", "65歲以上人口數")
PERCENT_LABELS = ("0_14歲比率", "15_64歲比率", "65歲以上比率")
DERIVED_LABELS = ("扶老比", "扶幼比", "扶養比", "老化指數")


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


def _normalise_label(label):
    import unicodedata

    return "".join(unicodedata.normalize("NFKC", str(label)).split())


def _find_roles(complexes):
    gender_roles = []
    metric_roles = []
    for index, (_, options) in enumerate(complexes):
        labels = {_normalise_label(label) for _, label in options}
        if labels == set(GENDER_LABELS) and len(options) == EXPECTED_GENDER_COUNT:
            gender_roles.append(index)
        if labels == set(METRIC_SPECS) and len(options) == EXPECTED_METRIC_COUNT:
            metric_roles.append(index)
    if len(gender_roles) != 1 or len(metric_roles) != 1:
        raise RuntimeError(
            "OAS 11844 無法辨識性別/指標維度："
            f"gender={gender_roles}, metric={metric_roles}"
        )
    if gender_roles[0] == metric_roles[0]:
        raise RuntimeError("OAS 11844 性別與指標誤指向同一維度")
    return {"gender": gender_roles[0], "metric": metric_roles[0]}


def _number(value, field):
    import math
    import unicodedata

    raw = unicodedata.normalize("NFKC", str(value)).strip()
    raw = raw.replace(",", "").replace("−", "-")
    if raw in {"-", "—"}:
        return 0.0
    if not raw or raw.lower() in {"nan", "none", "null", "...", "…"}:
        raise ValueError(f"OAS 11844 欄位 {field} 是空值或佔位符：{value!r}")
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"OAS 11844 欄位 {field} 不是數字：{value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"OAS 11844 欄位 {field} 是非法非負數：{value!r}")
    return number


def _table_cells(page):
    import html
    import re

    tables = re.findall(r"<table.*?</table>", page, re.IGNORECASE | re.DOTALL)
    if not tables:
        raise RuntimeError("OAS 11844 回應沒有資料表")
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
            f"OAS 11844 {date_value} 回傳 {len(cells)} 格，"
            f"但維度乘積是 {expected} 格"
        )
    first = re.search(r"(-|−|[\d,]+(?:\.\d+)?)\s*$", cells[0])
    if not first:
        raise ValueError(f"OAS 11844 {date_value} 首格無法取出數字：{cells[0]!r}")
    raw_values = [first.group(1)] + cells[1:]
    return [
        _number(value, f"{date_value}[{index}]")
        for index, value in enumerate(raw_values)
    ]


def _fetch_records(session):
    import itertools
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
            f"OAS 11844 複分類數量 {len(complexes)} != "
            f"預期 {EXPECTED_COMPLEX_COUNT}"
        )
    roles = _find_roles(complexes)
    _, gender_options = complexes[roles["gender"]]
    _, metric_options = complexes[roles["metric"]]

    date_name, date_options = _select(report_page, "lisDate")
    if not date_options:
        raise RuntimeError("OAS 11844 找不到日期選項")
    expected = len(gender_options) * len(metric_options)
    print(
        "OAS 11844 dimensions = "
        f"{[(name.rsplit('$', 1)[-1], len(options)) for name, options in complexes]}, "
        f"dates={len(date_options)}, product={expected}, roles={roles}"
    )

    fields = _hidden_fields(report_page)
    ax_x = "[Measures]" + "".join(
        ";" + options[0][0][:5] for _, options in complexes
    )
    ax_code = "".join(
        ";".join(code for code, _ in options) + ";"
        for _, options in complexes
    )
    ax_code += NTPC_QUERY_AREA_CODE

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
        for name, options in complexes:
            encoded_payload.extend((name, value) for value, _ in options)
        response = session.post(
            SHOW,
            data=encoded_payload,
            headers={"Referer": navigation.url},
            timeout=300,
        )
        response.raise_for_status()
        if "ShowQuery.aspx" not in response.url:
            raise RuntimeError(f"OAS 11844 查詢被重新導向：{response.url}")
        return response.text

    combinations = list(
        itertools.product(*[options for _, options in complexes])
    )
    records = []
    for date_value, date_label in date_options:
        year_match = re.match(r"^(\d{4})/", date_value)
        if not year_match:
            raise ValueError(f"OAS 11844 無法解析日期：{date_value!r}")
        values = _result_values(
            query(date_value), len(combinations), date_value
        )
        for combination, value in zip(combinations, values):
            source_codes = {
                role: combination[index][0] for role, index in roles.items()
            }
            source_labels = {
                role: _normalise_label(combination[index][1])
                for role, index in roles.items()
            }
            metric_label = source_labels["metric"]
            spec = METRIC_SPECS.get(metric_label)
            if spec is None:
                raise ValueError(f"OAS 11844 未知指標標籤：{metric_label!r}")
            records.append(
                {
                    "year": int(year_match.group(1)),
                    "date_value": date_value,
                    "date_label": date_label,
                    "source_codes": source_codes,
                    "source_labels": source_labels,
                    "gender": GENDER_LABELS[source_labels["gender"]],
                    "metric_label": metric_label,
                    "age_lower": spec["age_lower"],
                    "age_upper": spec["age_upper"],
                    "age_scope": spec["age_scope"],
                    "indicator_id": spec["indicator_id"],
                    "unit": spec["unit"],
                    "value_type": spec["value_type"],
                    "value": value,
                }
            )
        print(f"OAS {date_value} ({date_label}) values = {len(values)}")
    if not records:
        raise RuntimeError("OAS 11844 沒有取得任何資料")
    return records


def drop_scope_anomalies(records):
    import statistics

    totals = {}
    for record in records:
        if (
            record["gender"] == "total"
            and record["metric_label"] in COUNT_LABELS
        ):
            totals.setdefault(record["year"], {})[record["metric_label"]] = (
                record["value"]
            )
    if len(totals) < 3:
        raise RuntimeError("OAS 11844 年度不足，無法做尺度檢查")
    scope_totals = {}
    for year, values in totals.items():
        if set(values) != set(COUNT_LABELS):
            raise RuntimeError(f"OAS 11844 {year} 缺少年齡結構人口數")
        scope_totals[year] = sum(values.values())

    active = {
        year: value
        for year, value in scope_totals.items()
        if year not in KNOWN_BAD_YEARS
    }
    for year, value in sorted(active.items()):
        peers = [
            other
            for other_year, other in active.items()
            if other_year != year
        ]
        median = statistics.median(peers)
        if median == 0 and value != 0:
            raise ValueError(f"OAS 11844 {year} 相對中位數為異常")
        if median and (
            value > median * SCALE_ANOMALY_FACTOR
            or value * SCALE_ANOMALY_FACTOR < median
        ):
            raise ValueError(
                f"OAS 11844 {year} scope total={value:.0f}，"
                f"同項其他年度中位數 {median:.0f} 超過 "
                f"{SCALE_ANOMALY_FACTOR} 倍尺度防護"
            )
    print(f"OAS 11844 scale guard: {len(active)} years passed")
    if KNOWN_BAD_YEARS:
        print(f"OAS 11844 known bad years: {sorted(KNOWN_BAD_YEARS)}")
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
                "age_lower": record["age_lower"],
                "age_upper": record["age_upper"],
                "age_band_raw": (
                    record["metric_label"]
                    if record["age_scope"] == "age_band"
                    else None
                ),
                "gender": record["gender"],
                "area_code": NTPC_CITY_CODE,
                "area_level": "city",
                "breakdown": json.dumps(
                    {
                        "age_scope": record["age_scope"],
                        "note": (
                            "ETL 保留 0-14、15-64、65+ 原始組別；"
                            "18-35 coverage 由查詢階段決定"
                        ),
                        "source_codes": record["source_codes"],
                        "source_metric": record["metric_label"],
                        "source_platform": "oas.bas.ntpc.gov.tw",
                        "source_report": REPORT_ID,
                        "youth_18_35_status": "apportioned",
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

    def input_key(record):
        return (
            record["year"],
            record["source_codes"]["gender"],
            record["source_codes"]["metric"],
        )

    expected = {input_key(record): record["value"] for record in records}
    if len(expected) != len(records):
        raise ValueError("OAS 11844 輸入 source cell 不唯一")
    emitted = {}
    for row in rows:
        details = json.loads(row["breakdown"])
        codes = details.get("source_codes") or {}
        key = (
            int(str(row["period_start"])[:4]),
            codes.get("gender"),
            codes.get("metric"),
        )
        if key in emitted:
            raise ValueError(f"OAS 11844 輸出 source cell 重複：{key}")
        emitted[key] = float(row["value"])
    if set(expected) != set(emitted):
        raise ValueError("OAS 11844 輸入輸出 source cell 集合不一致")
    for key, value in expected.items():
        if abs(value - emitted[key]) > 0.001:
            raise ValueError(
                f"OAS 11844 source cell 對帳失敗：{key} {value} != {emitted[key]}"
            )
    if abs(sum(expected.values()) - sum(emitted.values())) > 0.001:
        raise ValueError("OAS 11844 輸入總數與輸出總數不一致")

    lookup = {
        (record["year"], record["gender"], record["metric_label"]): record[
            "value"
        ]
        for record in records
    }
    years = sorted({record["year"] for record in records})
    for year in years:
        for gender in GENDER_LABELS.values():
            percentages = sum(
                lookup[(year, gender, label)] for label in PERCENT_LABELS
            )
            if abs(percentages - 100.0) > FORMULA_TOLERANCE:
                raise ValueError(
                    f"OAS 11844 {year} {gender} 百分比合計不等於 100："
                    f"{percentages}"
                )
            young = lookup[(year, gender, "0_14歲人口數")]
            working = lookup[(year, gender, "15_64歲人口數")]
            elderly = lookup[(year, gender, "65歲以上人口數")]
            if working <= 0 or young <= 0:
                raise ValueError(
                    f"OAS 11844 {year} {gender} 年齡結構分母為零"
                )
            formulas = {
                "扶老比": elderly / working * 100,
                "扶幼比": young / working * 100,
                "扶養比": (young + elderly) / working * 100,
                "老化指數": elderly / young * 100,
            }
            for label, calculated in formulas.items():
                if abs(lookup[(year, gender, label)] - calculated) > (
                    FORMULA_TOLERANCE
                ):
                    raise ValueError(
                        f"OAS 11844 {year} {gender} {label} 公式不一致"
                    )

    if 2025 in years:
        total = sum(
            lookup[(2025, "total", label)] for label in COUNT_LABELS
        )
        if abs(total - REFERENCE_2025_TOTAL) > 0.5:
            raise ValueError(
                f"OAS 11844 2025 總人口 {total:.0f} != "
                f"11843 交叉來源 {REFERENCE_2025_TOTAL:.0f}"
            )
        print(
            f"OAS 11844 cross-source 2025 total = {total:.0f}, "
            f"11843 = {REFERENCE_2025_TOTAL:.0f}"
        )
    print(
        f"OAS 11844 reconciliation: source cells={len(expected)}, "
        f"formula groups={len(years) * len(GENDER_LABELS)}, "
        f"emitted_rows={len(rows)}"
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
        raise ValueError("OAS 11844 輸出不符合 15 欄事實表契約")
    print(f"OAS 11844 ready_data shape = {data.shape}")

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
    dag_folder="youth_oas_age_structure_ntpc",
)
dag.create_dag(etl_func=_transfer)
