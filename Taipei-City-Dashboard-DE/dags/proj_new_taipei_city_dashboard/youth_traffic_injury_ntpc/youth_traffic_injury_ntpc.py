from airflow import DAG  # noqa: F401
from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
CATALOG = "https://data.gov.tw/dataset/{dataset_id}"
INDICATOR_ID = "traffic_injury_party_count"

# 每個年度是獨立資料集，資源 UUID 會變動；DAG 只保存目錄頁上的穩定編號。
YEAR_DATASETS = {
    109: "158864",
    110: "158865",
    111: "161199",
    112: "167905",
    113: "172969",
    114: "177136",
}

NTPC_UNIT = "新北市政府警察局"
CITY_CODE = "65000"
KNOWN_BAD_YEARS = frozenset()

# 實測 109--114 年新北 A2 的來源年齡欄出現下列特殊值：
# -1，以及 148--210 間的少數值。它們不能當作真實年齡，也不能推回年齡；
# 以 NULL 年齡保留原列，任何未列入此清單的新異常值則直接 raise。
KNOWN_SOURCE_AGE_ANOMALIES = frozenset(
    {
        -1,
        148,
        150,
        160,
        163,
        164,
        173,
        174,
        176,
        177,
        178,
        179,
        180,
        181,
        182,
        183,
        184,
        185,
        186,
        188,
        189,
        190,
        191,
        192,
        194,
        195,
        199,
        200,
        201,
        202,
        203,
        210,
    }
)

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

# 與 youth_pop_single_age、youth_traffic_fatal_ntpc 完全一致。
DISTRICTS = {
    "板橋區": "65000010",
    "三重區": "65000020",
    "中和區": "65000030",
    "永和區": "65000040",
    "新莊區": "65000050",
    "新店區": "65000060",
    "樹林區": "65000070",
    "鶯歌區": "65000080",
    "三峽區": "65000090",
    "淡水區": "65000100",
    "汐止區": "65000110",
    "瑞芳區": "65000120",
    "土城區": "65000130",
    "蘆洲區": "65000140",
    "五股區": "65000150",
    "泰山區": "65000160",
    "林口區": "65000170",
    "深坑區": "65000180",
    "石碇區": "65000190",
    "坪林區": "65000200",
    "三芝區": "65000210",
    "石門區": "65000220",
    "八里區": "65000230",
    "平溪區": "65000240",
    "雙溪區": "65000250",
    "貢寮區": "65000260",
    "金山區": "65000270",
    "萬里區": "65000280",
    "烏來區": "65000290",
}


# ---------------------------------------------------------------------------
# ★ 車種分類在民國 112 年整批改制，跨年趨勢不可直接用原始車種
#
# 實測 18-35 歲當事人的車種分布（人數）：
#
#   車種                        109     110     111     112     113     114
#   小客車                    5,398   5,518   5,765   2,885       -       -
#   小客車(含客、貨兩用)          -       -       -   3,083   6,328   7,256
#   小貨車(含客、貨兩用)      1,005   1,084   1,049     474       -       -
#   大貨車                      116     121     143      47       -       -
#   大客車                      111      65      73     149     302     324
#
# 民國 112 是過渡年（新舊並存），113 起舊分類完全消失。
# 直接用原始車種畫趨勢，「小客車」會呈現 5,765 -> 2,885 -> 0 的**假崩跌**，
# 「大客車」則會有假的 3 倍成長。
#
# 因此在保留原始 vehicle 之外，另外輸出一個**跨年穩定的粗分組**
# vehicle_group。這不是估算、不會改動任何數值，只是加一個可安全比較的標籤；
# 原始分類仍完整保留在 breakdown.vehicle，下游要細分仍然拿得到。
VEHICLE_GROUPS = (
    ("機車", ("機車", "重型機車", "輕型機車", "普通重型機車", "普通輕型機車",
              "大型重型機車", "小型輕型機車")),
    ("小型車", ("小客車", "小貨車", "小客車(含客、貨兩用)", "小貨車(含客、貨兩用)",
                "小客貨車", "計程車")),
    ("大型車", ("大客車", "大貨車", "曳引車", "半聯結車", "全聯結車",
                "聯結車", "特種車", "軍車")),
    ("行人", ("人", "行人")),
    ("慢車", ("慢車", "腳踏車", "自行車")),
)


def _vehicle_group(vehicle):
    """把逐年變動的原始車種對到一個跨年穩定的粗分組。

    比對用「包含」而非完全相等，因為來源會在大類前後加修飾語
    （小客車 -> 小客車(含客、貨兩用)、小客車 -> 普通小客車）。
    順序有意義：機車先比，才不會讓「普通重型機車」落到別組。
    未知車種回 '其他'，不 raise：車種只是分析維度，不影響數值正確性，
    而來源新增車種是常態。新車種會出現在 verify 的 youth_vehicle 統計裡。
    """
    text = str(vehicle).strip()
    for group, members in VEHICLE_GROUPS:
        for member in members:
            if member in text:
                return group
    return "其他"


def _find_col(columns, *musts):
    """依欄位角色比對，不把跨年度欄名寫死。"""
    for column in columns:
        if all(must in column for must in musts):
            return column
    return None


def _clean(value):
    return "" if value is None else str(value).strip()


def _district_of(place):
    text = _clean(place)
    for district in DISTRICTS:
        if district in text:
            return district
    return None


def _gender_code(label):
    text = _clean(label)
    if text == "男":
        return "male"
    if text == "女":
        return "female"
    return "other"


def _parse_age(raw):
    """解析單齡；來源明確的特殊值保留為 NULL，不做年齡推估。"""
    text = _clean(raw)
    if not text:
        raise ValueError(f"A2 年齡無法解析：原始值={raw!r}")

    signed = text[1:] if text[:1] in {"+", "-"} else text
    if not signed.isdigit():
        raise ValueError(f"A2 年齡無法解析：原始值={raw!r}")

    age = int(text)
    if age in KNOWN_SOURCE_AGE_ANOMALIES:
        return None, None
    if age < 0 or age > 120:
        raise ValueError(f"A2 年齡超出可解析範圍：原始值={raw!r}")
    return age, age


def _parse_death_count(raw):
    """解析事故欄位中的死亡數，供 A2 跨欄位一致性檢核。"""
    text = _clean(raw)
    for part in text.split(";"):
        if part.startswith("死亡"):
            count = part[len("死亡") :].strip()
            if count.isdigit():
                return int(count)
    raise ValueError(f"A2 死亡受傷人數無法解析：原始值={raw!r}")


def _check_scale_anomalies(year_totals):
    """防止單一年度誤抓全國資料或最新年度異常腰斬。"""
    observed = {
        year: total
        for year, total in year_totals.items()
        if year not in KNOWN_BAD_YEARS
    }
    if len(observed) < 3:
        return

    for year, total in observed.items():
        peers = [value for peer, value in observed.items() if peer != year]
        median = sorted(peers)[len(peers) // 2]
        if median and (total > median * 3 or total * 3 < median):
            raise ValueError(
                f"A2 年度尺度異常：ROC {year}={total}，"
                f"其餘年度中位數={median}，超過 3 倍"
            )


def _transfer(**kwargs):
    import csv
    import html
    import io
    import json
    import re
    import tempfile
    import zipfile
    from collections import Counter

    import pandas as pd
    import requests
    from sqlalchemy import create_engine
    from utils.get_time import get_tpe_now_time_str
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    ready_data_db_uri = kwargs.get("ready_data_db_uri")
    dag_infos = kwargs.get("dag_infos") or {}
    dag_id = dag_infos.get("dag_id")
    load_behavior = dag_infos.get("load_behavior")
    default_table = dag_infos.get("ready_data_default_table")
    headers = {"User-Agent": UA}

    def resolve_download_url(dataset_id):
        response = requests.get(
            CATALOG.format(dataset_id=dataset_id),
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        page = html.unescape(response.text)
        links = re.findall(
            r"https?://opdadm\.moi\.gov\.tw[^\"'<> \r\n]+/download",
            page,
        )
        links = list(dict.fromkeys(links))
        if not links:
            raise RuntimeError(
                f"資料集 {dataset_id} 的目錄頁找不到 opdadm 下載連結"
            )
        return links[0]

    def process_year(roc_year, dataset_id):
        import_year = roc_year + 1911
        year_base = {
            "indicator_id": INDICATOR_ID,
            "period_start": f"{import_year}-01-01",
            "period_end": f"{import_year}-12-31",
            "period_type": "year",
            "unit": "人",
            "value_type": "count",
        }
        by_gender = Counter()
        by_total = Counter()
        city_by_gender = Counter()
        city_by_total = Counter()
        accident_deaths = {}
        unknown_age_values = Counter()
        scanned_rows = 0
        ntpc_rows = 0

        download_url = resolve_download_url(dataset_id)
        response = requests.get(
            download_url,
            headers=headers,
            stream=True,
            timeout=900,
        )
        try:
            response.raise_for_status()
            with tempfile.SpooledTemporaryFile(
                max_size=64 * 1024 * 1024,
                mode="w+b",
            ) as zip_buffer:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        zip_buffer.write(chunk)
                zip_buffer.seek(0)

                with zipfile.ZipFile(zip_buffer) as archive:
                    members = sorted(
                        name
                        for name in archive.namelist()
                        if "A2" in name.upper()
                        and name.lower().endswith(".csv")
                    )
                    if not members:
                        raise RuntimeError(
                            f"ROC {roc_year} ZIP 找不到 A2 CSV："
                            f"{archive.namelist()[:5]}"
                    )

                    for member in members:
                        with archive.open(member) as binary:  # noqa: SIM117
                            with io.TextIOWrapper(
                                binary,
                                encoding="utf-8-sig",
                                newline="",
                            ) as text:
                                reader = csv.DictReader(text)
                                columns = reader.fieldnames or []
                                column_roles = {
                                    "unit": _find_col(columns, "處理單位"),
                                    "place": _find_col(columns, "發生地點"),
                                    "age": _find_col(
                                        columns,
                                        "事故發生時",
                                        "年齡",
                                    )
                                    or _find_col(
                                        columns,
                                        "當事者",
                                        "年齡",
                                    ),
                                    "gender": _find_col(
                                        columns,
                                        "當事者",
                                        "性",
                                        "別",
                                    ),
                                    "vehicle": _find_col(
                                        columns,
                                        "車種",
                                        "大類別",
                                    ),
                                    "injury": _find_col(
                                        columns,
                                        "死亡受傷人數",
                                    ),
                                    "date": _find_col(columns, "發生日期"),
                                    "time": _find_col(columns, "發生時間"),
                                }
                                missing = [
                                    role
                                    for role, column in column_roles.items()
                                    if column is None
                                ]
                                if missing:
                                    raise ValueError(
                                        f"ROC {roc_year} / {member} 缺少欄位角色"
                                        f" {missing}；實際欄位={columns}"
                                    )

                                for source_row in reader:
                                    scanned_rows += 1
                                    if (
                                        _clean(
                                            source_row[column_roles["unit"]]
                                        )
                                        != NTPC_UNIT
                                    ):
                                        continue
                                    ntpc_rows += 1

                                    place = _clean(
                                        source_row[column_roles["place"]]
                                    )
                                    district = _district_of(place)
                                    if district is None:
                                        raise ValueError(
                                            f"ROC {roc_year} 新北市列無法解析行政區："
                                            f"{place!r}"
                                        )

                                    age_raw = _clean(
                                        source_row[column_roles["age"]]
                                    )
                                    age_lower, age_upper = _parse_age(age_raw)
                                    if age_lower is None:
                                        unknown_age_values[age_raw] += 1

                                    gender = _gender_code(
                                        source_row[column_roles["gender"]]
                                    )
                                    vehicle = (
                                        _clean(
                                            source_row[column_roles["vehicle"]]
                                        )
                                        or "未填"
                                    )
                                    injury = (
                                        _clean(
                                            source_row[column_roles["injury"]]
                                        )
                                        or "未填"
                                    )
                                    date = _clean(
                                        source_row[column_roles["date"]]
                                    )
                                    time = _clean(
                                        source_row[column_roles["time"]]
                                    )
                                    accident_id = (date, time, place)
                                    death_count = _parse_death_count(injury)
                                    previous = accident_deaths.get(accident_id)
                                    if (
                                        previous is not None
                                        and previous != death_count
                                    ):
                                        raise ValueError(
                                            f"ROC {roc_year} 同一事故的死亡欄位不一致："
                                            f"{accident_id!r}"
                                        )
                                    accident_deaths[accident_id] = death_count

                                    gender_key = (
                                        district,
                                        age_lower,
                                        age_upper,
                                        age_raw,
                                        gender,
                                        injury,
                                        vehicle,
                                    )
                                    total_key = (
                                        district,
                                        age_lower,
                                        age_upper,
                                        age_raw,
                                        injury,
                                        vehicle,
                                    )
                                    city_gender_key = (
                                        age_lower,
                                        age_upper,
                                        age_raw,
                                        gender,
                                        injury,
                                        vehicle,
                                    )
                                    city_total_key = (
                                        age_lower,
                                        age_upper,
                                        age_raw,
                                        injury,
                                        vehicle,
                                    )
                                    by_gender[gender_key] += 1
                                    by_total[total_key] += 1
                                    city_by_gender[city_gender_key] += 1
                                    city_by_total[city_total_key] += 1
        finally:
            response.close()

        if not ntpc_rows:
            raise RuntimeError(f"ROC {roc_year} 沒有新北市 A2 當事者")

        district_gender_total = sum(by_gender.values())
        district_total = sum(by_total.values())
        city_gender_total = sum(city_by_gender.values())
        city_total = sum(city_by_total.values())
        if not (
            district_gender_total
            == district_total
            == city_gender_total
            == city_total
            == ntpc_rows
        ):
            raise ValueError(
                f"ROC {roc_year} 對帳失敗：輸入={ntpc_rows}，"
                f"district_gender={district_gender_total}，"
                f"district_total={district_total}，"
                f"city_gender={city_gender_total}，city_total={city_total}"
            )

        def emit(counter, area_code, area_level, area_name, has_gender):
            output = []
            for key, count in counter.items():
                if has_gender:
                    if area_level == "district":
                        (
                            district_or_age,
                            age_lower,
                            age_upper,
                            age_raw,
                            gender,
                            injury,
                            vehicle,
                        ) = key
                        district_name = district_or_age
                    else:
                        (
                            age_lower,
                            age_upper,
                            age_raw,
                            gender,
                            injury,
                            vehicle,
                        ) = key
                        district_name = area_name
                else:
                    if area_level == "district":
                        (
                            district_name,
                            age_lower,
                            age_upper,
                            age_raw,
                            injury,
                            vehicle,
                        ) = key
                    else:
                        (
                            age_lower,
                            age_upper,
                            age_raw,
                            injury,
                            vehicle,
                        ) = key
                    gender = "total"
                    district_name = (
                        district_name if area_level == "district" else area_name
                    )

                output.append(
                    dict(
                        year_base,
                        age_lower=age_lower,
                        age_upper=age_upper,
                        age_band_raw=age_raw,
                        gender=gender,
                        area_code=(
                            DISTRICTS[district_name]
                            if area_level == "district"
                            else area_code
                        ),
                        area_level=area_level,
                        breakdown=json.dumps(
                            {
                                "vehicle": vehicle,
                                # 跨年穩定的粗分組；原始 vehicle 同時保留。
                                # 畫跨年趨勢請用這個，不要用 vehicle。
                                "vehicle_group": _vehicle_group(vehicle),
                                "injury_severity": injury,
                                "district_name": district_name,
                                "age_status": (
                                    "single_age"
                                    if age_lower is not None
                                    else "source_value_unavailable"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        value=float(count),
                    )
                )
            return output

        year_rows = []
        year_rows.extend(
            emit(by_gender, "", "district", "", has_gender=True)
        )
        year_rows.extend(
            emit(by_total, "", "district", "", has_gender=False)
        )
        year_rows.extend(
            emit(
                city_by_gender,
                CITY_CODE,
                "city",
                "新北市全市",
                has_gender=True,
            )
        )
        year_rows.extend(
            emit(
                city_by_total,
                CITY_CODE,
                "city",
                "新北市全市",
                has_gender=False,
            )
        )

        youth_total = sum(
            count
            for key, count in by_total.items()
            if key[1] is not None and 18 <= key[1] <= 35
        )
        youth_by_vehicle = Counter()
        youth_by_injury = Counter()
        for key, count in by_total.items():
            age_lower = key[1]
            if age_lower is not None and 18 <= age_lower <= 35:
                youth_by_vehicle[key[5]] += count
                youth_by_injury[key[4]] += count

        death_accidents = sum(
            1 for death_count in accident_deaths.values() if death_count > 0
        )
        death_total = sum(accident_deaths.values())
        print(
            f"ROC {roc_year} dataset={dataset_id} "
            f"scanned_rows={scanned_rows} ntpc_rows={ntpc_rows} "
            f"aggregated_rows={len(year_rows)}"
        )
        print(
            f"ROC {roc_year} reconciliation input_total={ntpc_rows} "
            f"district_gender_total={district_gender_total} "
            f"city_total={city_total} "
            f"unknown_age_rows={sum(unknown_age_values.values())} "
            f"emitted_rows={len(year_rows)} "
            f"emitted_rows_nonzero={sum(1 for row in year_rows if row['value'])}"
        )
        print(
            f"ROC {roc_year} A2 unique_accidents={len(accident_deaths)} "
            f"accidents_with_death={death_accidents} "
            f"death_total_from_field={death_total} "
            f"death_field_conflicts=0"
        )
        print(
            f"ROC {roc_year} age18_35_exact={youth_total} "
            f"youth_vehicle={dict(youth_by_vehicle)} "
            f"youth_injury={dict(youth_by_injury)}"
        )
        if unknown_age_values:
            print(
                f"ROC {roc_year} source_age_anomalies="
                f"{dict(unknown_age_values)}"
            )
        return year_rows, ntpc_rows

    rows = []
    year_totals = {}
    for roc_year, dataset_id in sorted(YEAR_DATASETS.items()):
        year_rows, ntpc_rows = process_year(roc_year, dataset_id)
        rows.extend(year_rows)
        year_totals[roc_year] = ntpc_rows

    _check_scale_anomalies(year_totals)
    print(f"A2 scale_check=PASS yearly_ntpc_rows={year_totals}")

    if not rows:
        raise RuntimeError("沒有產出任何資料列，來源或篩選條件可能已變更。")

    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    data["data_time"] = get_tpe_now_time_str(is_with_tz=True)

    print(f"ready_data shape =========== {data.shape}")
    print(data.head(5).to_string(index=False))

    engine = create_engine(ready_data_db_uri)
    save_dataframe_to_postgresql(
        engine,
        data=data,
        load_behavior=load_behavior,
        default_table=default_table,
    )
    update_lasttime_in_data_to_dataset_info(
        engine,
        dag_id,
        data["data_time"].max(),
    )


dag = CommonDag(
    proj_folder="proj_new_taipei_city_dashboard",
    dag_folder="youth_traffic_injury_ntpc",
)
dag.create_dag(etl_func=_transfer)
