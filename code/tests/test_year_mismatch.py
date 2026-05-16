"""Req 1 AC 3; Req 3 AC 4: 2025 date in a 2026 EOD file emits year_mismatch.

The brief states positions are for Jan 2, 2026, but every row in the
sample custodian files shows 2025. We preserve the source-row truth (the
parsed date is kept as 2025) and surface the discrepancy as a structured
warning rather than silently rewriting the year.
"""

from pathlib import Path

from code.pipeline.ingest import ingest_custodian_b

FIXTURE = Path(__file__).parent / "fixtures" / "custodian_b_year.csv"


def test_2025_date_in_2026_eod_file_emits_year_mismatch_warning() -> None:
    positions, warnings = ingest_custodian_b(FIXTURE, configured_year=2026)

    assert len(positions) == 1
    assert positions[0].as_of_date.year == 2025

    year_warnings = [w for w in warnings if w.type == "year_mismatch"]
    assert len(year_warnings) == 1
    assert year_warnings[0].detail["parsed_year"] == 2025
    assert year_warnings[0].detail["configured_year"] == 2026
