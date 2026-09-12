import html
import io
import json
import math
import re

try:
    from operators.common_pipeline import CommonDag
except ModuleNotFoundError:
    CommonDag = None


CATALOG_URL = "https://data.gov.tw/dataset/41223"
DATASET_ID = "41223"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
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

# 民國 100 是這份分年檔的序列起始／轉換列：本年受理只有 3,541 戶，
# 但本年止累計已含前期數，不能和完整年度直接比較。保留在來源說明，
# 不讓它進入可比的事實序列。
KNOWN_BAD_YEARS = {
    100: "分年檔序列起始／轉換列，累計欄含前期數，非完整年度可比",
}
SCALE_ANOMALY_FACTOR = 3.0

EMITTED_SPECS = (
    {
        "key": "application_count_year",
        "indicator_id": "youth_home_loan_application_households",
        "tokens": ("受理情形", "本年", "戶數"),
        "exclude": ("止累計",),
        "unit": "戶",
        "value_type": "count",
    },
    {
        "key": "application_amount_year",
        "indicator_id": "youth_home_loan_application_amount",
        "tokens": ("受理情形", "本年", "金額"),
        "exclude": ("止累計",),
        "unit": "億元",
        "value_type": "sum",
    },
    {
        "key": "disbursement_count_year",
        "indicator_id": "youth_home_loan_disbursement_households",
        "tokens": ("撥貸情形", "本年", "戶數"),
        "exclude": ("止累計",),
        "unit": "戶",
        "value_type": "count",
    },
    {
        "key": "disbursement_amount_year",
        "indicator_id": "youth_home_loan_disbursement_amount",
        "tokens": ("撥貸情形", "本年", "金額"),
        "exclude": ("止累計",),
        "unit": "億元",
        "value_type": "sum",
    },
)

# 這些欄位不直接輸出，但必須存在，避免來源改版時只剩四個欄位仍被
# 靜默解讀。欄位用角色／token 對應，不依賴 CSV 排列位置。
AUXILIARY_SPECS = (
    {
        "key": "application_count_cumulative",
        "tokens": ("受理情形", "本年止累計", "戶數"),
        "exclude": (),
    },
    {
        "key": "application_amount_cumulative",
        "tokens": ("受理情形", "本年止累計", "金額"),
        "exclude": (),
    },
    {
        "key": "disbursement_count_cumulative",
        "tokens": ("撥貸情形", "本年止累計", "戶數"),
        "exclude": (),
    },
    {
        "key": "disbursement_amount_cumulative",
        "tokens": ("撥貸情形", "本年止累計", "金額"),
        "exclude": (),
    },
    {
        "key": "disbursement_count_rate",
        "tokens": ("撥貸占受理比率", "戶數", "%"),
        "exclude": (),
    },
    {
        "key": "disbursement_amount_rate",
        "tokens": ("撥貸占受理比率", "金額", "%"),
        "exclude": (),
    },
)


def _find_column(columns, tokens, exclude=()):
    matches = [
        column
        for column in columns
        if all(token in str(column) for token in tokens)
        and not any(token in str(column) for token in exclude)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"青安年度檔找不到唯一角色欄位 tokens={tokens!r}, "
            f"exclude={exclude!r}, matches={matches!r}"
        )
    return matches[0]


def extract_resource_url(page_text):
    """從 data.gov.tw 伺服器渲染頁找目前的 CSV URL。"""
    patterns = (
        r'"contentUrl"\s*:\s*"([^"]+\.csv)"',
        r'href=["\']([^"\']+\.csv)["\']',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, page_text, flags=re.IGNORECASE):
            url = html.unescape(match.group(1))
            url = url.replace("\\/", "/").replace("\\u002F", "/")
            if "newgovshare.nta.gov.tw" in url:
                return url
    raise RuntimeError(
        f"資料集 {DATASET_ID} 目錄頁找不到 newgovshare CSV 資源 URL"
    )


def _number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null", "-"}:
        raise ValueError(f"青安年度欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"青安年度欄位 {field} 無法轉數值：{value!r}") from exc
    if math.isnan(number) or number < 0:
        raise ValueError(f"青安年度欄位 {field} 出現無效數值：{value!r}")
    return number


def _roc_year(value):
    try:
        year = int(float(str(value).strip()))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"青安年度民國年無法解析：{value!r}") from exc
    if not 90 <= year <= 999:
        raise ValueError(f"青安年度民國年超出合理範圍：{year}")
    return year


def _resolve_columns(frame):
    columns = list(frame.columns)
    year_col = _find_column(columns, ("資料所屬年度",))
    bank_code_col = _find_column(columns, ("項次",))
    bank_name_col = _find_column(columns, ("銀行名稱",))
    resolved = {
        "roc_year": year_col,
        "bank_code": bank_code_col,
        "bank_name": bank_name_col,
    }
    for spec in (*EMITTED_SPECS, *AUXILIARY_SPECS):
        resolved[spec["key"]] = _find_column(
            columns, spec["tokens"], spec["exclude"]
        )
    return resolved


def _prepare_frame(frame, pd, resolved):
    work = frame.copy()
    work["_roc_year"] = work[resolved["roc_year"]].map(_roc_year)
    work["_bank_code"] = work[resolved["bank_code"]].astype(str).str.strip()
    work["_bank_name"] = work[resolved["bank_name"]].astype(str).str.strip()
    if (work["_bank_code"] == "").any() or (work["_bank_name"] == "").any():
        raise ValueError("青安年度檔存在空白銀行識別欄")
    if work.duplicated(["_roc_year", "_bank_code"]).any():
        duplicate = work.loc[
            work.duplicated(["_roc_year", "_bank_code"], keep=False),
            ["_roc_year", "_bank_code", "_bank_name"],
        ]
        raise ValueError(f"青安年度檔年度×銀行重複：{duplicate.to_dict('records')}")
    for spec in (*EMITTED_SPECS, *AUXILIARY_SPECS):
        work[spec["key"]] = work[resolved[spec["key"]]].map(
            lambda value, key=spec["key"]: _number(value, key)
        )
    # 比率是來源的稽核欄；不輸出成新的 fact，但若改成非百分比就應中止。
    for key in ("disbursement_count_rate", "disbursement_amount_rate"):
        if (work[key] > 100).any():
            raise ValueError(f"青安年度檔 {key} 超過 100%")
    return work


def drop_scope_anomalies(work, pd):
    """用年度受理／撥貸戶數做三倍尺度守門，再排除已知起始列。"""
    kept = work[~work["_roc_year"].isin(KNOWN_BAD_YEARS)].copy()
    if len(kept) < 3:
        raise ValueError("青安年度檔可比年度不足三年，不能執行尺度守門")
    diagnostics = {}
    for key in ("application_count_year", "disbursement_count_year"):
        totals = kept.groupby("_roc_year", dropna=False)[key].sum()
        median = float(totals.median())
        bad = totals[
            (totals > median * SCALE_ANOMALY_FACTOR)
            | (totals < median / SCALE_ANOMALY_FACTOR)
        ]
        diagnostics[key] = {
            "median": median,
            "totals": {int(year): float(value) for year, value in totals.items()},
        }
        if not bad.empty:
            raise ValueError(
                f"青安年度檔 {key} 尺度異常：{bad.to_dict()}; "
                f"其餘年度中位數={median:.2f}，請確認範圍後更新 KNOWN_BAD_YEARS"
            )
    excluded = sorted(set(work["_roc_year"]) & set(KNOWN_BAD_YEARS))
    print(
        "home-loan annual scale guard =========== "
        f"checked={list(diagnostics)}, excluded_roc_years={excluded}"
    )
    return kept, diagnostics


def transform_frame(frame, pd, data_time, source_url):
    resolved = _resolve_columns(frame)
    work = _prepare_frame(frame, pd, resolved)
    kept, scale_diagnostics = drop_scope_anomalies(work, pd)
    rows = []
    for _, record in kept.iterrows():
        roc_year = int(record["_roc_year"])
        ad_year = roc_year + 1911
        for spec in EMITTED_SPECS:
            source_field = resolved[spec["key"]]
            rows.append(
                {
                    "indicator_id": spec["indicator_id"],
                    "period_start": f"{ad_year:04d}-01-01",
                    "period_end": f"{ad_year:04d}-12-31",
                    "period_type": "year",
                    # 來源是政策方案／銀行彙總，不是借款人年齡分布；
                    # 歷年資格又不固定，不能套成命題 18-35。
                    "age_lower": None,
                    "age_upper": None,
                    "age_band_raw": None,
                    "gender": "total",
                    "area_code": "TW",
                    "area_level": "nation",
                    "breakdown": json.dumps(
                        {
                            "bank_code": record["_bank_code"],
                            "bank_name": record["_bank_name"],
                            "source_dataset_id": DATASET_ID,
                            "source_field": source_field,
                            "source_row_count": 1,
                            "policy_name": "青年安心成家貸款",
                            "age_from_eligibility": False,
                            "age_coverage": "unavailable",
                            "scope": "Taiwan_public_banks",
                            "source_url": source_url,
                        },
                        ensure_ascii=False,
                    ),
                    "value": float(record[spec["key"]]),
                    "unit": spec["unit"],
                    "value_type": spec["value_type"],
                    "data_time": data_time,
                }
            )
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = pd.array(data["age_lower"], dtype="Int16")
    data["age_upper"] = pd.array(data["age_upper"], dtype="Int16")
    if len(data) != len(kept) * len(EMITTED_SPECS):
        raise ValueError(
            f"青安年度檔列數對帳失敗：來源 {len(kept)}，輸出 {len(data)}"
        )
    reconciliation = {}
    for spec in EMITTED_SPECS:
        source_total = float(kept[spec["key"]].sum())
        output_total = float(
            data.loc[data["indicator_id"] == spec["indicator_id"], "value"].sum()
        )
        if abs(source_total - output_total) > 1e-6:
            raise ValueError(
                f"青安年度檔 {spec['key']} 對帳失敗："
                f"source={source_total}, output={output_total}"
            )
        reconciliation[spec["indicator_id"]] = {
            "source_total": source_total,
            "output_total": output_total,
            "source_rows": len(kept),
            "output_rows": int(
                (data["indicator_id"] == spec["indicator_id"]).sum()
            ),
        }
    return data, {
        "source_rows_raw": len(frame),
        "source_rows_emitted": len(kept),
        "excluded_roc_years": sorted(set(work["_roc_year"]) - set(kept["_roc_year"])),
        "years": sorted(int(year) for year in kept["_roc_year"].unique()),
        "reconciliation": reconciliation,
        "scale_guard": scale_diagnostics,
    }


def fetch_and_transform(pd, requests, data_time):
    catalog = requests.get(CATALOG_URL, headers={"User-Agent": UA}, timeout=60)
    catalog.raise_for_status()
    source_url = extract_resource_url(catalog.text)
    response = requests.get(source_url, headers={"User-Agent": UA}, timeout=120)
    response.raise_for_status()
    frame = pd.read_csv(
        io.BytesIO(response.content),
        dtype=str,
        encoding="utf-8-sig",
    )
    if frame.empty:
        raise RuntimeError("青安分年 CSV 為空")
    data, diagnostics = transform_frame(frame, pd, data_time, source_url)
    diagnostics.update(
        {
            "source_url": source_url,
            "source_bytes": len(response.content),
            "source_columns": list(frame.columns),
        }
    )
    return data, diagnostics


def _transfer(**kwargs):
    import pandas as pd
    import requests
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    dag_infos = kwargs["dag_infos"]
    data, diagnostics = fetch_and_transform(
        pd,
        requests,
        get_tpe_now_time_str(is_with_tz=True),
    )
    print(f"home-loan annual source =========== {diagnostics['source_url']}")
    print(f"ready_data shape =========== {data.shape}")
    engine = create_engine(kwargs["ready_data_db_uri"])
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=dag_infos.get("load_behavior"),
        default_table=dag_infos.get("ready_data_default_table"),
    )
    update_lasttime_in_data_to_dataset_info(
        engine, dag_infos["dag_id"], data["data_time"].max()
    )


if CommonDag is not None:
    dag = CommonDag(
        proj_folder="proj_new_taipei_city_dashboard",
        dag_folder="youth_home_loan_annual_tw",
    )
    dag.create_dag(etl_func=_transfer)
