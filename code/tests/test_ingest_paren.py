"""Req 3 AC 1: paren-negative coercion in Custodian B is parsed as a SHORT.

The accounting convention of writing short positions as ``(5000)`` MUST be
decoded to ``-5000`` AND surfaced as a ``paren_negative_coerced`` warning
so the coercion is never silent.
"""

from pathlib import Path

from code.pipeline.ingest import ingest_custodian_b

FIXTURE = Path(__file__).parent / "fixtures" / "custodian_b_paren.csv"


def test_paren_quantity_in_custodian_b_is_parsed_as_short_position() -> None:
    positions, warnings = ingest_custodian_b(FIXTURE, configured_year=2026)

    assert len(positions) == 1
    position = positions[0]
    assert position.quantity == -5000
    assert position.position_type == "SHORT"

    paren_warnings = [w for w in warnings if w.type == "paren_negative_coerced"]
    # Two paren_negative_coerced warnings: one for shares, one for market_value.
    # Req 3 AC 1 specifies "exactly one" warning for the shares field; we assert
    # that the shares-field warning is present and carries the correct payload.
    shares_warnings = [w for w in paren_warnings if w.detail.get("raw") == "(5000)"]
    assert len(shares_warnings) == 1
    assert shares_warnings[0].source_file == "custodian_b_paren.csv"
    assert shares_warnings[0].source_row_index == 0
