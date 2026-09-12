"""MOL initial-salary history extracted from the official 110--113 PDFs.

The fetch and PDF parsing functions are deliberately usable by the companion
verification script without importing Airflow.
"""

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from decimal import Decimal
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

try:
    from operators.common_pipeline import CommonDag
except ImportError:  # pragma: no cover - allows local verification without Airflow
    CommonDag = None


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

PUBLISHED_PAGES = {
    110: "https://www.mol.gov.tw/1607/1632/1640/50679/",
    111: "https://www.mol.gov.tw/1607/1632/1640/58627/post",
    112: "https://www.mol.gov.tw/1607/1632/1640/68628/",
    113: "https://www.mol.gov.tw/1607/1632/1640/80217/",
}
EXPECTED_YEARS = tuple(sorted(PUBLISHED_PAGES))

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

EDUCATION_LEVELS = (
    "全體",
    "國中",
    "高級中等（高中、高職）",
    "專科",
    "大學",
    "研究所",
)

EDUCATION_ROW_PREFIXES = {
    "全體": ("全體",),
    "國中": ("國中",),
    # The 110 PDF wraps the parenthetical label onto the next line.
    "高級中等（高中、高職）": ("高級中等（高中、高職）", "高級中等"),
    "專科": ("專科",),
    "大學": ("大學",),
    "研究所": ("研究所",),
}

MEDIAN_EDUCATION_LEVELS_110 = (
    "全體",
    "高級中等（高中、高職）",
    "大專（專科及大學）",
    "研究所",
)

MEDIAN_ROW_PREFIXES = {
    "全體": ("全體",),
    "高級中等（高中、高職）": ("高級中等（高中、高職）", "高級中等"),
    "大專（專科及大學）": ("大專（專科及大學）", "大專"),
    "研究所": ("研究所",),
}

# No bad year was found in the four official PDFs. Keep this explicit so a
# later confirmed scope issue has a named place to be documented and excluded.
KNOWN_BAD_YEARS = {}
SCALE_ANOMALY_FACTOR = 3.0

_NUMBER_RE = re.compile(
    r"(?<![\w.])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
)
_YEAR_PREFIX_RE = re.compile(r"^\s*(\d{3})\s*年")
_PDF_HREF_RE = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.I)


def _number_tokens(text):
    return [Decimal(token.replace(",", "")) for token in _NUMBER_RE.findall(text)]


def _find_marker(lines, predicate, description):
    for index, line in enumerate(lines):
        if predicate(line):
            return index
    raise ValueError(f"PDF 找不到表格標記：{description}")


def _find_numeric_row(lines, prefixes, start, end, *, minimum_numbers, description):
    for index in range(start, end):
        line = lines[index]
        stripped = line.lstrip()
        if not any(stripped.startswith(prefix) for prefix in prefixes):
            continue
        numbers = _number_tokens(line)
        if len(numbers) >= minimum_numbers:
            return numbers
        # Poppler's layout output can put a long label and its numeric cells on
        # adjacent lines (the 110 high-school row is one such table).  Only
        # bridge an immediately following line; a wider search could associate
        # a different row's values with the label.
        if index + 1 < end:
            next_numbers = _number_tokens(lines[index + 1])
            if len(next_numbers) >= minimum_numbers:
                return next_numbers
    raise ValueError(f"PDF 找不到資料列：{description}")


def _period_fields(roc_year):
    if roc_year < 100 or roc_year > 999:
        raise ValueError(f"PDF 民國年度超出合理範圍：{roc_year}")
    year = roc_year + 1911
    return f"{year:04d}-01-01", f"{year:04d}-12-31"


def _http_bytes(url, *, accept="text/html,application/xhtml+xml,*/*", timeout=180):
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": accept,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get_content_type()
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"官方來源請求失敗 {url}: {exc}") from exc


def _normalise_url(url):
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/%")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def resolve_pdf_url(roc_year):
    """Resolve the current PDF href from the year's server-rendered page."""
    try:
        page_url = PUBLISHED_PAGES[roc_year]
    except KeyError as exc:
        raise ValueError(f"沒有設定民國 {roc_year} 年官方發布頁") from exc

    page_bytes, _ = _http_bytes(page_url)
    page_text = page_bytes.decode("utf-8-sig", errors="replace")
    candidates = []
    for raw_href in _PDF_HREF_RE.findall(page_text):
        href = unescape(raw_href)
        full_url = urljoin(page_url, href)
        decoded = unquote(full_url)
        if ".pdf" not in full_url.lower():
            continue
        if "初任" not in decoded or "薪資" not in decoded:
            continue
        normalised = _normalise_url(full_url)
        if normalised not in candidates:
            candidates.append(normalised)

    if len(candidates) != 1:
        raise RuntimeError(
            f"民國 {roc_year} 年發布頁找到 {len(candidates)} 個初任薪資 PDF，"
            f"預期唯一：{candidates}"
        )
    return candidates[0]


def _pdf_to_text(pdf_bytes, *, roc_year):
    if not pdf_bytes.startswith(b"%PDF"):
        raise RuntimeError(
            f"民國 {roc_year} 年下載內容不是 PDF（缺少 %PDF magic bytes），"
            "可能是政府網站驗證頁。"
        )

    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise RuntimeError(
            "找不到 pdftotext；Airflow 映像需要安裝 poppler-utils，"
            "請確認 docker/develop/Dockerfile 與 docker/prod/Dockerfile。"
        )

    with tempfile.TemporaryDirectory(prefix="salary_pdf_") as temp_dir:
        pdf_path = f"{temp_dir}/source.pdf"
        text_path = f"{temp_dir}/source.txt"
        with open(pdf_path, "wb") as output:
            output.write(pdf_bytes)
        try:
            subprocess.run(
                [pdftotext, "-layout", "-enc", "UTF-8", pdf_path, text_path],
                check=True,
                capture_output=True,
                timeout=180,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"pdftotext 解析民國 {roc_year} 年 PDF 失敗：{stderr[-500:]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"pdftotext 解析民國 {roc_year} 年 PDF 逾時") from exc
        with open(text_path, "r", encoding="utf-8") as source:
            text = source.read()

    if not text.strip() or "\ufffd" in text:
        raise RuntimeError(f"民國 {roc_year} 年 PDF 沒有可用文字層")
    return text


def fetch_pdf_texts():
    """Fetch all four official pages/PDFs and return text plus diagnostics."""
    texts = {}
    diagnostics = {}
    for roc_year in EXPECTED_YEARS:
        pdf_url = resolve_pdf_url(roc_year)
        pdf_bytes, content_type = _http_bytes(
            pdf_url,
            accept="application/pdf,*/*",
            timeout=240,
        )
        if content_type != "application/pdf":
            raise RuntimeError(
                f"民國 {roc_year} 年 PDF Content-Type 異常：{content_type}；"
                "拒絕把 HTML 驗證頁送進 parser。"
            )
        text = _pdf_to_text(pdf_bytes, roc_year=roc_year)
        texts[roc_year] = text
        diagnostics[roc_year] = {
            "page_url": PUBLISHED_PAGES[roc_year],
            "pdf_url": pdf_url,
            "content_type": content_type,
            "pdf_bytes": len(pdf_bytes),
            "text_chars": len(text),
        }
    return texts, diagnostics


def _parse_110(text):
    lines = text.splitlines()
    appendix_start = _find_marker(
        lines,
        lambda line: "人數及結構比" in line,
        "110 年附表 1",
    )
    appendix_end = _find_marker(
        lines[appendix_start:],
        lambda line: "說明" in line,
        "110 年附表 1 說明",
    ) + appendix_start

    share = {}
    for education in EDUCATION_LEVELS:
        numbers = _find_numeric_row(
            lines,
            EDUCATION_ROW_PREFIXES[education],
            appendix_start,
            appendix_end,
            minimum_numbers=2,
            description=f"110 年教育程度結構比 {education}",
        )
        share[education] = numbers[1]

    mean_start = _find_marker(
        lines,
        lambda line: "薪資相關統計" in line,
        "110 年薪資平均數表",
    )
    mean_end = _find_marker(
        lines[mean_start:],
        lambda line: "（二）女性" in line,
        "110 年薪資平均數表結尾",
    ) + mean_start
    mean = {}
    for education in EDUCATION_LEVELS:
        numbers = _find_numeric_row(
            lines,
            EDUCATION_ROW_PREFIXES[education],
            mean_start,
            mean_end,
            minimum_numbers=2,
            description=f"110 年薪資平均數 {education}",
        )
        mean[education] = numbers[0]

    distribution_start = _find_marker(
        lines,
        lambda line: re.search(r"表\s*2\s*.*薪資分布", line) is not None,
        "110 年薪資分布表",
    )
    distribution_end = _find_marker(
        lines[distribution_start:],
        lambda line: "說明" in line,
        "110 年薪資分布表說明",
    ) + distribution_start
    median = {}
    for education in MEDIAN_EDUCATION_LEVELS_110:
        numbers = _find_numeric_row(
            lines,
            MEDIAN_ROW_PREFIXES[education],
            distribution_start,
            distribution_end,
            minimum_numbers=6,
            description=f"110 年薪資中位數 {education}",
        )
        median[education] = {
            "d1": numbers[0],
            "q1": numbers[1],
            "median": numbers[2],
            "q3": numbers[3],
            "d9": numbers[4],
            "mean": numbers[5],
            "source_field": "表2 初任人員薪資分布",
        }

    return {
        "share": share,
        "mean": mean,
        "median": median,
        "source_fields": {
            "share": "附表1 初任人員人數及結構比",
            "mean": "表1 初任人員薪資相關統計",
        },
    }


def _year_row_numbers(lines, start, metric, roc_year):
    headings = ("人數結構比", "薪資平均數", "薪資中位數", "初任薪資為基本工資")
    metric_start = _find_marker(
        lines[start + 1:],
        lambda line: line.strip().startswith(metric),
        f"{roc_year} 年 {metric} 表頭",
    ) + start + 1
    metric_end = len(lines)
    for index in range(metric_start + 1, len(lines)):
        if any(
            lines[index].strip().startswith(other)
            for other in headings
            if other != metric
        ):
            metric_end = index
            break

    for line in lines[metric_start + 1 : metric_end]:
        match = _YEAR_PREFIX_RE.match(line)
        if not match or int(match.group(1)) != roc_year:
            continue
        numbers = _number_tokens(line[match.end() :])
        if len(numbers) >= 10:
            return numbers[:10]
    raise ValueError(f"PDF 找不到民國 {roc_year} 年 {metric} 數值列")


def _parse_112_or_113(text, roc_year):
    lines = text.splitlines()
    table_start = _find_marker(
        lines,
        lambda line: "人數結構比及薪資" in line and "1/3" in line,
        f"{roc_year} 年附表 1",
    )

    column_index = {
        "全體": 0,
        "國中": 3,
        "高級中等（高中、高職）": 4,
        "專科": 5,
        "大學": 6,
        "研究所": 7,
    }
    parsed = {"share": {}, "mean": {}, "median": {}}
    for metric, output_key in (
        ("人數結構比", "share"),
        ("薪資平均數", "mean"),
        ("薪資中位數", "median"),
    ):
        numbers = _year_row_numbers(lines, table_start, metric, roc_year)
        for education, index in column_index.items():
            if output_key == "median":
                parsed[output_key][education] = {
                    "d1": None,
                    "q1": None,
                    "median": numbers[index],
                    "q3": None,
                    "d9": None,
                    "mean": None,
                    "source_field": "附表1 薪資中位數",
                }
            else:
                parsed[output_key][education] = numbers[index]

    return {
        **parsed,
        "source_fields": {
            "share": "附表1 人數結構比（%）",
            "mean": "附表1 薪資平均數（千元）",
            "median": "附表1 薪資中位數（千元）",
        },
    }


def parse_pdf_text(roc_year, text):
    if roc_year == 110:
        return _parse_110(text)
    if roc_year == 111:
        lines = text.splitlines()
        table_start = _find_marker(
            lines,
            lambda line: "人數及薪資" in line,
            "111 年附表 1",
        )
        table_end = _find_marker(
            lines[table_start:],
            lambda line: "說明：1." in line,
            "111 年附表 1 說明",
        ) + table_start
        parsed = {"share": {}, "mean": {}, "median": {}}
        for education in EDUCATION_LEVELS:
            numbers = _find_numeric_row(
                lines,
                EDUCATION_ROW_PREFIXES[education],
                table_start,
                table_end,
                minimum_numbers=10,
                description=f"111 年薪資附表 {education}",
            )
            parsed["share"][education] = numbers[2]
            parsed["mean"][education] = numbers[4]
            parsed["median"][education] = {
                "d1": None,
                "q1": None,
                "median": numbers[7],
                "q3": None,
                "d9": None,
                "mean": None,
                "source_field": "附表1 薪資中位數（千元）",
            }
        parsed["source_fields"] = {
            "share": "附表1 人數結構比（%）",
            "mean": "附表1 薪資平均數（千元）",
            "median": "附表1 薪資中位數（千元）",
        }
        return parsed
    if roc_year in (112, 113):
        return _parse_112_or_113(text, roc_year)
    raise ValueError(f"未支援的 PDF 民國年度：{roc_year}")


def _annual_scale_values(parsed_by_year):
    values = {}
    for roc_year, parsed in parsed_by_year.items():
        try:
            value = parsed["mean"]["全體"]
        except KeyError as exc:
            raise ValueError(f"民國 {roc_year} 年缺少全體薪資平均數") from exc
        if value <= 0:
            raise ValueError(f"民國 {roc_year} 年全體薪資平均數無效：{value}")
        values[roc_year] = value
    return values


def drop_scale_anomalies(parsed_by_year):
    """Exclude named bad years and fail loud on an unknown 3x scale jump."""
    values = _annual_scale_values(parsed_by_year)
    kept = {year: value for year, value in values.items() if year not in KNOWN_BAD_YEARS}
    baseline = None
    if len(kept) >= 3:
        ordered = sorted(kept.values())
        baseline = ordered[len(ordered) // 2]
        for year, value in sorted(kept.items()):
            if value > baseline * Decimal(str(SCALE_ANOMALY_FACTOR)) or (
                value * Decimal(str(SCALE_ANOMALY_FACTOR)) < baseline
            ):
                raise ValueError(
                    f"民國 {year} 年全體薪資平均數 {value} 千元，"
                    f"與其餘年度尺度中位數 {baseline} 千元相差超過 "
                    f"{SCALE_ANOMALY_FACTOR} 倍；請先確認 PDF 口徑。"
                )

    for year, reason in sorted(KNOWN_BAD_YEARS.items()):
        if year in parsed_by_year:
            print(f"salary PDF scope guard =========== 排除民國 {year} 年：{reason}")

    excluded = set(KNOWN_BAD_YEARS)
    remaining = {
        year: parsed
        for year, parsed in parsed_by_year.items()
        if year not in excluded
    }
    return remaining, {
        "annual_mean_thousand": {str(k): float(v) for k, v in sorted(values.items())},
        "scale_baseline_thousand": float(baseline) if baseline is not None else None,
        "excluded_years": sorted(excluded.intersection(parsed_by_year)),
    }


def _as_json_number(value):
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _yuan(thousand):
    return thousand * Decimal("1000")


def _fact_base(roc_year, data_time):
    period_start, period_end = _period_fields(roc_year)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "period_type": "year",
        "age_lower": None,
        "age_upper": None,
        "age_band_raw": None,
        "gender": "total",
        "area_code": "TW",
        "area_level": "country",
        "data_time": data_time,
    }


def _breakdown(roc_year, education, source_field, source_value, *, extra=None):
    payload = {
        "education_level": education,
        "characteristic": "全體",
        "source": "MOL official initial-salary PDF",
        "source_format": "pdf",
        "source_page": PUBLISHED_PAGES[roc_year],
        "source_year_roc": roc_year,
        "source_field": source_field,
        "source_value_thousand": _as_json_number(source_value),
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def transform_parsed(parsed_by_year, *, data_time):
    parsed_by_year, scale_diagnostics = drop_scale_anomalies(parsed_by_year)
    if not parsed_by_year:
        raise RuntimeError("尺度防護後沒有可輸出的薪資年度")

    rows = []
    selected_counts = {"share": 0, "mean": 0, "median": 0}
    input_sums = {
        "share": Decimal("0"),
        "mean_yuan": Decimal("0"),
        "median_yuan": Decimal("0"),
    }
    emitted_sums = {
        "share": Decimal("0"),
        "mean_yuan": Decimal("0"),
        "median_yuan": Decimal("0"),
    }

    for roc_year, parsed in sorted(parsed_by_year.items()):
        base = _fact_base(roc_year, data_time)
        for education in EDUCATION_LEVELS:
            share = parsed["share"].get(education)
            if share is None:
                raise ValueError(f"民國 {roc_year} 年缺少教育程度結構比：{education}")
            rows.append(
                dict(
                    base,
                    indicator_id="youth_salary_initial_share_by_education",
                    breakdown=_breakdown(
                        roc_year,
                        education,
                        parsed["source_fields"]["share"],
                        share,
                    ),
                    value=_as_json_number(share),
                    unit="%",
                    value_type="rate",
                )
            )
            selected_counts["share"] += 1
            input_sums["share"] += share
            emitted_sums["share"] += share

            mean = parsed["mean"].get(education)
            if mean is None:
                raise ValueError(f"民國 {roc_year} 年缺少教育程度薪資平均數：{education}")
            mean_yuan = _yuan(mean)
            rows.append(
                dict(
                    base,
                    indicator_id="youth_salary_initial_mean_by_education",
                    breakdown=_breakdown(
                        roc_year,
                        education,
                        parsed["source_fields"]["mean"],
                        mean,
                        extra={"source_unit": "千元", "output_unit": "元"},
                    ),
                    value=_as_json_number(mean_yuan),
                    unit="元",
                    value_type="mean",
                )
            )
            selected_counts["mean"] += 1
            input_sums["mean_yuan"] += mean_yuan
            emitted_sums["mean_yuan"] += mean_yuan

        for education, distribution in parsed["median"].items():
            median = distribution.get("median")
            if median is None:
                raise ValueError(f"民國 {roc_year} 年薪資中位數空白：{education}")
            median_yuan = _yuan(median)
            extra = {
                "source_unit": "千元",
                "output_unit": "元",
                "d1_thousand": _as_json_number(distribution.get("d1")),
                "q1_thousand": _as_json_number(distribution.get("q1")),
                "q3_thousand": _as_json_number(distribution.get("q3")),
                "d9_thousand": _as_json_number(distribution.get("d9")),
                "mean_thousand_in_distribution": _as_json_number(
                    distribution.get("mean")
                ),
            }
            rows.append(
                dict(
                    base,
                    indicator_id="youth_salary_initial_median_by_education",
                    breakdown=_breakdown(
                        roc_year,
                        education,
                        distribution["source_field"],
                        median,
                        extra=extra,
                    ),
                    value=_as_json_number(median_yuan),
                    unit="元",
                    value_type="median",
                )
            )
            selected_counts["median"] += 1
            input_sums["median_yuan"] += median_yuan
            emitted_sums["median_yuan"] += median_yuan

    expected_counts = {
        "share": sum(len(parsed["share"]) for parsed in parsed_by_year.values()),
        "mean": sum(len(parsed["mean"]) for parsed in parsed_by_year.values()),
        "median": sum(len(parsed["median"]) for parsed in parsed_by_year.values()),
    }
    if selected_counts != expected_counts:
        raise ValueError(
            f"PDF 薪資對帳失敗：選定來源格數 {expected_counts}，"
            f"輸出列數 {selected_counts} 不一致。"
        )
    if input_sums != emitted_sums:
        raise ValueError(
            f"PDF 薪資數值對帳失敗：輸入 {input_sums}，輸出 {emitted_sums}"
        )
    if not rows:
        raise RuntimeError("PDF 薪資沒有產出任何資料列")

    return rows, {
        "selected_counts": selected_counts,
        "emitted_rows": len(rows),
        "input_sums": {key: str(value) for key, value in input_sums.items()},
        "emitted_sums": {key: str(value) for key, value in emitted_sums.items()},
        "scale_guard": scale_diagnostics,
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
            raise ValueError(f"契約第 {row_number} 列 value/value_type 無效：{row}")
        if any(
            row[column] is not None
            for column in ("age_lower", "age_upper", "age_band_raw")
        ):
            raise ValueError("初任薪資 PDF 沒有可用年齡欄位，不得產生年齡值")
        if row["unit"] not in {"元", "%"}:
            raise ValueError(f"薪資 PDF 單位異常：{row['unit']}")
        json.loads(row["breakdown"])
    return True


def fetch_and_transform(*, data_time=None):
    if data_time is None:
        data_time = datetime.now().astimezone().isoformat()
    texts, fetch_diagnostics = fetch_pdf_texts()
    parsed = {
        roc_year: parse_pdf_text(roc_year, text)
        for roc_year, text in sorted(texts.items())
    }
    rows, diagnostics = transform_parsed(parsed, data_time=data_time)
    diagnostics["fetch"] = fetch_diagnostics
    diagnostics["parsed_years"] = sorted(parsed)
    return rows, diagnostics


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
    data_time = get_tpe_now_time_str(is_with_tz=True)

    rows, diagnostics = fetch_and_transform(data_time=data_time)
    validate_contract(rows)
    data = pd.DataFrame(rows, columns=list(CONTRACT_COLUMNS))
    data["age_lower"] = data["age_lower"].astype("Int16")
    data["age_upper"] = data["age_upper"].astype("Int16")
    print(f"salary PDF diagnostics =========== {diagnostics}")
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
        dag_folder="youth_salary_initial_history_pdf",
    )
    dag.create_dag(etl_func=_transfer)
