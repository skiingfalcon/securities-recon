"""Reconciler: cross-custodian join and raw break detection.

Layer 1 — deterministic, no LLM. Joins normalized positions from two
custodians, resolves identifiers via IdentifierResolver, and emits Break
records for every disagreement. No book of record is available in this
case study, so book_* fields are always None.

Algorithm C from design.md §5.C is implemented in reconcile().
"""

import hashlib
import json
import subprocess
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from code.models import ArtifactMetadata, Break, IngestWarning, OutputArtifact, Position
from code.pipeline.ingest import ingest_custodian_a, ingest_custodian_b
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
        A dict mapping (source_file, row_index) ? list of warnings.
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
       - One-sided ? missing_at_custodian
       - Both present, type mismatch ? position_type_mismatch (with
         quantity fields also populated so the delta is not lost)
       - Both present, quantity mismatch ? quantity_mismatch
       - Both present, value mismatch ? value_mismatch
       - Both present, no mismatch ? no break
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
        master_path = Path(__file__).parent.parent.parent / "securities_reference.csv"
        resolver = IdentifierResolver(master_path)

    # Pre-build the warning index so we can attach per-row warnings to each
    # Break in O(1) rather than scanning the full list each time.
    warn_index = _build_warning_index(warnings)

    # ---------------------------------------------------------------------------
    # Step 1 & 2: Resolve identifiers and partition into ambiguous / resolved
    # ---------------------------------------------------------------------------

    # ambiguous: list of (position, match) where match.security_id is None
    ambiguous: list[tuple[Position, object]] = []

    # resolved: security_id ? {"custodian_a": Position | None, "custodian_b": Position | None}
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


# ---------------------------------------------------------------------------
# Phase 5 — OutputArtifact envelope helpers (design.md §5.D)
# ---------------------------------------------------------------------------


def compute_input_hashes(paths: dict[str, Path]) -> dict[str, str]:
    """Compute SHA-256 digests for every input file in a single streaming pass.

    Uses ``hashlib.file_digest`` (Python 3.11+) which streams the file in
    chunks so we never materialise the whole CSV in memory  important if
    custodian files grow to GB scale in production.
    """
    hashes: dict[str, str] = {}
    for label, path in paths.items():
        with path.open("rb") as fh:
            hashes[label] = hashlib.file_digest(fh, "sha256").hexdigest()
    return hashes


def read_git_short_sha() -> str:
    """Return the current git short SHA, or the literal ``"uncommitted"``.

    MUST NOT raise. Req 5 AC 3 makes the metadata envelope a hard invariant
    of every artifact write, so a missing git binary, a non-git directory,
    or any subprocess failure has to degrade silently.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "uncommitted"
    if result.returncode != 0:
        return "uncommitted"
    sha = result.stdout.strip()
    return sha or "uncommitted"


def write_envelope(
    path: Path,
    records: list[BaseModel],
    metadata: ArtifactMetadata,
) -> None:
    """Serialise records inside an OutputArtifact envelope and write to disk.

    Every JSON artifact in ``out/`` shares the same ``{metadata, data}``
    shape so reviewers can verify provenance (input hashes, commit, as-of)
    without reading the data block. See design.md §5.D and Req 5 AC 1, 2.
    """
    envelope = OutputArtifact(
        metadata=metadata,
        data=[r.model_dump(mode="json") for r in records],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(envelope.model_dump(mode="json"), fh, indent=2, default=str)


# ---------------------------------------------------------------------------
# Phase 6 — Run summary printer (Req 4 AC 1, 4)
# ---------------------------------------------------------------------------


def _format_break_line(b: Break) -> str:
    """Render one Break as a compact, fixed-column-ish stdout line.

    All data is read straight off the Break record  no joins back to the
    Position list. For one-sided breaks the present-side identifier comes
    out of raw_source_row; for two-sided breaks raw_source_row is the
    A-side (per the Reconciler), so the symbol is there. The B-side
    quantities are intentionally not reconstructed  the canonical truth
    lives in out/raw_breaks.json.
    """
    sec = b.security_id or "-"
    ident = b.raw_source_row.get("symbol") or b.raw_source_row.get("security_description") or ""
    if len(ident) > 32:
        ident = ident[:29] + "..."
    if b.break_type == "quantity_mismatch":
        detail = f"custodian_a_qty={b.custodian_quantity}, delta={b.quantity_delta:+d}"
    elif b.break_type == "position_type_mismatch":
        detail = (
            f"custodian_a={b.position_type_custodian}/{b.custodian_quantity}, "
            f"|delta|={abs(b.quantity_delta or 0)} shares"
        )
    elif b.break_type == "missing_at_custodian":
        detail = f"absent at {b.custodian}"
    elif b.break_type == "identifier_ambiguous":
        detail = (
            f"unresolved ({b.custodian}, qty={b.custodian_quantity} {b.position_type_custodian})"
        )
    elif b.break_type == "value_mismatch":
        detail = f"value_delta={b.value_delta:+.2f}"
    else:
        detail = ""
    return f"  {b.break_type:22s} {sec:8s} {b.custodian:13s} {ident:32s} {detail}"


def print_run_summary(breaks: list[Break], runtime_seconds: float) -> None:
    """Print the Layer-1 run summary to stdout.

    The very first line MUST be the no-book-of-record disclosure (Req 4
    AC 1). Reviewers grep for this line to confirm the pipeline is honest
    about not having a true book of record to reconcile against.

    Output shape:
        1. No-book disclosure (verbatim, Req 4 AC 1)
        2. Reconciliation pair (verbatim, Req 4 AC 4)
        3. Per-break detail block, sorted by (break_type, security_id)
        4. Summary block  totals + per-type counts
        5. runtime_seconds=... (absolute last line, tail-friendly)
    """
    print("No book of record was supplied; reconciling custodian_a vs custodian_b only.")
    print("Reconciliation pair: custodian_a vs custodian_b")

    # Sort by (break_type, security_id) so reviewers can scan visually:
    # all ambiguous together, all missing together, all quantity together.
    # Ambiguous breaks (security_id is None) sort last within their type
    # via the "zzz" sentinel.
    sorted_breaks = sorted(breaks, key=lambda b: (b.break_type, b.security_id or "zzz"))
    print(f"\nBreaks ({len(breaks)}):")
    for b in sorted_breaks:
        print(_format_break_line(b))

    counts = Counter(b.break_type for b in breaks)
    print("\nSummary:")
    print(f"  Total breaks: {len(breaks)}")
    for break_type, count in sorted(counts.items()):
        print(f"  {break_type}: {count}")
    print(f"runtime_seconds={runtime_seconds:.3f}")


# ---------------------------------------------------------------------------
# Phase 6 — Layer-1 entrypoint (Req 4 AC 1, 4, 5; Req 5 AC 4; Req 6 AC 2)
# ---------------------------------------------------------------------------


def run_layer1(
    project_root: Path,
    out_dir: Path | None = None,
    as_of_date: date = date(2026, 1, 2),
    configured_year: int = 2026,
) -> list[Break]:
    """Orchestrate Ingest -> Reconcile -> write envelopes -> print summary.

    This is the Layer-1-only entry point. It MUST NOT import from
    ``code.agent`` or any Strands SDK module (Req 7 AC 2). The Phase 8
    ``code.run`` module calls this function first and then attempts the
    agent path on top, so a Bedrock auth failure still leaves both
    Layer-1 artifacts on disk.

    Args:
        project_root: Directory containing the three input CSVs.
        out_dir: Where to write the JSON artifacts. Defaults to
            ``project_root / "out"``. Tests pass a ``tmp_path``.
        as_of_date: Reconciliation date stamped into break_ids + metadata.
        configured_year: Authoritative year for year_mismatch warnings.

    Returns:
        The list of Break records (so callers like ``code.run`` can feed
        them to the agent without re-running ingest).
    """
    if out_dir is None:
        out_dir = project_root / "out"

    started = time.monotonic()

    input_paths = {
        "custodian_a.csv": project_root / "custodian_a.csv",
        "custodian_b.csv": project_root / "custodian_b.csv",
        "securities_reference.csv": project_root / "securities_reference.csv",
    }

    positions_a, warnings_a = ingest_custodian_a(
        input_paths["custodian_a.csv"], configured_year=configured_year
    )
    positions_b, warnings_b = ingest_custodian_b(
        input_paths["custodian_b.csv"], configured_year=configured_year
    )
    all_warnings = warnings_a + warnings_b

    resolver = IdentifierResolver(input_paths["securities_reference.csv"])
    breaks = reconcile(positions_a, positions_b, all_warnings, as_of=as_of_date, resolver=resolver)

    metadata = ArtifactMetadata(
        ruleset_version="0.1.0",
        code_commit=read_git_short_sha(),
        input_file_sha256s=compute_input_hashes(input_paths),
        as_of_date=as_of_date,
        generated_at=datetime.now(UTC),
    )

    write_envelope(out_dir / "raw_breaks.json", breaks, metadata)
    write_envelope(out_dir / "data_quality.json", all_warnings, metadata)

    runtime_seconds = time.monotonic() - started
    print_run_summary(breaks, runtime_seconds)

    return breaks


if __name__ == "__main__":
    # Resolve the project root by walking up from this file. The layout is:
    #   code/pipeline/reconcile.py  (this file)
    #   <repo-root>/                 (the project root)
    project_root = Path(__file__).resolve().parent.parent.parent
    run_layer1(project_root)
