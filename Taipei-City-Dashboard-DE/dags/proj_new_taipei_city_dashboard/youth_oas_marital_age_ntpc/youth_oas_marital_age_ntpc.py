from operators.common_pipeline import CommonDag

BASE = "https://oas.bas.ntpc.gov.tw/DgbasWeb/Page/"
NAV = BASE + "StatFile2.aspx?Mode=3&CateNo=121&ParentNo=null12102"
SHOW = (
    "https://oas.bas.ntpc.gov.tw/"
    "%e7%b6%b2%e9%A0%81%e8%B3%87%e6%96%99%e6%9f%A5%e8%A9%A2/ShowQuery.aspx"
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

REPORT_ID = "11848"
NTPC_QUERY_AREA_CODE = "010000065000"
NTPC_CITY_CODE = "65000"
EXPECTED_COMPLEX_COUNT = 2
SCALE_ANOMALY_FACTOR = 3.0
KNOWN_BAD_YEARS = set()

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

GENDER_LABELS = {"計": "total", "男": "male", "女": "female"}
MARITAL_STATUS_LABELS = {
    "總計": "all",
    "未婚": "unmarried",
    "有偶": "married",
    "離婚": "divorced",
    "喪偶": "widowed",
}
MARITAL_STATUS_PREFIXES = (
    ("離婚/終止結婚", "divorced"),
    ("總計", "all"),
    ("未婚", "unmarried"),
    ("有偶", "married"),
    ("喪偶", "widowed"),
)
ROLE_ORDER = ("age", "marital")
PRIMARY_STATUSES = ("all", "unmarried", "married", "divorced", "widowed")


def _hidden_fields(html):
    import re

    fields = {}
    patterns = (
        r'<input[^>]+type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"',
        r'<input[^>]+name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"',
    )
    for pattern in patterns:
        for name, value in re.findall(pattern, html, re.IGNORECASE):
            fields.setdefault(name, value)
    return fields


def _select(html, fragment):
    import html as html_module
    import re

    match = re.search(
        r'<select[^>]*name="([^"]*' + re.escape(fragment)
        + r'[^"]*)"[^>]*>(.*?)</select>',
        html, re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, []
    options = []
    for option in re.finditer(
        r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>',
        match.group(2), re.IGNORECASE | re.DOTALL,
    ):
        label = re.sub(r"<[^>]+>", "", option.group(2))
        options.append((option.group(1), html_module.unescape(label).strip()))
    return match.group(1), options


def _number(value, field):
    import math

    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "...", "…"}:
        raise ValueError(f"OAS 11848 欄位 {field} 為空或佔位符：{value!r}")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"OAS 11848 欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or not math.isfinite(number):
        raise ValueError(f"OAS 11848 欄位 {field} 出現無效數值：{value!r}")
    return number


def _parse_age_label(label):
    """Return (lower, upper, scope); unknown labels fail loudly."""
    import re

    value = str(label).strip()
    if value == "總計":
        return None, None, "summary"
    match = re.fullmatch(r"未滿(\d{1,3})歲", value)
    if match:
        upper = int(match.group(1)) - 1
        if upper < 0:
            raise ValueError(f"OAS 11848 年齡上界無效：{label!r}")
        return None, upper, "age_band"
    match = re.fullmatch(r"(\d{1,3})[_~～-](\d{1,3})歲", value)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"OAS 11848 年齡區間上下界顛倒：{label!r}")
        return lower, upper, "age_band"
    match = re.fullmatch(r"(\d{1,3})歲以上", value)
    if match:
        return int(match.group(1)), None, "age_band"
    raise ValueError(f"無法解析 OAS 11848 年齡標籤：{label!r}")


def _parse_marital_label(label):
    """Parse the source's composite status/gender/same-sex label."""
    value = "".join(str(label).strip().split())
    same_sex = False
    for marker in ("_相同性別", "相同性別", "_同性別", "同性別"):
        if value.endswith(marker):
            same_sex = True
            value = value[: -len(marker)].rstrip("_")
            break
    status_prefix, status = next(
        ((prefix, status) for prefix, status in MARITAL_STATUS_PREFIXES if value.startswith(prefix)),
        (None, None),
    )
    if status_prefix is None:
        raise ValueError(f"無法解析 OAS 11848 婚姻狀況標籤：{label!r}")
    suffix = value[len(status_prefix):].lstrip("_")
    gender = {"": "total", "計": "total", "男": "male", "女": "female"}.get(suffix)
    if gender is None or (same_sex and gender == "total"):
        raise ValueError(f"OAS 11848 婚姻狀況性別後綴未知：{label!r}")
    return status, gender, same_sex


def _result_json(html):
    import json
    import re

    match = re.search(r"values\s*=\s*(\{.*?\})\s*;\s*//", html, re.DOTALL)
    if not match:
        raise RuntimeError("OAS 11848 回應找不到 values JSON。")
    try:
        result = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError("OAS 11848 values JSON 無法解析。") from exc
    if not isinstance(result, dict) or not isinstance(result.get("Values"), list):
        raise TypeError("OAS 11848 values JSON 缺少 Values 陣列。")
    return result


def _find_roles(complexes):
    candidates = {role: [] for role in ROLE_ORDER}
    for index, (_, options) in enumerate(complexes):
        labels = [label for _, label in options]
        try:
            [_parse_age_label(label) for label in labels]
        except ValueError:
            pass
        else:
            candidates["age"].append(index)
        try:
            [_parse_marital_label(label) for label in labels]
        except ValueError:
            pass
        else:
            candidates["marital"].append(index)
    roles = {}
    for role in ROLE_ORDER:
        matches = candidates[role]
        if len(matches) != 1:
            raise RuntimeError(
                f"OAS 11848 無法唯一辨識 {role} 維度：{matches}；"
                f"實際維度={[len(options) for _, options in complexes]}"
            )
        roles[role] = matches[0]
    if len(set(roles.values())) != len(ROLE_ORDER):
        raise RuntimeError(f"OAS 11848 維度角色重疊：{roles}")
    return roles


def _fetch_records():
    import re

    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    navigation = session.get(NAV, timeout=180)
    navigation.raise_for_status()
    form = _hidden_fields(navigation.text)
    form["__EVENTTARGET"] = "lbtGUID"
    form["__EVENTARGUMENT"] = REPORT_ID
    report = session.post(NAV, data=form, timeout=300)
    report.raise_for_status()
    report_html = report.text
    print(f"OAS {REPORT_ID} report bytes = {len(report.content):,}")

    complexes = []
    for number in range(1, 6):
        name, options = _select(report_html, f"lisComplex{number}")
        if name and options:
            complexes.append((name, options))
    if len(complexes) != EXPECTED_COMPLEX_COUNT:
        raise RuntimeError(
            f"OAS {REPORT_ID} 複分類數量為 {len(complexes)}，預期 {EXPECTED_COMPLEX_COUNT}；"
            "來源若少送維度會回傳全為 ... 的假空表。"
        )
    date_name, date_options = _select(report_html, "lisDate")
    if not date_options:
        raise RuntimeError("OAS 11848 找不到日期選項。")
    roles = _find_roles(complexes)
    prefixes = {
        role: complexes[index][1][0][0][:5]
        for role, index in roles.items()
    }
    expected_product = 1
    code_sets = {}
    labels = {}
    for role, index in roles.items():
        options = complexes[index][1]
        expected_product *= len(options)
        code_sets[role] = {value for value, _ in options}
        labels[role] = {value: label for value, label in options}
    print(
        "OAS 11848 dimensions = "
        f"{[(name.rsplit('$', 1)[-1], len(options)) for name, options in complexes]}, "
        f"dates={len(date_options)}, product={expected_product}"
    )

    fields = _hidden_fields(report_html)
    ax_x = "[Measures]" + "".join(
        ";" + options[0][0][:5] for _, options in complexes
    )
    ax_code = "".join(
        ";".join(value for value, _ in options) + ";"
        for _, options in complexes
    ) + NTPC_QUERY_AREA_CODE

    def query(date_value):
        payload = dict(fields)
        payload.update({
            "__EVENTTARGET": "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1$goon",
            "__EVENTARGUMENT": "",
            "axCycle": "Y年",
            "axY": "[Date];[Place]",
            "axDate": date_value,
            "axEffect": REPORT_ID,
            "axFunction": "統計數值",
            "axMode": "3",
            "axSubjectNo": REPORT_ID,
            "axX": ax_x,
            "axCode": ax_code,
            "ShowQueryReturnUrl": NAV,
            "UrlOrgNo": "",
            "FindString": "",
            "Complex": "",
            "Statistic": "",
            "ctl00$ctl00$ContentPlaceHolder3$ContentPlaceHolder1$ddlFunctiontype": "統計數值",
            date_name: date_value,
        })
        encoded_payload = list(payload.items())
        for name, options in complexes:
            encoded_payload.extend((name, value) for value, _ in options)
        response = session.post(SHOW, data=encoded_payload, timeout=300)
        response.raise_for_status()
        return _result_json(response.text)

    records = []
    for date_value, date_label in date_options:
        result = query(date_value)
        values = result["Values"]
        if len(values) != expected_product:
            raise ValueError(
                f"OAS 11848 {date_value} 回傳 {len(values)} 格，但維度乘積為 "
                f"{expected_product}。"
            )
        seen = set()
        for cell in values:
            if not isinstance(cell, dict):
                raise TypeError("OAS 11848 Values 含非物件格子。")
            source_codes = {}
            for role in ROLE_ORDER:
                code = cell.get(prefixes[role])
                if code not in code_sets[role]:
                    raise ValueError(
                        f"OAS 11848 {date_value} 出現未知 {role} code：{code!r}"
                    )
                source_codes[role] = code
            key = (date_value, tuple(source_codes[role] for role in ROLE_ORDER))
            if key in seen:
                raise ValueError(f"OAS 11848 {date_value} 出現重複 source cell：{key}")
            seen.add(key)
            if cell.get("[Place]") not in {None, NTPC_QUERY_AREA_CODE}:
                raise ValueError(f"OAS 11848 回傳非新北市格子：{cell.get('[Place]')!r}")
            year_match = re.match(r"^(\d{4})", date_value)
            if not year_match:
                raise ValueError(f"OAS 11848 日期無法解析年份：{date_value!r}")
            age_label = labels["age"][source_codes["age"]]
            age_lower, age_upper, age_scope = _parse_age_label(age_label)
            marital_label = labels["marital"][source_codes["marital"]]
            marital_status, gender, same_sex = _parse_marital_label(marital_label)
            records.append({
                "year": int(year_match.group(1)),
                "date_value": date_value,
                "date_label": date_label,
                "source_codes": source_codes,
                "source_labels": {
                    role: labels[role][source_codes[role]] for role in ROLE_ORDER
                },
                "age_lower": age_lower,
                "age_upper": age_upper,
                "age_scope": age_scope,
                "marital_status": marital_status,
                "gender": gender,
                "same_sex": same_sex,
                "value": _number(cell.get("Value"), "Value"),
            })
        if len(seen) != expected_product:
            raise ValueError(f"OAS 11848 {date_value} source cell 集合不完整。")
        print(f"OAS {date_value} ({date_label}) values = {len(values)}")
    if not records:
        raise RuntimeError("OAS 11848 沒有取得任何資料。")
    return records


def drop_scope_anomalies(records):
    totals = {}
    for record in records:
        if (
            record["age_scope"] == "summary"
            and record["marital_status"] == "all"
            and record["gender"] == "total"
            and not record["same_sex"]
        ):
            totals[record["year"]] = record["value"]
    if len(totals) < 3:
        raise RuntimeError("OAS 11848 找不到至少三個年度的 scope total。")
    for year, value in sorted(totals.items()):
        if year in KNOWN_BAD_YEARS:
            continue
        peers = [other for other_year, other in totals.items() if other_year != year]
        median = sorted(peers)[len(peers) // 2]
        if median and (value > median * SCALE_ANOMALY_FACTOR or value * SCALE_ANOMALY_FACTOR < median):
            raise ValueError(
                f"OAS 11848 {year} scope total={value:.0f} 與其他年度中位數 "
                f"{median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍。"
            )
    print(f"OAS 11848 scale guard: {len(totals)} years passed")
    return [record for record in records if record["year"] not in KNOWN_BAD_YEARS]


def transform_records(records):
    import json

    rows = []
    for record in records:
        source_labels = record["source_labels"]
        indicator_id = (
            "resident_marital_status_same_sex_count"
            if record["same_sex"]
            else "resident_marital_status_count"
        )
        rows.append({
            "indicator_id": indicator_id,
            "period_start": f"{record['year']}-01-01",
            "period_end": f"{record['year']}-12-31",
            "period_type": "year",
            "age_lower": record["age_lower"],
            "age_upper": record["age_upper"],
            "age_band_raw": source_labels["age"],
            "gender": record["gender"],
            "area_code": NTPC_CITY_CODE,
            "area_level": "city",
            "breakdown": json.dumps({
                "source_platform": "oas.bas.ntpc.gov.tw",
                "source_report": REPORT_ID,
                "marital_status": record["marital_status"],
                "same_sex_subset": record["same_sex"],
                "source_detail_raw": source_labels["marital"],
                "age_scope": record["age_scope"],
                "source_codes": record["source_codes"],
            }, ensure_ascii=False, sort_keys=True),
            "value": record["value"],
            "unit": "人",
            "value_type": "count",
        })
    return rows


def _reconcile(records, data):
    import json

    def input_key(record):
        return (
            record["year"],
            tuple(record["source_codes"][role] for role in ROLE_ORDER),
        )

    expected = {input_key(record): record["value"] for record in records}
    if len(expected) != len(records):
        raise ValueError("OAS 11848 輸入 source cell 有重複。")
    emitted = {}
    for _, row in data.iterrows():
        breakdown = json.loads(row["breakdown"])
        codes = breakdown.get("source_codes")
        if set(codes or {}) != set(ROLE_ORDER):
            raise ValueError("OAS 11848 輸出缺少 source code。")
        key = (
            int(str(row["period_start"])[:4]),
            tuple(codes[role] for role in ROLE_ORDER),
        )
        if key in emitted:
            raise ValueError(f"OAS 11848 輸出 source cell 重複：{key}")
        emitted[key] = float(row["value"])
    if set(expected) != set(emitted):
        raise ValueError("OAS 11848 輸入／輸出 source cell 集合不一致。")
    for key, value in expected.items():
        if abs(value - emitted[key]) > 0.5:
            raise ValueError(f"OAS 11848 source cell 對帳失敗：{key} {value} != {emitted[key]}")

    primary = {}
    for record in records:
        if record["same_sex"]:
            continue
        key = (
            record["year"], record["source_codes"]["age"], record["marital_status"],
        )
        primary.setdefault(key, {})[record["gender"]] = record["value"]
    for key, values in primary.items():
        if set(values) != {"total", "male", "female"}:
            raise ValueError(f"OAS 11848 性別分量不完整：{key} {values}")
        if abs(values["total"] - values["male"] - values["female"]) > 0.5:
            raise ValueError(f"OAS 11848 性別 total 對帳失敗：{key} {values}")

    by_all = {}
    for (year, age_code, status), values in primary.items():
        for gender, value in values.items():
            by_all.setdefault((year, age_code, gender), {})[status] = value
    for key, values in by_all.items():
        if set(values) != set(PRIMARY_STATUSES):
            raise ValueError(f"OAS 11848 婚姻狀態分量不完整：{key} {values}")
        parts = sum(values[status] for status in PRIMARY_STATUSES if status != "all")
        if abs(values["all"] - parts) > 0.5:
            raise ValueError(f"OAS 11848 all/status 對帳失敗：{key} {values}")
    print(
        f"OAS 11848 reconciliation: source cells={len(expected)}, "
        f"primary status groups={len(primary)}"
    )


def _transfer(**kwargs):
    import pandas as pd
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    ready_data_db_uri = kwargs.get("ready_data_db_uri")
    dag_infos = kwargs.get("dag_infos") or {}
    records = drop_scope_anomalies(_fetch_records())
    data = pd.DataFrame(transform_records(records), columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    data["data_time"] = get_tpe_now_time_str(is_with_tz=True)
    _reconcile(records, data)
    required_columns = [column for column in CONTRACT_COLUMNS if column not in {"age_lower", "age_upper"}]
    if list(data.columns) != CONTRACT_COLUMNS or data[required_columns].isna().any().any():
        raise ValueError("OAS 11848 輸出契約欄位或非空檢查失敗。")
    print(f"OAS 11848 ready_data shape = {data.shape}")

    engine = create_engine(ready_data_db_uri)
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
    dag_folder="youth_oas_marital_age_ntpc",
)
dag.create_dag(etl_func=_transfer)
