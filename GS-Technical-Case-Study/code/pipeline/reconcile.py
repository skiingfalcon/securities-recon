"""Reconciler: cross-custodian join and raw break detection.

Layer 1 — deterministic, no LLM. Joins normalized positions from two
custodians, resolves identifiers via IdentifierResolver, and emits Break
records for every disagreement. No book of record is available in this
case study, so book_* fields are always None.

Algorithm C from design.md §5.C is implemented in reconcile().
"""

import hashlib
from collections import defaultdict
from datetime import date
from typing import Literal

from code.models import Break, IngestWarning, Position
from code.tools.securities import IdentifierResolver

# ---------------------------------------------------------------------------
# Break-ID helper
# ---------------------------------------------------------------------------


def _make_break_id(
    as_of: date,
    security_id_or_query: str,
    custodian: str,
) -> str:
    """Compute a stable 12-character break identifier.

    The ID is the first 12 hex characters of the SHA-256 digest of the
    pipe-delimited tuple (as_of_date, security_id_or_query, custodian).
    Using a content-addressed hash means the same break always gets the
    same ID across runs, which is important for deduplication and audit
    trail correlation.

    Args:
        as_of: The reconciliation as-of date.
        security_id_or_query: The resolved security_id, or the raw query
            string when the identifier is ambiguous (security_id is None).
        custodian: The custodian label ("custodian_a", "custodian_b", or
            "both" for two-sided breaks).

    Returns:
        A 12-character lowercase hex string.
    """
    payload = f"{as_of.isoformat()}|{security_id_or_query}|{custodian}"
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Warning index helper
# ---------------------------------------------------------------------------


def _build_warning_index(
    warnings: list[IngestWarning],
) -> dict[tuple[str, int], list[IngestWarning]]:
    """Index warnings by (source_file, source_row_index) for O(1) lookup.

    Each Break needs the warnings that were emitted for its source row(s).
    Building this index once avoids an O(n*m) scan inside the main loop.

    Args:
        warnings: Flat list of all IngestWarning records from both custodians.

    Returns:
        A dict mapping (source_file, row_index) → list of warnings.
    """
    index: dict[tuple[str, int], list[IngestWarning]] = defaultdict(list)
    for w in warnings:
        index[(w.source_file, w.source_row_index)].append(w)
    return index


def _warnings_for(
    position: Position,
    index: dict[tuple[str, int], list[IngestWarning]],
) -> list[IngestWarning]:
    """Retrieve warnings for a single Position's source row.

    Args:
        position: The Position whose source row we want warnings for.
        index: The pre-built warning index from _build_warning_index.

    Returns:
        List of IngestWarning records for this row (may be empty).
    """
    # The source_file key in IngestWarning is the CSV basename (e.g.
    # "custodian_a.csv"). We reconstruct the expected key from the custodian
    # label and the row index stored on the Position.
    source_file = f"{position.custodian}.csv"
    return list(index.get((source_file, position.source_row_index), []))


# ---------------------------------------------------------------------------
# Main reconcile function
# ---------------------------------------------------------------------------


def reconcile(
    positions_a: list[Position],
    positions_b: list[Position],
    warnings: list[IngestWarning],
    as_of: date,
    resolver: IdentifierResolver | None = None,
) -> list[Break]:
    """Join positions from two custodians and emit Break records.

    Implements Algorithm C from design.md §5.C:

    1. For each Position, call IdentifierResolver.resolve().
    2. Partition into ambiguous (security_id is None) and resolved
       (grouped by security_id).
    3. Emit identifier_ambiguous breaks for ambiguous positions.
    4. For each resolved group, compare the two custodian sides:
       - One-sided → missing_at_custodian
       - Both present, type mismatch → position_type_mismatch (with
         quantity fields also populated so the delta is not lost)
       - Both present, quantity mismatch → quantity_mismatch
       - Both present, value mismatch → value_mismatch
       - Both present, no mismatch → no break
    5. book_quantity, book_market_value, position_type_book are always
       None because no book of record is available in this case study.

    Args:
        positions_a: Normalized positions from custodian_a.
        positions_b: Normalized positions from custodian_b.
        warnings: All IngestWarning records from both custodians, used to
            attach per-row warnings to each Break.
        as_of: The reconciliation as-of date, used in break_id computation.
        resolver: An IdentifierResolver instance. If None, one is
            constructed from the default securities_reference.csv path.
            Passing an explicit resolver makes the function testable without
            touching the filesystem.

    Returns:
        A list of Break records, one per detected discrepancy.
    """
    # Build the resolver lazily so callers that already have one don't pay
    # the CSV-read cost twice.
    if resolver is None:
        from pathlib import Path

        master_path = (
            Path(__file__).parent.parent.parent / "securities_reference.csv"
        )
        resolver = IdentifierResolver(master_path)

    # Pre-build the warning index so we can attach per-row warnings to each
    # Break in O(1) rather than scanning the full list each time.
    warn_index = _build_warning_index(warnings)

    # ---------------------------------------------------------------------------
    # Step 1 & 2: Resolve identifiers and partition into ambiguous / resolved
    # ---------------------------------------------------------------------------

    # ambiguous: list of (position, match) where match.security_id is None
    ambiguous: list[tuple[Position, object]] = []

    # resolved: security_id → {"custodian_a": Position | None, "custodian_b": Position | None}
    # We use a dict-of-dicts so the join step is a simple key lookup.
    resolved: dict[str, dict[str, Position | None]] = defaultdict(
        lambda: {"custodian_a": None, "custodian_b": None}
    )

    for pos in positions_a + positions_b:
        # Custodian A uses ticker symbols; Custodian B uses free-text descriptions.
        query_kind: Literal["ticker", "description"] = (
            "ticker" if pos.custodian == "custodian_a" else "description"
        )
        match = resolver.resolve(pos.raw_query, query_kind)

        if match.security_id is None:
            # Ambiguous or below-threshold — cannot join on security_id.
            ambiguous.append((pos, match))
        else:
            # Resolved — group by security_id. If two positions from the same
            # custodian resolve to the same security_id (shouldn't happen in
            # clean data but is possible), the later one wins. In production
            # we'd surface this as a data-quality warning; for the demo the
            # simple overwrite is acceptable.
            resolved[match.security_id][pos.custodian] = pos

    breaks: list[Break] = []

    # ---------------------------------------------------------------------------
    # Step 3: Emit identifier_ambiguous breaks
    # ---------------------------------------------------------------------------

    for pos, match in ambiguous:
        # Use the raw query as the key in the break_id so the hash is still
        # stable across runs even though we have no security_id.
        break_id = _make_break_id(as_of, pos.raw_query, pos.custodian)

        # Collect warnings for this source row.
        row_warnings = _warnings_for(pos, warn_index)

        breaks.append(
            Break(
                break_id=break_id,
                as_of_date=as_of,
                security_id=None,  # ambiguous — no resolved ID
                custodian=pos.custodian,
                break_type="identifier_ambiguous",
                # No book-of-record fields in this case study.
                book_quantity=None,
                book_market_value=None,
                position_type_book=None,
                # Populate the custodian side we do know about.
                custodian_quantity=pos.quantity,
                custodian_market_value=pos.market_value,
                position_type_custodian=pos.position_type,
                raw_source_row=pos.raw_source_row,
                ingest_warnings=row_warnings,
            )
        )

    # ---------------------------------------------------------------------------
    # Step 4: Emit breaks for resolved groups
    # ---------------------------------------------------------------------------

    # Value-mismatch tolerance: floating-point dust below this threshold is
    # not a real break (e.g. rounding differences in custodian exports).
    VALUE_EPSILON: float = 0.01

    for security_id, sides in resolved.items():
        pa: Position | None = sides["custodian_a"]
        pb: Position | None = sides["custodian_b"]

        # Collect warnings from whichever side(s) are present.
        row_warnings: list[IngestWarning] = []
        if pa is not None:
            row_warnings.extend(_warnings_for(pa, warn_index))
        if pb is not None:
            row_warnings.extend(_warnings_for(pb, warn_index))

        # --- Step 4.2: One-sided — present only at custodian_b ---
        if pa is None and pb is not None:
            break_id = _make_break_id(as_of, security_id, "custodian_a")
            breaks.append(
                Break(
                    break_id=break_id,
                    as_of_date=as_of,
                    security_id=security_id,
                    # The break is "missing at custodian_a" — we label the
                    # custodian field as the one that is absent.
                    custodian="custodian_a",
                    break_type="missing_at_custodian",
                    book_quantity=None,
                    book_market_value=None,
                    position_type_book=None,
                    # custodian_quantity is None because custodian_a has no position.
                    custodian_quantity=None,
                    custodian_market_value=None,
                    position_type_custodian=None,
                    raw_source_row=pb.raw_source_row,
                    ingest_warnings=row_warnings,
                )
            )
            continue

        # --- Step 4.3: One-sided — present only at custodian_a ---
        if pb is None and pa is not None:
            break_id = _make_break_id(as_of, security_id, "custodian_b")
            breaks.append(
                Break(
                    break_id=break_id,
                    as_of_date=as_of,
                    security_id=security_id,
                    custodian="custodian_b",
                    break_type="missing_at_custodian",
                    book_quantity=None,
                    book_market_value=None,
                    position_type_book=None,
                    custodian_quantity=None,
                    custodian_market_value=None,
                    position_type_custodian=None,
                    raw_source_row=pa.raw_source_row,
                    ingest_warnings=row_warnings,
                )
            )
            continue

        # --- Step 4.4: Both sides present — compare ---
        # At this point both pa and pb are non-None.
        assert pa is not None and pb is not None  # type narrowing for mypy

        # Step 4.4a: Position-type mismatch (direction flip).
        # NVDA: 10000 LONG vs -5000 SHORT; BRK.A: 30 LONG vs -10 SHORT.
        # Per design.md §5.C step 4.4: both position_type_* AND quantity_*
        # fields SHALL be populated so the quantity divergence is not lost.
        if pa.position_type != pb.position_type:
            break_id = _make_break_id(as_of, security_id, "both")
            breaks.append(
                Break(
                    break_id=break_id,
                    as_of_date=as_of,
                    security_id=security_id,
                    custodian="both",
                    break_type="position_type_mismatch",
                    book_quantity=None,
                    book_market_value=None,
                    position_type_book=None,
                    # Populate both custodian sides so the full picture is
                    # visible in the break record without needing to join back
                    # to the raw positions.
                    custodian_quantity=pa.quantity,  # custodian_a quantity
                    custodian_market_value=pa.market_value,
                    position_type_custodian=pa.position_type,
                    quantity_delta=pa.quantity - pb.quantity,
                    raw_source_row=pa.raw_source_row,
                    ingest_warnings=row_warnings,
                )
            )
            continue

        # Step 4.4b: Quantity mismatch (same direction, different size).
        # AAPL: 75000 vs 25000; AMZN: 15000 vs 4000; MSFT: 18000 vs 15000; V: 5000 vs 3000.
        if pa.quantity != pb.quantity:
            break_id = _make_break_id(as_of, security_id, "both")
            breaks.append(
                Break(
                    break_id=break_id,
                    as_of_date=as_of,
                    security_id=security_id,
                    custodian="both",
                    break_type="quantity_mismatch",
                    book_quantity=None,
                    book_market_value=None,
                    position_type_book=None,
                    custodian_quantity=pa.quantity,
                    custodian_market_value=pa.market_value,
                    position_type_custodian=pa.position_type,
                    quantity_delta=pa.quantity - pb.quantity,
                    raw_source_row=pa.raw_source_row,
                    ingest_warnings=row_warnings,
                )
            )
            continue

        # Step 4.4c: Value mismatch (quantities agree but valuations diverge).
        # Reserved for FX or stale-price cases; quantities are equal here.
        if abs(pa.market_value - pb.market_value) > VALUE_EPSILON:
            break_id = _make_break_id(as_of, security_id, "both")
            breaks.append(
                Break(
                    break_id=break_id,
                    as_of_date=as_of,
                    security_id=security_id,
                    custodian="both",
                    break_type="value_mismatch",
                    book_quantity=None,
                    book_market_value=None,
                    position_type_book=None,
                    custodian_quantity=pa.quantity,
                    custodian_market_value=pa.market_value,
                    position_type_custodian=pa.position_type,
                    value_delta=pa.market_value - pb.market_value,
                    raw_source_row=pa.raw_source_row,
                    ingest_warnings=row_warnings,
                )
            )
            continue

        # Step 4.4d: No break — quantities, types, and values all agree.
        # This is the happy path; no Break record is emitted.

    return breaks
