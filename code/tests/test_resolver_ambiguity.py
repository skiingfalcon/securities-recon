"""Req 2 AC 4, 5; Req 3 AC 2: "Alphabet Inc" is ambiguous between GOOGL and GOOG.

The resolver MUST refuse to pick a silent winner when the top-two
candidates are within ``AMBIGUITY_EPSILON`` of each other. The Reconciler
then surfaces that as an ``identifier_ambiguous`` Break so a human can
disambiguate.
"""

from datetime import date
from pathlib import Path

from code.models import Position
from code.pipeline.reconcile import reconcile
from code.tools.securities import IdentifierResolver

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MASTER = PROJECT_ROOT / "securities_reference.csv"


def test_alphabet_inc_is_ambiguous_between_googl_and_goog_class_shares() -> None:
    resolver = IdentifierResolver(MASTER)
    match = resolver.resolve("Alphabet Inc", query_kind="description")

    assert match.security_id is None
    assert match.reason == "ambiguous_top_two"

    alt_ids = {a["security_id"] for a in match.alternatives}
    assert alt_ids == {"SEC0004", "SEC0005"}

    position = Position(
        custodian="custodian_b",
        raw_query="Alphabet Inc",
        quantity=8000,
        market_value=1_360_000.0,
        position_type="LONG",
        as_of_date=date(2026, 1, 2),
        source_row_index=0,
        raw_source_row={"security_description": "Alphabet Inc"},
    )
    breaks = reconcile([], [position], [], as_of=date(2026, 1, 2), resolver=resolver)

    ambiguous = [b for b in breaks if b.break_type == "identifier_ambiguous"]
    assert len(ambiguous) == 1
    assert ambiguous[0].security_id is None
    assert ambiguous[0].custodian == "custodian_b"
