"""Ingest_Module: parse and normalize custodian CSV files.

Layer 1 — deterministic, no LLM. Owns every coercion (paren negatives,
non-ISO dates, year mismatches, ticker-dot preservation) and emits a typed
IngestWarning for each one. Coercions never happen silently.
"""

import csv
from datetime import date
from pathlib import Path
from typing import Literal

from dateutil import parser as dateutil_parser

from code.models import IngestWarning, Position

# ---------------------------------------------------------------------------
# Numeric coercion helpers
# ---------------------------------------------------------------------------


def parse_paren_int(
    raw: str, source_file: str, source_row_index: int
) -> tuple[int, IngestWarning | None]:
    """Parse a string that may use parentheses to denote a negative integer.

    Custodian B encodes short positions as e.g. "(5000)" — a common
    accounting convention. This helper normalises that to -5000 and emits
    a typed warning so the coercion is never silent.

    Args:
        raw: The raw string value from the CSV cell.
        source_file: Basename of the source CSV (for the warning record).
        source_row_index: Zero-based row index (for the warning record).

    Returns:
        A tuple of (parsed_int, warning_or_None).

    Raises:
        ValueError: If the inner value cannot be parsed as an integer.
    """
    stripped = raw.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        inner = stripped[1:-1].replace(",", "")
        value = -int(inner)
        warning = IngestWarning(
            type="paren_negative_coerced",
            source_file=source_file,
            source_row_index=source_row_index,
            message=f"Parenthesised negative coerced: {raw!r} → {value}",
            detail={"raw": raw, "coerced": str(value)},
        )
        return value, warning
    # Plain integer — strip commas for safety (e.g. "1,000")
    return int(stripped.replace(",", "")), None


def parse_paren_float(
    raw: str, source_file: str, source_row_index: int
) -> tuple[float, IngestWarning | None]:
    """Parse a string that may use parentheses to denote a negative float.

    Same convention as parse_paren_int but for market-value fields which
    may carry decimal places.

    Args:
        raw: The raw string value from the CSV cell.
        source_file: Basename of the source CSV (for the warning record).
        source_row_index: Zero-based row index (for the warning record).

    Returns:
        A tuple of (parsed_float, warning_or_None).

    Raises:
        ValueError: If the inner value cannot be parsed as a float.
    """
    stripped = raw.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        inner = stripped[1:-1].replace(",", "")
        value = -float(inner)
        warning = IngestWarning(
            type="paren_negative_coerced",
            source_file=source_file,
            source_row_index=source_row_index,
            message=f"Parenthesised negative coerced: {raw!r} → {value}",
            detail={"raw": raw, "coerced": str(value)},
        )
        return value, warning
    return float(stripped.replace(",", "")), None


def _detect_date_format(raw: str) -> str:
    """Detect the format token for a non-ISO date string.

    Used to populate the `detected_format` field in the non_iso_date_coerced
    warning so downstream consumers know exactly what the custodian sent.

    Args:
        raw: The raw date string from the CSV cell.

    Returns:
        A format token string: "MM/DD/YYYY", "M/D/YY", "DD-MMM-YYYY",
        or "unknown" if none of the known patterns match.
    """
    import re

    # Four date formats observed in Custodian B (see custodian_b.csv):
    #   01/02/2025  → MM/DD/YYYY  (two-digit month and day, four-digit year)
    #   1/2/25      → M/D/YY     (one-digit month/day, two-digit year)
    #   02-JAN-2025 → DD-MMM-YYYY (two-digit day, three-letter month abbrev, four-digit year)
    #   2025-01-02  → ISO 8601   (handled before this function is called)
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", raw.strip()):
        return "MM/DD/YYYY"
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}", raw.strip()):
        return "M/D/YY"
    if re.fullmatch(r"\d{2}-[A-Za-z]{3}-\d{4}", raw.strip()):
        return "DD-MMM-YYYY"
    return "unknown"


def parse_flexible_date(
    raw: str, configured_year: int, source_file: str, source_row_index: int
) -> tuple[date, list[IngestWarning]]:
    """Parse a date string that may be ISO 8601 or one of several non-ISO formats.

    Implements Algorithm A from the design spec:
    1. Try ISO 8601 first (YYYY-MM-DD) — no format warning needed.
    2. Otherwise, parse with dateutil and emit a non_iso_date_coerced warning
       carrying the detected format token.
    3. After parsing (either path), compare the parsed year against
       configured_year. If they differ, emit a year_mismatch warning.

    The year-mismatch warning is important for this case study: the brief
    states positions are for Jan 2, 2026, but every row in the custodian
    files shows 2025. We surface that discrepancy rather than silently
    accepting the wrong year.

    Args:
        raw: The raw date string from the CSV cell.
        configured_year: The authoritative year for this pipeline run
            (e.g. 2026 per the brief). Used only for comparison; the
            parsed date is returned as-is regardless.
        source_file: Basename of the source CSV (for warning records).
        source_row_index: Zero-based row index (for warning records).

    Returns:
        A tuple of (parsed_date, list_of_warnings). The list may be empty
        (ISO date, year matches), contain one warning (non-ISO format OR
        year mismatch), or contain two warnings (non-ISO format AND year
        mismatch).

    Raises:
        ValueError: If the date string cannot be parsed by either path.
    """
    import re

    warnings: list[IngestWarning] = []

    # --- Step 1: Try ISO 8601 ---
    # Match exactly YYYY-MM-DD so we don't accidentally accept partial ISO
    # strings that dateutil would also handle. No warning for clean ISO dates.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw.strip()):
        parsed = date.fromisoformat(raw.strip())
    else:
        # --- Step 2: Non-ISO path — parse with dateutil ---
        # dayfirst=False ensures "01/02/2025" is read as Jan 2, not Feb 1,
        # which matches the US convention used by Custodian B.
        parsed_dt = dateutil_parser.parse(raw.strip(), dayfirst=False)
        parsed = parsed_dt.date()

        detected_format = _detect_date_format(raw)
        warnings.append(
            IngestWarning(
                type="non_iso_date_coerced",
                source_file=source_file,
                source_row_index=source_row_index,
                message=(
                    f"Non-ISO date coerced: {raw!r} → {parsed.isoformat()} "
                    f"(detected format: {detected_format})"
                ),
                detail={"raw": raw, "detected_format": detected_format},
            )
        )

    # --- Step 3: Year-mismatch check (applies to both ISO and non-ISO paths) ---
    # The brief says positions are for 2026-01-02, but every custodian row
    # shows 2025. We emit a warning rather than raising so the pipeline can
    # continue and surface the discrepancy in out/data_quality.json.
    if parsed.year != configured_year:
        warnings.append(
            IngestWarning(
                type="year_mismatch",
                source_file=source_file,
                source_row_index=source_row_index,
                message=(
                    f"Parsed year {parsed.year} does not match configured year "
                    f"{configured_year} for date {raw!r}"
                ),
                detail={
                    "parsed_year": parsed.year,
                    "configured_year": configured_year,
                },
            )
        )

    return parsed, warnings


# ---------------------------------------------------------------------------
# Identifier normalization
# ---------------------------------------------------------------------------


def normalize_identifier(
    raw: str, source_file: str, source_row_index: int
) -> tuple[str, IngestWarning | None]:
    """Normalize an identifier field from either custodian.

    Implements Algorithm A step 5 from the design spec:
    - If the field looks like a ticker (uppercase letters, digits, and dots
      only; no spaces; contains at least one dot), preserve it verbatim and
      emit a ``ticker_dot_preserved`` warning. The dot must NOT be stripped
      because ``BRK.A`` and ``BRK`` are different securities — silently
      removing the dot would cause a silent identifier-mismatch break.
    - Otherwise, strip leading/trailing whitespace only and return with no
      warning. Free-text descriptions from Custodian B fall into this branch.

    The ticker-like detection uses ``re.fullmatch(r'[A-Z0-9.]+', raw.strip())``
    combined with a dot-presence check. This intentionally excludes lowercase
    letters and spaces so that descriptions like ``"Apple Inc Common Stock"``
    are never misclassified as tickers.

    Args:
        raw: The raw identifier string from the CSV cell (``symbol`` for
            Custodian A, ``security_description`` for Custodian B).
        source_file: Basename of the source CSV (for the warning record).
        source_row_index: Zero-based row index (for the warning record).

    Returns:
        A tuple of (normalized_identifier, warning_or_None). The identifier
        is always ``raw.strip()``; the warning is non-None only when the
        ticker-dot branch fires.
    """
    import re

    stripped = raw.strip()

    # Detect ticker-like strings: all chars are uppercase letters, digits, or
    # dots; no spaces; and at least one dot is present.
    # The fullmatch anchors to the entire stripped string so a description
    # with a trailing dot (e.g. "Amazon.com, Inc.") won't match because the
    # comma and space break the pattern.
    is_ticker_like = bool(re.fullmatch(r"[A-Z0-9.]+", stripped)) and "." in stripped

    if is_ticker_like:
        # Preserve verbatim — do NOT uppercase, lowercase, or strip the dot.
        # BRK.A is the canonical case: many downstream systems strip the dot
        # and produce "BRKA", which fails to match the security master entry
        # keyed on "BRK.A". Emitting a warning makes this coercion-free path
        # visible in out/data_quality.json.
        warning = IngestWarning(
            type="ticker_dot_preserved",
            source_file=source_file,
            source_row_index=source_row_index,
            message=(f"Ticker-like identifier with dot preserved verbatim: {stripped!r}"),
            detail={"raw": raw, "preserved": stripped},
        )
        return stripped, warning

    # Plain description or clean ticker (no dot) — strip whitespace only.
    return stripped, None


# ---------------------------------------------------------------------------
# Custodian A entrypoint
# ---------------------------------------------------------------------------


def ingest_custodian_a(
    path: Path, configured_year: int = 2026
) -> tuple[list[Position], list[IngestWarning]]:
    """Parse and normalize Custodian A's EOD position CSV.

    Custodian A uses clean ticker symbols, plain (non-parenthesised) integers
    and floats, explicit LONG/SHORT position_type, and MM/DD/YYYY dates.
    Despite the clean format, we run every field through the same helpers used
    for Custodian B so that any unexpected quirks (e.g. a future paren value,
    a year mismatch) are surfaced as typed warnings rather than silent errors.

    CSV columns:
        symbol        — ticker symbol, e.g. "AAPL", "BRK.A"
        quantity      — plain integer (no parens)
        market_value  — plain float
        position_type — literal "LONG" or "SHORT"
        trade_date    — date in MM/DD/YYYY format (non-ISO)

    Args:
        path: Path to the custodian_a.csv file.
        configured_year: The authoritative year for this pipeline run.
            Used to detect year-mismatch warnings. Defaults to 2026 per
            the brief (positions are for Jan 2, 2026, but the file shows
            2025 — that discrepancy is surfaced as a warning, not an error).

    Returns:
        A tuple of (positions, warnings) where:
        - positions is a list of normalised Position records, one per CSV row.
        - warnings is a flat list of all IngestWarning records emitted across
          all rows (may be empty if the file is perfectly clean).

    Raises:
        FileNotFoundError: If `path` does not exist.
        ValueError: If a field cannot be parsed (e.g. non-numeric quantity).
    """
    positions: list[Position] = []
    all_warnings: list[IngestWarning] = []

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row_idx, row in enumerate(reader):
            row_warnings: list[IngestWarning] = []

            # --- Step a: parse quantity ---
            # Custodian A uses plain ints, but we run parse_paren_int for
            # consistency — if a future file revision adds parens, the warning
            # fires automatically rather than causing a silent parse error.
            quantity, qty_warn = parse_paren_int(row["quantity"], path.name, row_idx)
            if qty_warn is not None:
                row_warnings.append(qty_warn)

            # --- Step b: parse market_value ---
            market_value, mv_warn = parse_paren_float(row["market_value"], path.name, row_idx)
            if mv_warn is not None:
                row_warnings.append(mv_warn)

            # --- Step c: parse trade_date ---
            # The column is named "trade_date" but for an EOD position file
            # this is really an as_of_date. We parse it and emit a
            # non_iso_date_coerced warning (MM/DD/YYYY is not ISO 8601) plus
            # a year_mismatch warning if the year differs from configured_year.
            as_of_date, date_warns = parse_flexible_date(
                row["trade_date"], configured_year, path.name, row_idx
            )
            row_warnings.extend(date_warns)

            # --- Step d: parse symbol ---
            # normalize_identifier preserves dots in ticker-like strings
            # (e.g. "BRK.A") and emits a ticker_dot_preserved warning so the
            # coercion is visible in out/data_quality.json.
            symbol, sym_warn = normalize_identifier(row["symbol"], path.name, row_idx)
            if sym_warn is not None:
                row_warnings.append(sym_warn)

            # --- Step e: read position_type directly ---
            # Custodian A provides an explicit LONG/SHORT column; no derivation
            # from sign is needed (unlike Custodian B which uses paren negatives).
            position_type: Literal["LONG", "SHORT"] = row["position_type"].strip()  # type: ignore[assignment]

            # --- Step f: collect non-None warnings (already done above) ---

            # --- Step g: construct Position ---
            position = Position(
                custodian="custodian_a",
                raw_query=symbol,
                quantity=quantity,
                market_value=market_value,
                position_type=position_type,
                as_of_date=as_of_date,
                source_row_index=row_idx,
                raw_source_row=dict(row),
            )

            positions.append(position)
            all_warnings.extend(row_warnings)

    return positions, all_warnings


# ---------------------------------------------------------------------------
# Custodian B entrypoint
# ---------------------------------------------------------------------------


def ingest_custodian_b(
    path: Path, configured_year: int = 2026
) -> tuple[list[Position], list[IngestWarning]]:
    """Parse and normalize Custodian B's EOD position CSV.

    Custodian B uses free-text security descriptions (no tickers), encodes
    short positions as parenthesised negatives (e.g. "(5000)"), and mixes
    four different date formats across rows. There is no explicit
    ``position_type`` column — direction is derived from the sign of the
    parsed quantity (negative ⇒ SHORT, positive ⇒ LONG).

    CSV columns:
        security_description — free-text name, e.g. "Apple Inc Common Stock"
        1d_shares            — integer, may be parenthesised negative
        market_value_usd     — float, may be parenthesised negative
        as_of                — date in one of four formats:
                               MM/DD/YYYY, M/D/YY, YYYY-MM-DD, DD-MMM-YYYY

    Args:
        path: Path to the custodian_b.csv file.
        configured_year: The authoritative year for this pipeline run.
            Used to detect year-mismatch warnings. Defaults to 2026 per
            the brief (positions are for Jan 2, 2026, but the file shows
            2025 — that discrepancy is surfaced as a warning, not an error).

    Returns:
        A tuple of (positions, warnings) where:
        - positions is a list of normalised Position records, one per CSV row.
        - warnings is a flat list of all IngestWarning records emitted across
          all rows (may be empty if the file is perfectly clean).

    Raises:
        FileNotFoundError: If `path` does not exist.
        ValueError: If a field cannot be parsed (e.g. non-numeric shares).
    """
    positions: list[Position] = []
    all_warnings: list[IngestWarning] = []

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row_idx, row in enumerate(reader):
            row_warnings: list[IngestWarning] = []

            # --- Step a: parse shares (paren negatives fire here) ---
            # Custodian B encodes short positions as "(5000)" — a common
            # accounting convention. parse_paren_int normalises that to -5000
            # and emits a paren_negative_coerced warning so the coercion is
            # never silent.
            quantity, qty_warn = parse_paren_int(row["1d_shares"], path.name, row_idx)
            if qty_warn is not None:
                row_warnings.append(qty_warn)

            # --- Step b: parse market_value (same paren convention) ---
            # NVDA shows "(4250000)" and BRK.A shows "(6500000)" in the source
            # file — both are short positions with negative market values.
            market_value, mv_warn = parse_paren_float(row["market_value_usd"], path.name, row_idx)
            if mv_warn is not None:
                row_warnings.append(mv_warn)

            # --- Step c: parse as_of date (four formats in this file) ---
            # Custodian B mixes: 01/02/2025, 1/2/25, 2025-01-02, 02-JAN-2025.
            # parse_flexible_date handles all four and emits non_iso_date_coerced
            # for the three non-ISO variants, plus year_mismatch for every row
            # because the file shows 2025 while configured_year defaults to 2026.
            as_of_date, date_warns = parse_flexible_date(
                row["as_of"], configured_year, path.name, row_idx
            )
            row_warnings.extend(date_warns)

            # --- Step d: normalize security_description ---
            # normalize_identifier strips leading/trailing whitespace for plain
            # descriptions. It also detects ticker-like strings (uppercase + dot)
            # and emits ticker_dot_preserved — relevant if a future Custodian B
            # file ever includes a ticker like "BRK.A" directly.
            identifier, id_warn = normalize_identifier(
                row["security_description"], path.name, row_idx
            )
            if id_warn is not None:
                row_warnings.append(id_warn)

            # --- Step e: derive position_type from sign of quantity ---
            # Unlike Custodian A (which has an explicit LONG/SHORT column),
            # Custodian B encodes direction via paren negatives. After parsing,
            # a negative quantity means SHORT; zero or positive means LONG.
            # This mirrors the accounting convention: short positions are
            # liabilities and are represented as negative quantities.
            position_type: Literal["LONG", "SHORT"] = "SHORT" if quantity < 0 else "LONG"

            # --- Step f: collect non-None warnings (already done above) ---

            # --- Step g: construct Position ---
            position = Position(
                custodian="custodian_b",
                raw_query=identifier,
                quantity=quantity,
                market_value=market_value,
                position_type=position_type,
                as_of_date=as_of_date,
                source_row_index=row_idx,
                raw_source_row=dict(row),
            )

            positions.append(position)
            all_warnings.extend(row_warnings)

    return positions, all_warnings
