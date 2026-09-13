"""SQL validation guard -- pure synchronous module.

Validates SQL strings before execution to ensure only safe, read-only
queries against the ``youth_*`` tables are permitted.  Uses regex-based
parsing (no ``sqlparse`` dependency).
"""

from __future__ import annotations

import hashlib
import re

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_BLOCKED_DML_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

_BLOCKED_FUNC_RE = re.compile(
    r"\b(pg_read_file|pg_write_file|dblink|COPY|lo_import|lo_export)\b",
    re.IGNORECASE,
)

_SET_RE = re.compile(r"\bSET\b", re.IGNORECASE)
_SET_LOCAL_TIMEOUT_RE = re.compile(
    r"\bSET\s+LOCAL\s+statement_timeout\b", re.IGNORECASE
)

_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_ROUND_CALL_RE = re.compile(r"\bROUND\s*\(", re.IGNORECASE)
_TEXT_BREAKDOWN_JSON_OP_RE = re.compile(
    r"\b(?:[a-z_]\w*\.)?breakdown\s*->>?", re.IGNORECASE
)

# Keywords that start a new SQL clause.  Used by the table-reference
# collector to know when to stop consuming identifiers.
_CLAUSE_STARTERS: frozenset[str] = frozenset(
    {
        "AND",
        "BETWEEN",
        "CASE",
        "CROSS",
        "ELSE",
        "END",
        "EXCEPT",
        "EXISTS",
        "FETCH",
        "FOR",
        "FROM",
        "FULL",
        "GROUP",
        "HAVING",
        "ILIKE",
        "IN",
        "INNER",
        "INTERSECT",
        "INTO",
        "IS",
        "JOIN",
        "LEFT",
        "LIKE",
        "LIMIT",
        "NATURAL",
        "NOT",
        "OFFSET",
        "ON",
        "OR",
        "ORDER",
        "OUTER",
        "RETURNING",
        "RIGHT",
        "SELECT",
        "SET",
        "UNION",
        "USING",
        "VALUES",
        "WHEN",
        "WHERE",
        "WINDOW",
        "WITH",
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_strings_and_comments(sql: str) -> str:
    """Remove string literals and comments to avoid false-positive matches."""
    # Single-line comments
    result = re.sub(r"--.*$", " ", sql, flags=re.MULTILINE)
    # Block comments (non-nested)
    result = re.sub(r"/\*.*?\*/", " ", result, flags=re.DOTALL)
    # Single-quoted string literals (handles '' escape sequences)
    result = re.sub(r"'(?:[^']|'')*'", "''", result)
    return result


def _extract_cte_names(sql: str) -> set[str]:
    """Return the names defined by ``WITH`` (CTE) clauses.

    These names are *not* real tables and must be excluded from the
    table-whitelist check.
    """
    cleaned = _strip_strings_and_comments(sql)
    names: set[str] = set()

    m = re.match(r"\s*WITH\s+(?:RECURSIVE\s+)?", cleaned, re.IGNORECASE)
    if not m:
        return names

    rest = cleaned[m.end() :]

    while True:
        cte = re.match(r"(\w+)\s+AS\s*\(", rest, re.IGNORECASE)
        if not cte:
            break
        names.add(cte.group(1).lower())

        # Skip past the balanced parenthesised sub-query
        depth = 1
        pos = cte.end()
        while pos < len(rest) and depth > 0:
            if rest[pos] == "(":
                depth += 1
            elif rest[pos] == ")":
                depth -= 1
            pos += 1
        rest = rest[pos:].lstrip()

        # Another CTE follows after a comma
        if rest.startswith(","):
            rest = rest[1:].lstrip()
        else:
            break

    return names


_EXTRACT_FIELD_RE = re.compile(
    r"\b(?:YEAR|MONTH|DAY|HOUR|MINUTE|SECOND|TIMEZONE|TIMEZONE_HOUR|"
    r"TIMEZONE_MINUTE|DOW|DOY|EPOCH|ISODOW|ISOYEAR|MICROSECONDS|"
    r"MILLISECONDS|QUARTER|WEEK)\s+$",
    re.IGNORECASE,
)


def _extract_table_names(sql: str) -> list[str]:
    """Extract table names from FROM / JOIN clauses using regex heuristics.

    Handles optional schema qualifiers (``public.youth_foo``), aliases,
    comma-separated table lists, and LATERAL.  Skips sub-queries,
    function calls (identifiers followed by ``(``), and SQL syntax
    that reuses FROM (``EXTRACT(YEAR FROM ...)``).
    """
    cleaned = _strip_strings_and_comments(sql)
    tables: list[str] = []

    for m in re.finditer(r"\b(?:FROM|JOIN)\s+", cleaned, re.IGNORECASE):
        if m.group().strip().upper() == "FROM":
            before = cleaned[: m.start()]
            if _EXTRACT_FIELD_RE.search(before):
                continue
        _collect_table_refs(cleaned[m.end() :], tables)

    return tables


def _collect_table_refs(rest: str, tables: list[str]) -> None:
    """Walk a comma-separated list of table references starting at *rest*."""
    while True:
        rest = rest.lstrip()
        if not rest:
            break

        # Skip LATERAL keyword
        lat = re.match(r"LATERAL\s+", rest, re.IGNORECASE)
        if lat:
            rest = rest[lat.end() :]
            continue

        # Sub-query -- stop collecting
        if rest[0] == "(":
            break

        # Match optional_schema.table_name
        tbl = re.match(r"(?:(\w+)\.)?(\w+)", rest)
        if not tbl:
            break

        name = tbl.group(2)
        rest = rest[tbl.end() :]

        # Identifier followed by '(' is a function call, not a table
        if rest.lstrip().startswith("("):
            break

        # If the matched word is a clause-starting keyword we have overshot
        if name.upper() in _CLAUSE_STARTERS:
            break

        tables.append(name.lower())

        # Optional alias: [AS] identifier
        rest = rest.lstrip()
        alias = re.match(r"(?:AS\s+)?(\w+)", rest, re.IGNORECASE)
        if alias:
            if alias.group(1).upper() in _CLAUSE_STARTERS:
                break  # not an alias; next clause
            rest = rest[alias.end() :]

        # Comma means more table references follow
        rest = rest.lstrip()
        if rest.startswith(","):
            rest = rest[1:]
        else:
            break


def _matching_paren(sql: str, opening: int) -> int | None:
    """Return the closing parenthesis for *opening*, ignoring quoted text."""
    depth = 1
    quote: str | None = None
    pos = opening + 1
    while pos < len(sql):
        char = sql[pos]
        if quote:
            if char == quote:
                if pos + 1 < len(sql) and sql[pos + 1] == quote:
                    pos += 2
                    continue
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    return None


def _next_round_call(sql: str, start: int) -> re.Match[str] | None:
    """Find a ROUND call outside SQL strings and comments."""
    pos = start
    while pos < len(sql):
        if sql.startswith("--", pos):
            newline = sql.find("\n", pos + 2)
            pos = len(sql) if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", pos):
            end = sql.find("*/", pos + 2)
            pos = len(sql) if end < 0 else end + 2
            continue
        if sql[pos] in ("'", '"'):
            quote = sql[pos]
            pos += 1
            while pos < len(sql):
                if sql[pos] == quote:
                    if pos + 1 < len(sql) and sql[pos + 1] == quote:
                        pos += 2
                        continue
                    pos += 1
                    break
                pos += 1
            continue

        match = _ROUND_CALL_RE.match(sql, pos)
        if match:
            return match
        pos += 1
    return None


def _top_level_comma(text: str) -> int | None:
    """Return the first comma outside nested parentheses and quoted text."""
    depth = 0
    quote: str | None = None
    pos = 0
    while pos < len(text):
        char = text[pos]
        if quote:
            if char == quote:
                if pos + 1 < len(text) and text[pos + 1] == quote:
                    pos += 2
                    continue
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            return pos
        pos += 1
    return None


def repair_common_sql(sql: str) -> str:
    """Repair SQL forms that PostgreSQL rejects but the model commonly emits.

    PostgreSQL has ``round(numeric, integer)`` but not
    ``round(double precision, integer)``.  Casting the first argument to
    numeric preserves the requested precision and makes either input type
    executable.  Only two-argument calls with an integer precision are
    changed; one-argument ``round`` calls remain untouched.
    """
    pieces: list[str] = []
    cursor = 0
    while match := _next_round_call(sql, cursor):
        opening = sql.find("(", match.start(), match.end())
        closing = _matching_paren(sql, opening)
        if closing is None:
            break

        arguments = sql[opening + 1 : closing]
        comma = _top_level_comma(arguments)
        scale = arguments[comma + 1 :].strip() if comma is not None else ""
        if comma is None or not re.fullmatch(r"[+-]?\d+", scale):
            cursor = closing + 1
            continue

        value = arguments[:comma].strip()
        if re.search(r"::\s*(?:numeric|decimal)\s*$", value, re.IGNORECASE):
            cursor = closing + 1
            continue
        pieces.append(sql[cursor : match.start()])
        pieces.append(f"ROUND(({value})::numeric, {scale})")
        cursor = closing + 1

    if not pieces:
        return sql
    pieces.append(sql[cursor:])
    return "".join(pieces)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_sql(sql: str) -> tuple[bool, str]:
    """Validate a SQL string for safe, read-only execution.

    Returns ``(True, "")`` when the query passes all checks, or
    ``(False, reason)`` when it must be blocked.

    Checks (in order):

    1. Only a single ``SELECT`` statement (``WITH`` for CTEs is OK).
    2. No semicolons (prevents stacked statements).
    3. No DML keywords (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE,
       CREATE, GRANT, REVOKE) -- even inside CTEs.
    4. No dangerous PostgreSQL functions.
    5. Table whitelist: every referenced table must match ``youth_*``.
    6. No JSON operators directly on source-table ``breakdown`` TEXT.
    7. No ``SET`` except ``SET LOCAL statement_timeout``.
    """
    stripped = sql.strip()
    if not stripped:
        return False, "Empty SQL statement"

    cleaned = _strip_strings_and_comments(stripped)

    # 1 -- Only SELECT (or WITH ... SELECT for CTEs)
    first_kw = re.match(r"\s*(\w+)", cleaned)
    if not first_kw or first_kw.group(1).upper() not in ("SELECT", "WITH"):
        return False, "Only SELECT statements are allowed"

    # 2 -- Block semicolons
    if ";" in cleaned:
        return False, "Semicolons are not allowed"

    # 3 -- Block DML keywords
    dml = _BLOCKED_DML_RE.search(cleaned)
    if dml:
        return False, f"Blocked keyword: {dml.group(1).upper()}"

    # 4 -- Block dangerous functions
    func = _BLOCKED_FUNC_RE.search(cleaned)
    if func:
        return False, f"Blocked function: {func.group(1)}"

    # 5 -- Table whitelist (youth_* only)
    cte_names = _extract_cte_names(stripped)
    tables = _extract_table_names(stripped)
    for tbl in tables:
        if tbl in cte_names:
            continue  # CTE alias, not a real table
        if not tbl.startswith("youth_"):
            return False, f"Table '{tbl}' is not in the allowed youth_* whitelist"

    # 6 -- Source tables store JSON text; an explicit cast is required.
    if _TEXT_BREAKDOWN_JSON_OP_RE.search(cleaned):
        return (
            False,
            "Source-table breakdown is TEXT; use breakdown::jsonb ->> 'key' "
            "(youth_fact.breakdown is already JSONB)",
        )

    # 7 -- Block SET (except SET LOCAL statement_timeout)
    for m in _SET_RE.finditer(cleaned):
        at = cleaned[m.start() :]
        if not _SET_LOCAL_TIMEOUT_RE.match(at):
            return False, "SET is not allowed (except SET LOCAL statement_timeout)"

    return True, ""


def add_safeguards(
    sql: str,
    row_limit: int = 10_000,
    *,
    wrap_timeout: bool = True,
) -> str:
    """Wrap *sql* with safety measures.

    * Appends ``LIMIT {row_limit}`` when no ``LIMIT`` clause is present.
    * When *wrap_timeout* is ``True`` (the default), prepends
      ``SET LOCAL statement_timeout = '30s';`` to produce a self-contained
      multi-statement string.

    Set *wrap_timeout* to ``False`` when the caller (e.g.
    :func:`db.execute_readonly`) already handles the statement timeout
    separately and requires a single-statement SQL string.
    """
    trimmed = repair_common_sql(sql).strip().rstrip(";").strip()
    if not _LIMIT_RE.search(trimmed):
        trimmed = f"{trimmed}\nLIMIT {row_limit}"
    if wrap_timeout:
        return f"SET LOCAL statement_timeout = '30s';\n{trimmed}"
    return trimmed


def compute_sql_hash(sql: str) -> str:
    """Return a stable 16-character hex SHA-256 digest of normalised *sql*.

    Normalisation collapses whitespace and lower-cases the string so that
    cosmetic reformatting does not change the hash.
    """
    normalised = " ".join(sql.split()).strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]
