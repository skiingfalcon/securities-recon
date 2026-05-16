"""Req 3 AC 5: a position present in only one custodian is one-sided.

The Reconciler emits exactly one ``missing_at_custodian`` Break that
labels the *absent* side, with ``custodian_quantity=None`` for that side
so downstream consumers can tell the difference between "quantity = 0"
and "no position reported".
"""

from datetime import date
from pathlib import Path

from code.pipeline.ingest import ingest_custodian_a, ingest_custodian_b
from code.pipeline.reconcile import reconcile
from code.tools.securities import IdentifierResolver

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_A = Path(__file__).parent / "fixtures" / "custodian_a_tsla_only.csv"
FIXTURE_B_EMPTY = Path(__file__).parent / "fixtures" / "custodian_b_empty.csv"
MASTER = PROJECT_ROOT / "securities_reference.csv"


def test_position_only_in_one_custodian_is_classified_as_one_sided() -> None:
    positions_a, warnings_a = ingest_custodian_a(FIXTURE_A, configured_year=2026)
    positions_b, warnings_b = ingest_custodian_b(FIXTURE_B_EMPTY, configured_year=2026)

    resolver = IdentifierResolver(MASTER)
    breaks = reconcile(
        positions_a,
        positions_b,
        warnings_a + warnings_b,
        as_of=date(2026, 1, 2),
        resolver=resolver,
    )

    missing = [b for b in breaks if b.break_type == "missing_at_custodian"]
    assert len(missing) == 1
    b = missing[0]
    assert b.security_id == "SEC0008"
    assert b.custodian == "custodian_b"
    assert b.custodian_quantity is None
    assert b.custodian_market_value is None
    assert b.position_type_custodian is None
