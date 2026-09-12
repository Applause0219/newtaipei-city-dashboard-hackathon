"""Historical job-vacancy counts by industry from DGBAS open-data XML."""

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
    "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/230513/"
    "mp05005.xml"
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

INDUSTRY_FIELDS = (
    "工業及服務業",
    "工業",
    "礦業及土石採取業",
    "製造業",
    "電力及燃氣供應業",
    "用水供應及污染整治業",
    "營建工程業",
    "服務業",
    "批發及零售業",
    "運輸及倉儲業",
    "住宿及餐飲業",
    "出版影音及資通訊業",
    "金融及保險業",
    "不動產業",
    "專業科學及技術服務業",
    "支援服務業",
    "教育業",
    "醫療保健及社會工作服務業",
    "藝術娛樂及休閒服務業",
    "其他服務業",
)


def _normalise_tag(value):
    return (
        str(value)
        .strip()
        .replace("　", "")
        .replace(" ", "")
        .replace("－", "-")
        .lower()
    )


def _find_tag(tags, role, aliases):
    wanted = {_normalise_tag(alias) for alias in aliases}
    matches = [
        tag
        for tag in tags
        if _normalise_tag(tag) in wanted
        or any(
            _normalise_tag(tag).startswith(alias + "_")
            for alias in wanted
        )
    ]
    if len(matches) != 1:
        raise KeyError(
            f"職缺來源角色 {role!r} 應有 1 個欄位，實際={matches}；全部欄位={tags}"
        )
    return matches[0]


def _resolve_fields(tags):
    period = _find_tag(tags, "period", ("年月別",))
    industries = {
        industry: _find_tag(
            tags,
            industry,
            (f"{industry}_職缺個數", industry),
        )
        for industry in INDUSTRY_FIELDS
    }
    return period, industries


def _parse_count(value, *, field, row_number):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in MISSING:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"職缺第 {row_number} 列欄位 {field!r} 數值無法解析：{value!r}"
        ) from exc
    if number != number.to_integral_value():
        raise ValueError(f"職缺個數不是整數：{field}={value!r}")
    return int(number)


def _parse_period(value):
    text = str(value).strip()
    if not re.fullmatch(r"\d{6}", text):
        raise ValueError(f"職缺年月別不是西元 YYYYMM：{value!r}")
    year, month = int(text[:4]), int(text[4:])
    if not 1 <= month <= 12:
        raise ValueError(f"職缺年月別月份不合法：{value!r}")
    import calendar

    last_day = calendar.monthrange(year, month)[1]
    return (
        f"{year:04d}-{month:02d}-01",
        f"{year:04d}-{month:02d}-{last_day:02d}",
        "month",
        text,
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


def transform_xml(raw, *, data_time):
    from xml.etree import ElementTree as ET

    if isinstance(raw, str):
        root = ET.fromstring(raw)
    else:
        root = ET.fromstring(raw.decode("utf-8-sig"))
    records = [element for element in list(root) if list(element)]
    if not records:
        raise RuntimeError("職缺 XML 沒有資料列")

    rows = []
    input_numeric = 0
    emitted_numeric = 0
    input_sum = 0
    emitted_sum = 0
    source_periods = set()
    hierarchy_checks = 0

    for row_number, record in enumerate(records, 1):
        values = {child.tag: (child.text or "").strip() for child in list(record)}
        period_tag, industry_tags = _resolve_fields(list(values))
        period_start, period_end, period_type, period_raw = _parse_period(
            values[period_tag]
        )
        source_periods.add(period_raw)
        parsed = {}
        for industry in INDUSTRY_FIELDS:
            parsed[industry] = _parse_count(
                values.get(industry_tags[industry]),
                field=industry_tags[industry],
                row_number=row_number,
            )

        # The hierarchy has overlapping aggregate fields. Check the valid
        # parent relationships, rather than summing all 20 columns together.
        industrial_parts = (
            "礦業及土石採取業",
            "製造業",
            "電力及燃氣供應業",
            "用水供應及污染整治業",
            "營建工程業",
        )
        service_parts = (
            "批發及零售業",
            "運輸及倉儲業",
            "住宿及餐飲業",
            "出版影音及資通訊業",
            "金融及保險業",
            "不動產業",
            "專業科學及技術服務業",
            "支援服務業",
            "教育業",
            "醫療保健及社會工作服務業",
            "藝術娛樂及休閒服務業",
            "其他服務業",
        )
        if (
            parsed["工業"] is not None
            and all(parsed[key] is not None for key in industrial_parts)
            and parsed["工業"] != sum(parsed[key] for key in industrial_parts)
        ):
            raise ValueError(f"職缺第 {row_number} 列工業分項對不上工業合計")
        if (
            parsed["服務業"] is not None
            and all(parsed[key] is not None for key in service_parts)
            and parsed["服務業"] != sum(parsed[key] for key in service_parts)
        ):
            raise ValueError(f"職缺第 {row_number} 列服務業分項對不上服務業合計")
        if (
            parsed["工業及服務業"] is not None
            and parsed["工業"] is not None
            and parsed["服務業"] is not None
            and parsed["工業及服務業"]
            != parsed["工業"] + parsed["服務業"]
        ):
            raise ValueError(f"職缺第 {row_number} 列工業及服務業對帳失敗")
        hierarchy_checks += 1

        for industry in INDUSTRY_FIELDS:
            value = parsed[industry]
            if value is None:
                continue
            input_numeric += 1
            emitted_numeric += 1
            input_sum += value
            emitted_sum += value
            rows.append(
                {
                    "indicator_id": "youth_job_vacancy_industry",
                    "period_start": period_start,
                    "period_end": period_end,
                    "period_type": period_type,
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "gender": "total",
                    "area_code": AREA_CODE,
                    "area_level": AREA_LEVEL,
                    "breakdown": json.dumps(
                        {
                            "industry": industry,
                            "source_field": industry_tags[industry],
                            "source_period": period_raw,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "value": value,
                    "unit": "個",
                    "value_type": "count",
                    "data_time": data_time,
                }
            )

    if input_numeric != emitted_numeric or input_sum != emitted_sum:
        raise ValueError(
            "職缺對帳失敗："
            f"輸入 {input_numeric}/{input_sum}，輸出 {emitted_numeric}/{emitted_sum}"
        )
    if len(source_periods) != len({row["period_start"] for row in rows}):
        raise ValueError("職缺來源期間與輸出期間數量不一致")
    return rows, {
        "source_records": len(records),
        "source_periods": len(source_periods),
        "input_numeric_cells": input_numeric,
        "emitted_rows": emitted_numeric,
        "input_value_sum": input_sum,
        "emitted_value_sum": emitted_sum,
        "hierarchy_checks": hierarchy_checks,
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
        if row["age_lower"] is not None or row["age_upper"] is not None:
            raise ValueError("職缺來源無年齡欄位，不得產生年齡值")
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
    print(f"job vacancy diagnostics =========== {diagnostics}")
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
        dag_folder="youth_job_vacancy_industry",
    )
    dag.create_dag(etl_func=_transfer)
