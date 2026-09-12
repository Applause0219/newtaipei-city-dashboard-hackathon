"""新北市青年局 A05--A08 統計分析 PDF 的可重現抽取。"""

import calendar
import json
import math
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from airflow import DAG  # noqa: F401
from operators.common_pipeline import CommonDag

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120 Safari/537.36"
)

PDF_SOURCES = (
    {
        "source_id": "A05",
        "roc_year": 113,
        "name": "113 年度新北市青職基地職涯專業服務統計分析",
        "url": (
            "https://www.youth.ntpc.gov.tw/youth/ch/app/data/doc?"
            "detailNo=1404372674104266752&module=youth0008&type=s"
        ),
    },
    {
        "source_id": "A06",
        "roc_year": 114,
        "name": "114 年度新北青創小聚課程規劃及參與情形統計分析報告",
        "url": (
            "https://www.youth.ntpc.gov.tw/youth/ch/app/data/doc?"
            "detailNo=1526873271595700224&module=youth0008&type=s"
        ),
    },
    {
        "source_id": "A07",
        "roc_year": 113,
        "name": "113 年度青年局綜合發展補助案件性別統計分析",
        "url": (
            "https://www.youth.ntpc.gov.tw/youth/ch/app/data/doc?"
            "detailNo=1404373073485893632&module=youth0008&type=s"
        ),
    },
    {
        "source_id": "A08",
        "roc_year": 112,
        "name": "112 年度青年局綜合發展補助案件統計分析報告",
        "url": (
            "https://www.youth.ntpc.gov.tw/youth/ch/app/data/doc?"
            "detailNo=1260108445797847040&module=youth0008&type=s"
        ),
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

NUMBER_RE = re.compile(r"(?<![\d,])\d[\d,]*(?:\.\d+)?")
SCALE_ANOMALY_FACTOR = 3.0

# 本 DAG 目前沒有已確認的壞年度。A06 的 115 年是 1--6 月，會用月份
# period 保存，不與完整年度混作同一尺度序列；A07/A08 的補助統計範圍
# 也不同，不能把兩份報告的參與人次當作同一個可比序列。
KNOWN_BAD_YEARS = {}


def _compact(value):
    """正規化 PDF 的相容字元與空白，但不改變數字。"""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", "", text)


def _parse_number(value, field):
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        raise ValueError(f"青年局來源欄位 {field} 為空")
    try:
        number = float(raw.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"青年局來源欄位 {field} 無法轉數值：{value!r}") from exc
    if number < 0 or not math.isfinite(number):
        raise ValueError(f"青年局來源欄位 {field} 出現無效數值：{value!r}")
    return number


def _parse_human_count(value, field):
    """解析報告中的阿拉伯／中文單位混寫，例如 3萬6千、7千3百零9。"""
    raw = _compact(value).replace(",", "")
    digits = {
        "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000, "萬": 10000, "億": 100000000}
    total = 0
    section = 0
    current = 0
    saw_value = False
    for char in raw:
        if char.isdigit():
            current = current * 10 + int(char)
            saw_value = True
        elif char in digits:
            current = current * 10 + digits[char]
            saw_value = True
        elif char in units:
            unit = units[char]
            if unit >= 10000:
                section += current
                total += (section or 1) * unit
                section = 0
            else:
                section += (current or 1) * unit
            current = 0
            saw_value = True
        else:
            raise ValueError(f"青年局來源欄位 {field} 無法解析數字：{value!r}")
    if not saw_value:
        raise ValueError(f"青年局來源欄位 {field} 沒有數字：{value!r}")
    return float(total + section + current)


def _pdf_to_text(pdf_bytes, source_name):
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError(
            "找不到 pdftotext。Airflow 映像需提供 Poppler pdftotext "
            "（通常來自 poppler-utils），才能解析青年局 PDF。"
        )
    with tempfile.TemporaryDirectory(prefix="youth_bureau_program_pdf_") as temp_dir:
        pdf_path = Path(temp_dir) / "source.pdf"
        pdf_path.write_bytes(pdf_bytes)
        result = subprocess.run(
            [executable, "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"{source_name} 的 pdftotext 失敗：{error}")
        text = result.stdout.decode("utf-8")
        if not text.strip():
            raise RuntimeError(f"{source_name} 沒有文字層，需先做 OCR；本 DAG 不靜默略過。")
        return text


def _fetch_pdf(source):
    import requests

    response = requests.get(source["url"], headers={"User-Agent": UA}, timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise RuntimeError(
            f"{source['name']} 回應不是 PDF："
            f"Content-Type={response.headers.get('Content-Type', '')!r}"
        )
    print(
        f"{source['source_id']} PDF HTTP {response.status_code} "
        f"bytes={len(response.content)}"
    )
    return response.content


def _find_section(text, start_pattern, end_pattern):
    start = re.search(start_pattern, text)
    if not start:
        raise ValueError(f"PDF 找不到區段：{start_pattern}")
    tail = text[start.end():]
    end = re.search(end_pattern, tail)
    if not end:
        raise ValueError(f"PDF 區段找不到結尾：{end_pattern}")
    return tail[:end.start()]


def _numbers_after_label(block, label, count, field):
    """在語意列附近取數字；不依賴 PDF 固定欄位寬度。"""
    lines = block.splitlines()
    wanted = unicodedata.normalize("NFKC", str(label))
    # 用可容納空白的角色比對，並從原始列擷取數字；不能把 PDF
    # 表格欄間空白先全部移除，否則 180  190 會變成 180190。
    label_pattern = r"\s*".join(re.escape(char) for char in wanted)
    for index, line in enumerate(lines):
        normalized_line = unicodedata.normalize("NFKC", line)
        label_match = re.search(label_pattern, normalized_line)
        if not label_match:
            continue
        candidate = normalized_line[label_match.end():]
        if len(NUMBER_RE.findall(candidate)) < count:
            candidate = "\n".join(
                [candidate, *lines[index + 1:index + 3]]
            )
        tokens = NUMBER_RE.findall(candidate)
        if len(tokens) >= count:
            return [_parse_number(token, field) for token in tokens[:count]]
    raise ValueError(f"PDF 找不到「{label}」列的 {count} 個數字（{field}）")


def _parse_age(label):
    """解析 A05 原始年齡組；未知標籤直接拋錯。"""
    compact = _compact(label)
    match = re.fullmatch(r"(\d{1,3})至(\d{1,3})歲", compact)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"青年局來源年齡區間上下界顛倒：{label!r}")
        return lower, upper
    match = re.fullmatch(r"(\d{1,3})[~～-](\d{1,3})歲", compact)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"青年局來源年齡區間上下界顛倒：{label!r}")
        return lower, upper
    match = re.fullmatch(r"未滿(\d{1,3})歲", compact)
    if match:
        return None, int(match.group(1)) - 1
    match = re.fullmatch(r"(\d{1,3})歲以上", compact)
    if match:
        return int(match.group(1)), None
    raise ValueError(f"無法解析青年局來源年齡標籤：{label!r}")


def _parse_a05(text, source):
    service_block = _find_section(
        text,
        r"表一\s*[、:]\s*諮詢服務統計表",
        r"資料來源\s*[:：]",
    )
    service_rows = {}
    for label in ("總計(人)", "男性(人)", "女性(人)"):
        service_rows[label] = _numbers_after_label(
            service_block, label, 2, f"A05 表一 {label}"
        )

    for column_index, column_name in ((0, "職涯諮詢"), (1, "履歷健診及模擬面試")):
        total = service_rows["總計(人)"][column_index]
        male = service_rows["男性(人)"][column_index]
        female = service_rows["女性(人)"][column_index]
        if abs(total - male - female) > 0.5:
            raise ValueError(
                f"A05 表一 {column_name} 性別對帳失敗：{total} != {male} + {female}"
            )

    age_block = _find_section(
        text,
        r"表三\s*[:：]\s*服務對象年齡層分布統計表",
        r"資料來源\s*[:：]",
    )
    age_labels = ("18 至 24 歲", "25 至 29 歲", "30 至 34 歲", "35 至 40 歲")
    age_rows = {}
    for label in age_labels:
        age_rows[label] = _numbers_after_label(
            age_block, label, 4, f"A05 表三 {label}"
        )

    service_age_totals = [0.0, 0.0, 0.0]
    for values in age_rows.values():
        if abs(values[0] + values[1] - values[2]) > 0.5:
            raise ValueError(
                f"A05 表三 {values} 服務合計對帳失敗："
                f"{values[0]} + {values[1]} != {values[2]}"
            )
        service_age_totals[0] += values[0]
        service_age_totals[1] += values[1]
        service_age_totals[2] += values[2]
    expected_service_totals = [
        service_rows["總計(人)"][0],
        service_rows["總計(人)"][1],
        sum(service_rows["總計(人)" ]),
    ]
    for actual, expected, label in zip(
        service_age_totals, expected_service_totals, ("職涯諮詢", "履歷健診", "合計")
    ):
        if abs(actual - expected) > 0.5:
            raise ValueError(
                f"A05 表三 {label} 對帳失敗：輸入 {expected}、年齡列加總 {actual}"
            )

    identity_block = _find_section(
        text,
        r"表四\s*[:：]\s*服務對象身分別統計表",
        r"資料來源\s*[:：]",
    )
    identity_labels = (
        "居住於新北市", "就學於新北市", "設籍於新北市", "就業於新北市", "其他"
    )
    identity_rows = {
        label: _numbers_after_label(
            identity_block, label, 2, f"A05 表四 {label}"
        )
        for label in identity_labels
    }
    identity_total = sum(values[0] for values in identity_rows.values())
    if abs(identity_total - sum(service_rows["總計(人)"])) > 0.5:
        raise ValueError(
            f"A05 表四對帳失敗：身份別加總 {identity_total}、"
            f"表一總計 {sum(service_rows['總計(人)'])}"
        )

    return {
        "service_rows": service_rows,
        "age_rows": age_rows,
        "identity_rows": identity_rows,
        "source_total": sum(service_rows["總計(人)"]),
    }


def _parse_a06_table(text, marker, roc_year, expected_months, source):
    block = _find_section(text, marker, r"資料來源\s*[:：]")
    rows = []
    for line in block.splitlines():
        match = re.match(r"^\s*(\d{1,2})\s+(.+?)\s+([\d,]+)\s*$", line)
        if not match:
            continue
        month = int(match.group(1))
        if month < 1 or month > 12:
            raise ValueError(f"A06 出現無效月份：{month}")
        course = match.group(2).strip()
        if not course:
            raise ValueError(f"A06 {roc_year} 年 {month} 月課程名稱為空")
        rows.append(
            {
                "roc_year": roc_year,
                "month": month,
                "course": course,
                "registrations": _parse_number(
                    match.group(3), f"A06 {roc_year} 年 {month} 月報名人數"
                ),
                "source_table": marker,
                "source": source,
            }
        )
    if len(rows) != expected_months or {row["month"] for row in rows} != set(
        range(1, expected_months + 1)
    ):
        raise ValueError(
            f"A06 {roc_year} 年課程列數／月份不符："
            f"輸入預期 {expected_months}、實際 {len(rows)}、"
            f"月份 {[row['month'] for row in rows]}"
        )
    return rows


def _parse_a06(text, source):
    return [
        *_parse_a06_table(text, r"【表1】114年度新北青創小聚辦理情形", 114, 12, source),
        *_parse_a06_table(text, r"【表2】115年度新北青創小聚辦理情形", 115, 6, source),
    ]


def _find_count(compact_text, patterns, field, human=False):
    for pattern in patterns:
        match = re.search(pattern, compact_text)
        if match:
            return (
                _parse_human_count(match.group(1), field)
                if human
                else _parse_number(match.group(1), field)
            )
    raise ValueError(f"PDF 找不到 {field} 的數字")


def _parse_a07(text, source):
    compact = _compact(text)
    total_cases = _find_count(
        compact, (r"補助總計([\d,]+)案",), "A07 補助案件總數"
    )
    total_participants = _find_count(
        compact, (r"參與總人次(?:為)?([\d,]+)人次",), "A07 參與人次總數"
    )
    overall_male = _find_count(
        compact, (r"其中男性參與者(?:為)?([\d,]+)人次",), "A07 男性總參與人次"
    )
    overall_female = _find_count(
        compact, (r"女性參與者為([\d,]+)人次",), "A07 女性總參與人次"
    )

    applicant_specs = (
        ("學校", (r"由學校.*?為([\d,]+)件",)),
        ("團體", (r"由團體.*?件數為([\d,]+)件",)),
        ("個人", (r"以個人.*?件數為([\d,]+)件",)),
    )
    applicants = {
        label: _find_count(compact, patterns, f"A07 {label}申請件數")
        for label, patterns in applicant_specs
    }
    if abs(sum(applicants.values()) - total_cases) > 0.5:
        raise ValueError(
            f"A07 申請對象對帳失敗：{sum(applicants.values())} != {total_cases}"
        )

    topics = ("社課", "志願服務", "競賽", "公共展演", "議題推廣", "人才培力", "大型會議")
    topic_cases = {
        topic: _find_count(
            compact,
            (rf"(?:以)?{re.escape(topic)}為主題(?:的(?:申請)?)?件數為([\d,]+)件",),
            f"A07 {topic}申請件數",
        )
        for topic in topics
    }
    if abs(sum(topic_cases.values()) - total_cases) > 0.5:
        raise ValueError(
            f"A07 主題申請件數對帳失敗：{sum(topic_cases.values())} != {total_cases}"
        )

    topic_participants = {
        topic: _find_count(
            compact,
            (rf"(?:以)?{re.escape(topic)}為主題的參與人次為([\d,]+)人次",),
            f"A07 {topic}參與人次",
        )
        for topic in topics
    }
    if abs(sum(topic_participants.values()) - total_participants) > 0.5:
        raise ValueError(
            f"A07 主題參與人次對帳失敗："
            f"{sum(topic_participants.values())} != {total_participants}"
        )
    if abs(total_participants - overall_male - overall_female) > 0.5:
        raise ValueError(
            f"A07 總參與人次性別對帳失敗："
            f"{total_participants} != {overall_male} + {overall_female}"
        )

    topic_gender = {}
    for topic in topics:
        pattern = (
            rf"(?:以)?{re.escape(topic)}為主題的性別統計分析"
            rf"[,，:：]總參與人次(?:為)?([\d,]+)人次"
            rf".*?男性參與者為([\d,]+)人(?:次)?"
            rf".*?女性參與者為([\d,]+)人(?:次)?"
        )
        match = re.search(pattern, compact)
        if not match:
            raise ValueError(f"A07 找不到 {topic} 性別統計列")
        values = [_parse_number(value, f"A07 {topic}性別統計") for value in match.groups()]
        if abs(values[0] - values[1] - values[2]) > 0.5:
            raise ValueError(f"A07 {topic} 性別對帳失敗：{values}")
        if abs(values[0] - topic_participants[topic]) > 0.5:
            raise ValueError(
                f"A07 {topic} 主題人次與性別統計不一致："
                f"{values[0]} != {topic_participants[topic]}"
            )
        topic_gender[topic] = values

    return {
        "total_cases": total_cases,
        "total_participants": total_participants,
        "overall_gender": (overall_male, overall_female),
        "applicants": applicants,
        "topic_cases": topic_cases,
        "topic_participants": topic_participants,
        "topic_gender": topic_gender,
        "source": source,
    }


def _parse_a08(text, source):
    compact = _compact(text)
    total_cases = _find_count(
        compact, (r"補助總計([\d,]+)案",), "A08 補助案件總數"
    )
    total_participants = _find_count(
        compact,
        (r"參與總人次為([^，。；]+?)人(?:次)?",),
        "A08 參與人次總數",
        human=True,
    )
    topics = ("社課", "公益", "競賽", "展演", "議題")
    topic_cases = {
        topic: _find_count(
            compact,
            (rf"(?:以)?{re.escape(topic)}為主題(?:的(?:申請)?)?件數為([\d,]+)件",),
            f"A08 {topic}申請件數",
        )
        for topic in topics
    }
    if abs(sum(topic_cases.values()) - total_cases) > 0.5:
        raise ValueError(
            f"A08 主題申請件數對帳失敗：{sum(topic_cases.values())} != {total_cases}"
        )

    participant_patterns = {
        "展演": (r"(?:以)?展演為主題的參與人次為最多[,，:：]?總共([^,，。；;]+?)人次",),
        "社課": (r"(?:以)?社課為主題的參與人次為([^,，。；;]+?)人次",),
        "公益": (r"(?:以)?公益為主題的參與人次(?:為|最少[,，]?為)([^,，。；;]+?)人次",),
        "競賽": (r"(?:以)?競賽為主題的參與人次為([^,，。；;]+?)人次",),
        "議題": (r"(?:以)?議題為主題的參與人次為([^,，。；;]+?)人次",),
    }
    topic_participants = {
        topic: _find_count(compact, (pattern,), f"A08 {topic}參與人次", human=True)
        for topic, (pattern,) in participant_patterns.items()
    }
    if abs(sum(topic_participants.values()) - total_participants) > 0.5:
        raise ValueError(
            f"A08 主題參與人次對帳失敗："
            f"{sum(topic_participants.values())} != {total_participants}"
        )

    # 112 年報文字寫「學校86、團體10、個人12」，合計只有108，
    # 但報告總案數與主題分項均為111。這個矛盾的申請對象小節不入庫，
    # 避免用猜測補出缺少的3案；其餘兩個可對帳小節照常輸出。
    applicant_match = re.search(
        r"由學校.*?為([\d,]+)件.*?團體.*?件數為([\d,]+)件.*?"
        r"個人.*?件數為([\d,]+)件",
        compact,
    )
    applicant_anomaly = None
    if applicant_match:
        applicant_values = [_parse_number(value, "A08 申請對象") for value in applicant_match.groups()]
        applicant_anomaly = {
            "values": applicant_values,
            "sum": sum(applicant_values),
            "reported_total": total_cases,
            "reconciled": abs(sum(applicant_values) - total_cases) <= 0.5,
        }

    return {
        "total_cases": total_cases,
        "total_participants": total_participants,
        "topic_cases": topic_cases,
        "topic_participants": topic_participants,
        "applicant_anomaly": applicant_anomaly,
        "source": source,
    }


def _annual_period(roc_year):
    year = roc_year + 1911
    return f"{year}-01-01", f"{year}-12-31", "year"


def _monthly_period(roc_year, month):
    year = roc_year + 1911
    last_day = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}", "month"


def _fact_row(
    *,
    source,
    period_start,
    period_end,
    period_type,
    indicator_id,
    age_lower,
    age_upper,
    age_band_raw,
    gender,
    breakdown,
    value,
    unit,
    data_time,
):
    return {
        "indicator_id": indicator_id,
        "period_start": period_start,
        "period_end": period_end,
        "period_type": period_type,
        "age_lower": age_lower,
        "age_upper": age_upper,
        "age_band_raw": age_band_raw,
        "gender": gender,
        "area_code": "65000",
        "area_level": "city",
        "breakdown": json.dumps(
            {
                "source_id": source["source_id"],
                "source_pdf": source["url"],
                **breakdown,
            },
            ensure_ascii=False,
        ),
        "value": float(value),
        "unit": unit,
        "value_type": "count",
        "data_time": data_time,
    }


def _a05_rows(parsed, source, data_time):
    start, end, period_type = _annual_period(source["roc_year"])
    rows = []
    service_names = ("職涯諮詢", "履歷健診及模擬面試")
    for column_index, service in enumerate(service_names):
        for gender, source_label, position in (
            ("total", "總計(人)", 0),
            ("male", "男性(人)", 0),
            ("female", "女性(人)", 0),
        ):
            rows.append(
                _fact_row(
                    source=source,
                    period_start=start,
                    period_end=end,
                    period_type=period_type,
                    indicator_id="youth_bureau_career_service_count",
                    age_lower=None,
                    age_upper=None,
                    age_band_raw=None,
                    gender=gender,
                    breakdown={
                        "metric": "service_users",
                        "service": service,
                        "source_row": source_label,
                        "age_coverage": "unavailable",
                    },
                    value=parsed["service_rows"][source_label][column_index],
                    unit="人",
                    data_time=data_time,
                )
            )

    for raw_label, values in parsed["age_rows"].items():
        lower, upper = _parse_age(raw_label)
        for service, value in zip(service_names + ("合計",), values[:3]):
            rows.append(
                _fact_row(
                    source=source,
                    period_start=start,
                    period_end=end,
                    period_type=period_type,
                    indicator_id="youth_bureau_career_age_group_count",
                    age_lower=lower,
                    age_upper=upper,
                    age_band_raw=raw_label,
                    gender="total",
                    breakdown={
                        "metric": "service_users",
                        "service": service,
                        "age_coverage": "apportioned",
                        "apportionment": "query_only; no 18-35 split in ETL",
                    },
                    value=value,
                    unit="人",
                    data_time=data_time,
                )
            )

    for label, values in parsed["identity_rows"].items():
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_career_identity_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender="total",
                breakdown={
                    "metric": "service_users",
                    "identity": label,
                    "age_coverage": "unavailable",
                },
                value=values[0],
                unit="人",
                data_time=data_time,
            )
        )
    return rows


def _a06_rows(parsed, source, data_time):
    rows = []
    for item in parsed:
        start, end, period_type = _monthly_period(item["roc_year"], item["month"])
        common = {
            "roc_year": item["roc_year"],
            "source_table": item["source_table"],
            "month": item["month"],
            "course_name": item["course"],
            "age_coverage": "unavailable",
        }
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_entrepreneurship_course_registration_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender="total",
                breakdown={"metric": "registrations", **common},
                value=item["registrations"],
                # 來源欄名是「報名人數」，不是 A07/A08 的「參與人次」；
                # 兩者分開用不同 indicator_id 與 unit，避免下游誤加總。
                unit="人數",
                data_time=data_time,
            )
        )
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_entrepreneurship_course_session_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender="total",
                breakdown={"metric": "course_sessions", **common},
                value=1,
                unit="場",
                data_time=data_time,
            )
        )
    return rows


def _a07_rows(parsed, source, data_time):
    start, end, period_type = _annual_period(source["roc_year"])
    rows = []

    for label, value in parsed["applicants"].items():
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_grant_application_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender="total",
                breakdown={
                    "metric": "application_cases",
                    "dimension": "applicant_type",
                    "category": label,
                    "age_coverage": "unavailable",
                },
                value=value,
                unit="案",
                data_time=data_time,
            )
        )
    for label, value in parsed["topic_cases"].items():
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_grant_application_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender="total",
                breakdown={
                    "metric": "application_cases",
                    "dimension": "topic",
                    "category": label,
                    "age_coverage": "unavailable",
                },
                value=value,
                unit="案",
                data_time=data_time,
            )
        )

    total_male, total_female = parsed["overall_gender"]
    rows.append(
        _fact_row(
            source=source,
            period_start=start,
            period_end=end,
            period_type=period_type,
            indicator_id="youth_bureau_grant_participant_count",
            age_lower=None,
            age_upper=None,
            age_band_raw=None,
            gender="total",
            breakdown={"metric": "participants", "dimension": "overall", "category": "total", "age_coverage": "unavailable"},
            value=parsed["total_participants"],
            unit="人次",
            data_time=data_time,
        )
    )
    for gender, value in (("male", total_male), ("female", total_female)):
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_grant_participant_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender=gender,
                breakdown={"metric": "participants", "dimension": "overall", "category": "total", "age_coverage": "unavailable"},
                value=value,
                unit="人次",
                data_time=data_time,
            )
        )
    for topic, value in parsed["topic_participants"].items():
        male, female = parsed["topic_gender"][topic][1:]
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_grant_participant_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender="total",
                breakdown={"metric": "participants", "dimension": "topic", "category": topic, "age_coverage": "unavailable"},
                value=value,
                unit="人次",
                data_time=data_time,
            )
        )
        for gender, gender_value in (("male", male), ("female", female)):
            rows.append(
                _fact_row(
                    source=source,
                    period_start=start,
                    period_end=end,
                    period_type=period_type,
                    indicator_id="youth_bureau_grant_participant_count",
                    age_lower=None,
                    age_upper=None,
                    age_band_raw=None,
                    gender=gender,
                    breakdown={"metric": "participants", "dimension": "topic", "category": topic, "age_coverage": "unavailable"},
                    value=gender_value,
                    unit="人次",
                    data_time=data_time,
                )
            )
    return rows


def _a08_rows(parsed, source, data_time):
    start, end, period_type = _annual_period(source["roc_year"])
    rows = []
    for topic, value in parsed["topic_cases"].items():
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_grant_application_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender="total",
                breakdown={
                    "metric": "application_cases",
                    "dimension": "topic",
                    "category": topic,
                    "age_coverage": "unavailable",
                },
                value=value,
                unit="案",
                data_time=data_time,
            )
        )
    rows.append(
        _fact_row(
            source=source,
            period_start=start,
            period_end=end,
            period_type=period_type,
            indicator_id="youth_bureau_grant_participant_count",
            age_lower=None,
            age_upper=None,
            age_band_raw=None,
            gender="total",
            breakdown={"metric": "participants", "dimension": "overall", "category": "total", "age_coverage": "unavailable"},
            value=parsed["total_participants"],
            unit="人次",
            data_time=data_time,
        )
    )
    for topic, value in parsed["topic_participants"].items():
        rows.append(
            _fact_row(
                source=source,
                period_start=start,
                period_end=end,
                period_type=period_type,
                indicator_id="youth_bureau_grant_participant_count",
                age_lower=None,
                age_upper=None,
                age_band_raw=None,
                gender="total",
                breakdown={"metric": "participants", "dimension": "topic", "category": topic, "age_coverage": "unavailable"},
                value=value,
                unit="人次",
                data_time=data_time,
            )
        )
    return rows


def _check_scale_anomalies(totals):
    """對同一可比序列做 3 倍尺度防護；樣本不足三期則明確記錄而不臆測。"""
    grouped = {}
    for item in totals:
        grouped.setdefault(item["series"], {})[item["year"]] = float(item["value"])
    for series, values in sorted(grouped.items()):
        comparable = {year: value for year, value in values.items() if year not in KNOWN_BAD_YEARS}
        if len(comparable) < 3:
            print(f"bureau scale guard: {series} only {len(comparable)} comparable period(s); no median test")
            continue
        for year, value in sorted(comparable.items()):
            peers = sorted(v for other_year, v in comparable.items() if other_year != year)
            median = peers[len(peers) // 2]
            if median and (value > median * SCALE_ANOMALY_FACTOR or value * SCALE_ANOMALY_FACTOR < median):
                raise ValueError(
                    f"青年局 {series} {year} value={value:.0f} 與其餘年度中位數 "
                    f"{median:.0f} 相差超過 {SCALE_ANOMALY_FACTOR} 倍；"
                    "請先確認統計期間／範圍，再加入明確 KNOWN_BAD_YEARS。"
                )


def build_facts(fetch_pdf=None, data_time=None):
    """實抓 A05--A08，回傳 (fact DataFrame, reconciliation audit)。"""
    import pandas as pd

    if fetch_pdf is None:
        fetch_pdf = _fetch_pdf
    if data_time is None:
        from utils.get_time import get_tpe_now_time_str

        data_time = get_tpe_now_time_str(is_with_tz=True)

    rows = []
    audit = []
    scale_totals = []
    for source in PDF_SOURCES:
        text = _pdf_to_text(fetch_pdf(source), source["name"])
        if source["source_id"] == "A05":
            parsed = _parse_a05(text, source)
            rows.extend(_a05_rows(parsed, source, data_time))
            age_totals = [sum(values[i] for values in parsed["age_rows"].values()) for i in range(3)]
            for label, actual, expected in (
                ("service_users_career", age_totals[0], parsed["service_rows"]["總計(人)"][0]),
                ("service_users_resume", age_totals[1], parsed["service_rows"]["總計(人)"][1]),
                ("service_users_total", age_totals[2], parsed["source_total"]),
            ):
                audit.append({"source_id": "A05", "section": label, "input_total": expected, "output_total": actual, "reconciled": abs(actual - expected) <= 0.5, "included": True})
                if abs(actual - expected) > 0.5:
                    raise ValueError(f"A05 {label} 輸入／輸出對帳失敗：{expected} != {actual}")
            identity_total = sum(values[0] for values in parsed["identity_rows"].values())
            audit.append({"source_id": "A05", "section": "identity", "input_total": parsed["source_total"], "output_total": identity_total, "reconciled": abs(identity_total - parsed["source_total"]) <= 0.5, "included": True})
            scale_totals.append({"series": "A05_service_users", "year": 2024, "value": parsed["source_total"]})
        elif source["source_id"] == "A06":
            parsed = _parse_a06(text, source)
            emitted_rows = _a06_rows(parsed, source, data_time)
            rows.extend(emitted_rows)
            for roc_year in (114, 115):
                subset = [item for item in parsed if item["roc_year"] == roc_year]
                expected = sum(item["registrations"] for item in subset)
                emitted = sum(
                    row["value"] for row in emitted_rows
                    if json.loads(row["breakdown"])["metric"] == "registrations"
                    and json.loads(row["breakdown"])["roc_year"] == roc_year
                )
                audit.append({"source_id": "A06", "section": f"course_registrations_{roc_year}", "input_total": expected, "output_total": emitted, "reconciled": abs(emitted - expected) <= 0.5, "included": True})
                if abs(emitted - expected) > 0.5:
                    raise ValueError(f"A06 {roc_year} 課程報名人數對帳失敗：{expected} != {emitted}")
                # 114 完整年度與 115 半年期不是同一可比序列。
                scale_totals.append({"series": f"A06_course_registrations_{roc_year}_coverage", "year": roc_year, "value": expected})
        elif source["source_id"] == "A07":
            parsed = _parse_a07(text, source)
            rows.extend(_a07_rows(parsed, source, data_time))
            audit.extend(
                [
                    {"source_id": "A07", "section": "applicant_types", "input_total": parsed["total_cases"], "output_total": sum(parsed["applicants"].values()), "reconciled": True, "included": True},
                    {"source_id": "A07", "section": "topic_application_cases", "input_total": parsed["total_cases"], "output_total": sum(parsed["topic_cases"].values()), "reconciled": True, "included": True},
                    {"source_id": "A07", "section": "topic_participants", "input_total": parsed["total_participants"], "output_total": sum(parsed["topic_participants"].values()), "reconciled": True, "included": True},
                    {"source_id": "A07", "section": "overall_gender", "input_total": parsed["total_participants"], "output_total": sum(parsed["overall_gender"]), "reconciled": True, "included": True},
                ]
            )
            scale_totals.append({"series": "A07_grant_application_cases", "year": 2024, "value": parsed["total_cases"]})
        elif source["source_id"] == "A08":
            parsed = _parse_a08(text, source)
            rows.extend(_a08_rows(parsed, source, data_time))
            audit.extend(
                [
                    {"source_id": "A08", "section": "topic_application_cases", "input_total": parsed["total_cases"], "output_total": sum(parsed["topic_cases"].values()), "reconciled": True, "included": True},
                    {"source_id": "A08", "section": "topic_participants", "input_total": parsed["total_participants"], "output_total": sum(parsed["topic_participants"].values()), "reconciled": True, "included": True},
                ]
            )
            scale_totals.append({"series": "A08_grant_application_cases", "year": 2023, "value": parsed["total_cases"]})
            if parsed["applicant_anomaly"]:
                anomaly = parsed["applicant_anomaly"]
                audit.append({"source_id": "A08", "section": "applicant_types", "input_total": anomaly["reported_total"], "output_total": anomaly["sum"], "reconciled": False, "included": False, "omitted_reason": "報告文字的學校86+團體10+個人12=108，但報告總案數與主題分項為111；不猜補缺少3案。"})

    _check_scale_anomalies(scale_totals)
    if not rows:
        raise RuntimeError("青年局 A05--A08 PDF 沒有產出任何事實列")
    data = pd.DataFrame(rows, columns=CONTRACT_COLUMNS)
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    if list(data.columns) != CONTRACT_COLUMNS:
        raise ValueError(f"事實表欄位不符合契約：{list(data.columns)}")
    for column in ("indicator_id", "period_start", "period_end", "period_type", "gender", "area_code", "area_level", "breakdown", "value", "unit", "value_type", "data_time"):
        if data[column].isna().any():
            raise ValueError(f"事實表契約欄位 {column} 出現 NULL")
    return data, audit


def _transfer(**kwargs):
    from sqlalchemy import create_engine
    from utils.load_stage import (
        save_dataframe_to_postgresql,
        update_lasttime_in_data_to_dataset_info,
    )

    ready_data_db_uri = kwargs.get("ready_data_db_uri")
    dag_infos = kwargs.get("dag_infos")
    data, audit = build_facts()
    for item in audit:
        print(
            f"{item['source_id']} {item['section']} input={item['input_total']} "
            f"output={item['output_total']} reconciled={item['reconciled']}"
        )
    print(f"ready_data shape =========== {data.shape}")
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
    dag_folder="youth_bureau_program_stats_ntpc",
)
dag.create_dag(etl_func=_transfer)
