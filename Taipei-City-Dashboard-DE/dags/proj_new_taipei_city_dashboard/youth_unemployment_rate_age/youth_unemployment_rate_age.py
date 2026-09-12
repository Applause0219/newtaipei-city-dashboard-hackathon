"""Historical unemployment rates by age from the DGBAS open-data XML."""

import calendar
import json
import re
try:
    import ssl
except ImportError:  # local Workbench Python lacks _ssl; verify fetches via Node
    ssl = None
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from operators.common_pipeline import CommonDag
except ImportError:  # pragma: no cover - local verify has no Airflow
    CommonDag = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SOURCE_URL = (
    "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/230038/"
    "mp0101a07.xml"
)
AREA_CODE = "TW"
AREA_LEVEL = "country"
CONTRACT_COLUMNS = (
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
)
MISSING = {"", "-", "－", "—", "–", "…", "..."}

AGE_RANGES = (
    (15, 19),
    (20, 24),
    (25, 29),
    (30, 34),
    (35, 39),
    (40, 44),
    (45, 49),
    (50, 54),
    (55, 59),
    (60, 64),
    (65, None),
)
EDUCATION_ROLES = {
    "junior_high_and_below": ("國中及以下", "junior_high_and_below"),
    "primary_school_and_below": ("國小及以下", "primary_school_and_below"),
    "junior_high": ("國中_junior_high_",),
    "senior_high": ("高級中等", "senior_high_school"),
    "junior_college_and_above": (
        "大專及以上",
        "junior_college_and_above",
    ),
    "junior_college": ("專科_junior_college_",),
    "university_and_above": ("大學及以上", "university_and_above"),
}


def _normalise_tag(value):
    return (
        str(value)
        .strip()
        .replace("％", "%")
        .replace("－", "-")
        .replace("～", "~")
        .replace("　", "")
        .replace(" ", "")
        .lower()
    )


def _find_one(tags, role, predicate):
    matches = [tag for tag in tags if predicate(_normalise_tag(tag))]
    if len(matches) != 1:
        raise KeyError(
            f"失業率來源角色 {role!r} 應有 1 個欄位，實際={matches}；"
            f"全部欄位={tags}"
        )
    return matches[0]


def _resolve_fields(tags):
    period = _find_one(
        tags,
        "period",
        lambda tag: "年月別" in tag or "year_and_month" in tag,
    )
    gender_total = _find_one(
        tags,
        "gender_total",
        lambda tag: ("總計" in tag or "total" in tag)
        and "age_" not in tag
        and "百分比" in tag,
    )
    gender_male = _find_one(
        tags,
        "gender_male",
        lambda tag: ("男" in tag or "_male_" in tag)
        and "age_" not in tag
        and "百分比" in tag,
    )
    gender_female = _find_one(
        tags,
        "gender_female",
        lambda tag: ("女" in tag or "_female_" in tag)
        and "age_" not in tag
        and "百分比" in tag,
    )

    age_fields = {}
    for tag in tags:
        norm = _normalise_tag(tag)
        match = re.search(r"age[_-](\d{1,3})[-~](\d{1,3})", norm)
        if match:
            age_fields[(int(match.group(1)), int(match.group(2)))] = tag
            continue
        match = re.search(r"age[_-](\d{1,3})[_-]over", norm)
        if match:
            age_fields[(int(match.group(1)), None)] = tag
    if set(age_fields) != set(AGE_RANGES):
        raise KeyError(
            "失業率來源年齡欄位不完整或標籤無法解析："
            f"expected={AGE_RANGES}, actual={sorted(age_fields)}"
        )

    education_fields = {}
    for role, aliases in EDUCATION_ROLES.items():
        def matches(tag, aliases=aliases):
            return any(alias.lower() in tag for alias in aliases) and (
                "百分比" in tag or "percent" in tag
            )

        education_fields[role] = _find_one(tags, role, matches)

    return {
        "period": period,
        "gender": {
            "total": gender_total,
            "male": gender_male,
            "female": gender_female,
        },
        "age": age_fields,
        "education": education_fields,
    }


def _parse_number(value, *, field, row_number):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in MISSING:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"失業率第 {row_number} 列欄位 {field!r} 數值無法解析：{value!r}"
        ) from exc


def _parse_period(value):
    text = str(value).strip().replace(" ", "")
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return f"{year:04d}-01-01", f"{year:04d}-12-31", "year", text
    match = re.fullmatch(r"(\d{4})M?(0[1-9]|1[0-2])", text, re.IGNORECASE)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        last_day = calendar.monthrange(year, month)[1]
        return (
            f"{year:04d}-{month:02d}-01",
            f"{year:04d}-{month:02d}-{last_day:02d}",
            "month",
            text,
        )
    raise ValueError(
        f"失業率年月別無法解析：{value!r}；只接受 YYYY、YYYYMM 或 YYYYMmm"
    )


def _http_bytes(url, *, timeout=180):
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/xml,text/xml,*/*",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, OSError) as exc:
        # ws.dgbas.gov.tw currently presents a certificate chain that is not
        # trusted by this runner. Try normal verification first; only this
        # official host gets the narrowly scoped compatibility fallback.
        # 停用 TLS 驗證是真實風險，預設關閉。
        #
        # 2026-09-10 更正：先前這裡寫「本機實測可正常取得，代表這個 fallback
        # 在憑證庫正常的環境並不需要」——**那個推論是錯的**。
        # 實際用 openssl 檢查 ws.dgbas.gov.tw 的憑證鏈：
        #   葉憑證 CN=www.dgbas.gov.tw，簽發者 TWCA Secure SSL CA
        #   但伺服器送出的中介憑證全屬於另一條體系（ePKI Root /
        #   政府伺服器數位憑證管理中心 G1），**真正的簽發中介不在鏈裡**
        #   → Verify return code: 21 (unable to verify the first certificate)
        # Windows 之所以成功是因為它會自行抓取缺少的中介（AIA chasing），
        # OpenSSL 不會。**所以在 Linux（正式 Airflow 映像）上這一定會失敗，
        # 不是環境沒裝好，是來源伺服器的鏈設定錯誤。**
        #
        # 正確修法：把 TWCA Secure SSL CA 中介憑證加進映像的信任庫
        # （或為這個 host 指定 cafile），而不是關掉驗證。
        # 真的必須暫時繞過時，才把這個 Airflow Variable 設為 "1"，
        # 讓它成為一個看得見、查得到的決定，而不是靜靜留在程式裡。
        # 延後 import：與本檔其他相依一致，避免模組層就綁 Airflow。
        # （這行原本漏了 import，只有走到這個 fallback 分支才會 NameError，
        #   屬於潛伏錯誤——2026-09-10 實跑整批 DAG 時才浮現。）
        from airflow.models import Variable

        allow_insecure = Variable.get("ALLOW_INSECURE_TLS_HOSTS", "0") == "1"
        if not allow_insecure:
            raise RuntimeError(
                f"來源請求失敗 {url}: {exc}。"
                "若確認是本機 CA 憑證庫不完整，請安裝 CA 憑證；"
                '僅在無法安裝時才將 Airflow Variable ALLOW_INSECURE_TLS_HOSTS 設為 "1"。'
            ) from exc
        if urlparse(url).hostname != "ws.dgbas.gov.tw" or ssl is None:
            raise RuntimeError(f"來源請求失敗 {url}: {exc}") from exc
        try:
            with urlopen(
                request,
                timeout=timeout,
                context=ssl._create_unverified_context(),
            ) as response:
                return response.read()
        except (HTTPError, URLError, OSError) as fallback_exc:
            raise RuntimeError(
                f"來源請求失敗 {url}: {fallback_exc}"
            ) from fallback_exc


def _fact_base(period, data_time):
    period_start, period_end, period_type, _ = _parse_period(period)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "period_type": period_type,
        "area_code": AREA_CODE,
        "area_level": AREA_LEVEL,
        "unit": "%",
        "value_type": "rate",
        "data_time": data_time,
    }


def transform_xml(raw, *, data_time):
    from xml.etree import ElementTree as ET

    if isinstance(raw, str):
        root = ET.fromstring(raw)
    else:
        root = ET.fromstring(raw.decode("utf-8-sig"))
    records = [element for element in list(root) if list(element)]
    if not records:
        raise RuntimeError("失業率 XML 沒有資料列")

    rows = []
    input_numeric = 0
    emitted_numeric = 0
    input_sum = Decimal("0")
    emitted_sum = Decimal("0")
    dimension_counts = {"gender": 0, "age": 0, "education": 0}
    dimension_sums = {key: Decimal("0") for key in dimension_counts}
    periods = set()

    for row_number, record in enumerate(records, 1):
        values = {child.tag: (child.text or "").strip() for child in list(record)}
        fields = _resolve_fields(list(values))
        period_raw = values[fields["period"]]
        base = _fact_base(period_raw, data_time)
        periods.add((base["period_start"], base["period_type"]))

        outputs = []
        for gender, tag in fields["gender"].items():
            outputs.append(
                ("gender", tag, None, None, None, gender, "youth_unemployment_rate_gender")
            )
        for age_range in AGE_RANGES:
            tag = fields["age"][age_range]
            outputs.append(
                (
                    "age",
                    tag,
                    age_range[0],
                    age_range[1],
                    tag,
                    "total",
                    "youth_unemployment_rate_age",
                )
            )
        for role, tag in fields["education"].items():
            outputs.append(
                (
                    "education",
                    tag,
                    None,
                    None,
                    None,
                    "total",
                    "youth_unemployment_rate_education",
                )
            )

        for dimension, tag, age_lower, age_upper, age_raw, gender, indicator in outputs:
            value = _parse_number(values.get(tag), field=tag, row_number=row_number)
            if value is None:
                continue
            input_numeric += 1
            emitted_numeric += 1
            input_sum += value
            emitted_sum += value
            dimension_counts[dimension] += 1
            dimension_sums[dimension] += value
            breakdown = {
                "dimension": dimension,
                "source_field": tag,
                "source_period": period_raw,
            }
            rows.append(
                dict(
                    base,
                    indicator_id=indicator,
                    age_lower=age_lower,
                    age_upper=age_upper,
                    age_band_raw=age_raw,
                    gender=gender,
                    breakdown=json.dumps(
                        breakdown, ensure_ascii=False, sort_keys=True
                    ),
                    value=float(value),
                )
            )

    if input_numeric != emitted_numeric or input_sum != emitted_sum:
        raise ValueError(
            "失業率對帳失敗："
            f"輸入 {input_numeric}/{input_sum}，輸出 {emitted_numeric}/{emitted_sum}"
        )
    return rows, {
        "source_records": len(records),
        "periods": len(periods),
        "input_numeric_cells": input_numeric,
        "emitted_rows": emitted_numeric,
        "input_value_sum": str(input_sum),
        "emitted_value_sum": str(emitted_sum),
        "dimension_counts": dimension_counts,
        "dimension_sums": {key: str(value) for key, value in dimension_sums.items()},
    }


def validate_contract(rows):
    allowed_types = {"count", "rate", "median", "mean", "index"}
    for row_number, row in enumerate(rows, 1):
        missing = [column for column in CONTRACT_COLUMNS if column not in row]
        if missing:
            raise ValueError(f"契約第 {row_number} 列缺欄位：{missing}")
        extra = [column for column in row if column not in CONTRACT_COLUMNS]
        if extra:
            raise ValueError(f"契約第 {row_number} 列多出欄位：{extra}")
        if row["value"] is None or row["value_type"] not in allowed_types:
            raise ValueError(f"契約第 {row_number} 列 value/value_type 無效")
        if row["indicator_id"] == "youth_unemployment_rate_age":
            if row["age_lower"] is None and row["age_upper"] is None:
                raise ValueError("年齡失業率列缺少可解析年齡區間")
            if row["age_band_raw"] is None:
                raise ValueError("年齡失業率列未保留原始年齡標籤")
        else:
            if row["age_lower"] is not None or row["age_upper"] is not None:
                raise ValueError("非年齡指標不應帶年齡區間")
        json.loads(row["breakdown"])
    return True


def fetch_and_transform(*, data_time=None):
    if data_time is None:
        data_time = datetime.now().astimezone().isoformat()
    return transform_xml(_http_bytes(SOURCE_URL), data_time=data_time)


def _transfer(**kwargs):
    import pandas as pd
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
    rows, diagnostics = fetch_and_transform(
        data_time=get_tpe_now_time_str(is_with_tz=True)
    )
    validate_contract(rows)
    data = pd.DataFrame(rows, columns=list(CONTRACT_COLUMNS))
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    print(f"unemployment diagnostics =========== {diagnostics}")
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


if CommonDag is not None:
    dag = CommonDag(
        proj_folder="proj_new_taipei_city_dashboard",
        dag_folder="youth_unemployment_rate_age",
    )
    dag.create_dag(etl_func=_transfer)
