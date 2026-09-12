from airflow import DAG
from operators.common_pipeline import CommonDag


URL_TMPL = "https://stats.moe.gov.tw/files/detail/{y}/{y}_student.csv"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
FIRST_ACADEMIC_YEAR = 103

CONTRACT_COLUMNS = [
    "indicator_id", "period_start", "period_end", "period_type",
    "age_lower", "age_upper", "age_band_raw", "gender", "area_code",
    "area_level", "breakdown", "value", "unit", "value_type", "data_time",
]

# 來源仍可能使用改制前舊碼；只用縣市名稱對應標準代碼，與 youth_edu_graduates 完全一致。
COUNTY_CODE = {
    "臺北市": "63000", "新北市": "65000", "桃園市": "68000", "臺中市": "66000",
    "臺南市": "67000", "高雄市": "64000", "基隆市": "10017", "新竹市": "10018",
    "嘉義市": "10020", "宜蘭縣": "10002", "新竹縣": "10004", "苗栗縣": "10005",
    "彰化縣": "10007", "南投縣": "10008", "雲林縣": "10009", "嘉義縣": "10010",
    "屏東縣": "10013", "臺東縣": "10014", "花蓮縣": "10015", "澎湖縣": "10016",
    "金門縣": "09020", "連江縣": "09007",
}


def _clean_text(value):
    value = "" if value is None else str(value).strip()
    if not value or value.lower() in {"nan", "none", "null"}:
        return None
    return value


def _find_col(columns, *musts, exclude=()):
    """依欄位角色比對，避免把跨年度字面名稱當成契約。"""
    for column in columns:
        name = str(column).strip()
        if all(must in name for must in musts) and not any(
            token in name for token in exclude
        ):
            return column
    return None


def _resolve_columns(columns, academic_year):
    columns = list(columns)
    roles = {
        "school_code": _find_col(columns, "學校", "代碼")
        or _find_col(columns, "學校", "代號"),
        "school_name": _find_col(columns, "學校", "名稱"),
        "day_night": _find_col(columns, "日間"),
        "level": _find_col(columns, "等級"),
        "county": _find_col(columns, "縣市"),
        "system": _find_col(columns, "體系"),
        "total": next(
            (column for column in columns
             if "總計" in str(column) and "男" not in str(column)
             and "女" not in str(column)),
            None,
        ),
        "male_total": _find_col(columns, "男", "計", exclude=("年級",)),
        "female_total": _find_col(columns, "女", "計", exclude=("年級",)),
    }
    missing = [
        role for role in ("school_code", "school_name", "day_night", "level", "county", "system")
        if roles[role] is None
    ]
    if missing:
        raise ValueError(
            f"學年度 {academic_year} 缺少必要角色欄位 {missing}；"
            f"實際欄位：{columns}"
        )

    roles["male_detail"] = [
        column for column in columns
        if "男" in str(column)
        and not any(token in str(column) for token in ("計", "總計", "合計"))
    ]
    roles["female_detail"] = [
        column for column in columns
        if "女" in str(column)
        and not any(token in str(column) for token in ("計", "總計", "合計"))
    ]
    if (roles["male_total"] is None) != (roles["female_total"] is None):
        raise ValueError(
            f"學年度 {academic_year} 只有一個性別合計欄："
            f"{roles['male_total']}, {roles['female_total']}"
        )
    if not roles["male_detail"] or not roles["female_detail"]:
        raise ValueError(
            f"學年度 {academic_year} 找不到年級／延修生男女明細欄；"
            f"實際欄位：{columns}"
        )
    return roles


def _strip_code(value):
    """去除 'B 學士'、'B學士'、'01 新北市' 的前綴，只留名稱。"""
    s = _clean_text(value)
    if s is None:
        return None
    parts = s.split(None, 1)
    if len(parts) == 2:
        return parts[1].strip()
    if s[0].isascii() and s[0].isupper() and len(s) > 1:
        return s[1:].strip() or s
    index = 0
    while index < len(s) and s[index].isdigit():
        index += 1
    return s[index:].strip() or s


def _code_of(value):
    """保留穩定的代碼前綴，供下游跨年度分組使用。"""
    s = _clean_text(value)
    if s is None:
        return None
    if " " in s or "　" in s:
        return s.split(None, 1)[0]
    return s[:1]


def _number(value, field, allow_dash=False):
    """嚴格解析學生數；CSV 的逗號由 parser 還原後，這裡再去除。"""
    raw = "" if value is None else str(value).strip()
    if allow_dash and raw in {"-", "—", "－"}:
        return 0.0
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"學生來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"學生來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or number != number:
        raise ValueError(f"學生來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _student_counts(row, roles, academic_year):
    male_detail = sum(
        _number(row[column], column, allow_dash=True)
        for column in roles["male_detail"]
    )
    female_detail = sum(
        _number(row[column], column, allow_dash=True)
        for column in roles["female_detail"]
    )

    if roles["male_total"] is not None:
        male = _number(row[roles["male_total"]], roles["male_total"], allow_dash=True)
        female = _number(
            row[roles["female_total"]], roles["female_total"], allow_dash=True
        )
        if abs(male - male_detail) > 0.5 or abs(female - female_detail) > 0.5:
            raise ValueError(
                f"學年度 {academic_year} 性別合計與年級明細不一致："
                f"male {male}/{male_detail}, female {female}/{female_detail}"
            )
        basis = "source_gender_totals"
    else:
        male, female = male_detail, female_detail
        basis = "sum_of_grade_and_extension_fields"

    total = male + female
    if roles["total"] is not None:
        source_total = _number(row[roles["total"]], roles["total"], allow_dash=True)
        if abs(source_total - total) > 0.5:
            raise ValueError(
                f"學年度 {academic_year} 總計與男女合計不一致："
                f"total {source_total}, male+female {total}"
            )
        total = source_total
        basis = "source_total_and_gender_totals"
    return male, female, total, basis


def fetch_year(academic_year):
    """抓一個學年度；404 代表該年度尚未上架。"""
    import io

    import pandas as pd
    import requests

    response = requests.get(
        URL_TMPL.format(y=academic_year),
        headers={"User-Agent": UA},
        timeout=90,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return pd.read_csv(
        io.BytesIO(response.content),
        encoding="utf-8-sig",
        dtype=str,
    )


def fetch_available_frames():
    """從 103 往後抓，連續兩個 404 才停止，讓新年度自動納入。"""
    frames = []
    academic_year, misses = FIRST_ACADEMIC_YEAR, 0
    while misses < 2:
        frame = fetch_year(academic_year)
        if frame is None:
            print(f"academic year {academic_year}: 404")
            misses += 1
        else:
            frames.append((academic_year, frame))
            print(f"academic year {academic_year}: {len(frame)} rows")
            misses = 0
        academic_year += 1
    if not frames:
        raise RuntimeError("沒有抓到任何學年度學生資料。")
    return frames


def _reconcile(data, expected):
    """輸出 total／male／female 各自與新北輸入對帳。"""
    for academic_year, values in sorted(expected.items()):
        period_start = f"{academic_year + 1911}-08-01"
        subset = data[data["period_start"] == period_start]
        total_rows = subset[subset["gender"] == "total"]
        if len(total_rows) != values["rows"]:
            raise ValueError(
                f"學年度 {academic_year} 輸出列數對帳失敗："
                f"輸入 {values['rows']}、輸出 {len(total_rows)}"
            )
        for gender in ("male", "female", "total"):
            emitted = float(subset.loc[subset["gender"] == gender, "value"].sum())
            if abs(emitted - values[gender]) > 0.5:
                raise ValueError(
                    f"學年度 {academic_year} 學生數對帳失敗（{gender}）："
                    f"輸入 {values[gender]}、輸出 {emitted}"
                )
        print(
            f"academic year {academic_year}: NTPC {values['rows']} rows, "
            f"students {int(values['total'])}, reconciled"
        )


def transform_frames(frames, data_time=None):
    """把學生 CSV 轉成事實表；DAG 與 verify 共用此轉換。"""
    import json

    import pandas as pd

    rows = []
    expected = {}
    for academic_year, frame in frames:
        frame = frame.rename(
            columns={column: str(column).strip() for column in frame.columns}
        )
        roles = _resolve_columns(frame.columns, academic_year)
        year_expected = {"rows": 0, "male": 0.0, "female": 0.0, "total": 0.0}
        academic_start_year = academic_year + 1911
        period_start = f"{academic_start_year}-08-01"
        period_end = f"{academic_start_year + 1}-07-31"

        for _, source_row in frame.iterrows():
            county = _strip_code(source_row[roles["county"]])
            area_code = COUNTY_CODE.get(county)
            if area_code != "65000":
                continue

            male, female, total, basis = _student_counts(
                source_row, roles, academic_year
            )
            year_expected["rows"] += 1
            year_expected["male"] += male
            year_expected["female"] += female
            year_expected["total"] += total

            level_raw = _clean_text(source_row[roles["level"]])
            if level_raw is None:
                raise ValueError(f"學年度 {academic_year} 出現空白等級別。")
            breakdown = json.dumps(
                {
                    "school": _clean_text(source_row[roles["school_name"]]),
                    "school_code": _clean_text(source_row[roles["school_code"]]),
                    "level": _strip_code(level_raw),
                    "level_code": _code_of(level_raw),
                    "day_night": _strip_code(source_row[roles["day_night"]]),
                    "system": _strip_code(source_row[roles["system"]]),
                    "county": county,
                    "source_academic_year": str(academic_year),
                    "student_count_basis": basis,
                    "source_total_field": roles["total"],
                    "source_male_field": roles["male_total"],
                    "source_female_field": roles["female_total"],
                },
                ensure_ascii=False,
            )
            for gender, value in (
                ("male", male), ("female", female), ("total", total)
            ):
                rows.append(
                    {
                        "indicator_id": "college_student_count",
                        "period_start": period_start,
                        "period_end": period_end,
                        "period_type": "academic_year",
                        # 來源只有學制／年級，沒有人的年齡；不得由學制推估 18-35。
                        "age_lower": None,
                        "age_upper": None,
                        "age_band_raw": None,
                        "gender": gender,
                        "area_code": area_code,
                        "area_level": "city",
                        "breakdown": breakdown,
                        "value": value,
                        "unit": "人",
                        "value_type": "count",
                        "data_time": data_time,
                    }
                )
        expected[academic_year] = year_expected

    if not rows:
        raise RuntimeError("學生資料轉換後沒有新北市資料列。")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if data_time is None:
        from datetime import datetime, timedelta, timezone

        data["data_time"] = datetime.now(
            timezone(timedelta(hours=8))
        ).isoformat()
    _reconcile(data, expected)
    data.attrs["expected_reconciliation"] = expected
    return data


def _transfer(**kwargs):
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

    data = transform_frames(
        fetch_available_frames(),
        data_time=get_tpe_now_time_str(is_with_tz=True),
    )
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


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_edu_students",
)
dag.create_dag(etl_func=_transfer)
