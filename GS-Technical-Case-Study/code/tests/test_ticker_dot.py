"""Req 2 AC 6; Req 3 AC 3: BRK.A's period is preserved end-to-end.

Stripping the dot would turn ``BRK.A`` into ``BRKA`` and miss the master
entry for SEC0009, silently breaking the join. The ingest layer MUST
preserve the dot (and emit a ``ticker_dot_preserved`` warning) and the
resolver MUST find the master entry verbatim.
"""

from pathlib import Path

from code.pipeline.ingest import ingest_custodian_a
from code.tools.securities import IdentifierResolver

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "custodian_a_brka.csv"
MASTER = PROJECT_ROOT / "securities_reference.csv"


def test_brka_dot_in_ticker_is_preserved_through_normalization() -> None:
    positions, warnings = ingest_custodian_a(FIXTURE, configured_year=2026)

    assert len(positions) == 1
    assert positions[0].raw_query == "BRK.A"

    dot_warnings = [w for w in warnings if w.type == "ticker_dot_preserved"]
    assert len(dot_warnings) == 1
    assert dot_warnings[0].detail["preserved"] == "BRK.A"

    resolver = IdentifierResolver(MASTER)
    match = resolver.resolve("BRK.A", query_kind="ticker")
    assert match.security_id == "SEC0009"
    assert match.reason == "exact_ticker"
