"""Layer 2 @tool: corporate-actions lookup (mocked for the demo).

In production this would query a vendor feed (e.g. Bloomberg CACS,
DTCC). For the demo we keep a small inline dict so the agent can
demonstrate the "check the corp-actions calendar to explain a holding
size change" workflow without external dependencies.
"""

from strands import tool

# Mocked corp-action calendar keyed by security_id. Kept deliberately
# sparse so the agent's verdicts stay legible — adding overlapping corp
# actions on the quantity_mismatch securities would muddy the
# settlement-timing story those breaks already have via trades.py.
_CORPORATE_ACTIONS: dict[str, list[dict]] = {
    # META: special dividend ex-date matches the recon date. Partial
    # explanation for "missing at B" — positions can move during a
    # special-div ex-date workflow if one custodian books the entitlement
    # ahead of the other. Agent should still flag this for human review
    # (investigate verdict expected).
    "SEC0007": [
        {
            "ex_date": "2026-01-02",
            "action_type": "SPECIAL_DIVIDEND",
            "ratio": None,
            "cash_per_share": 1.50,
        },
    ],
    # BAC: merger-consideration event effective on the recon date.
    # Positions are typically removed from the holdings books once the
    # consideration is paid. Combined with no recent trade ticket
    # (trades.py deliberately leaves SEC0014 empty) this explains why
    # BAC is missing at custodian_a but still present at custodian_b
    # (one side processed the corp action before the other).
    "SEC0014": [
        {
            "ex_date": "2026-01-02",
            "action_type": "MERGER_CONSIDERATION",
            "ratio": None,
            "cash_per_share": 40.00,
            "notes": (
                "Cash + stock consideration; positions removed from holdings on effective date."
            ),
        },
    ],
}


@tool
def lookup_corporate_actions(security_id: str, as_of_date: str) -> dict:
    """Return corporate actions effective on or before ``as_of_date``.

    Used by the agent to explain holding-size changes via splits,
    spinoffs, mergers, dividends. Returns an empty list when no actions
    are on file.

    Args:
        security_id: Canonical security identifier from the master.
        as_of_date: ISO 8601 date string (YYYY-MM-DD) — the reconciliation
            as-of date; actions after this date are excluded.

    Returns:
        dict with "security_id", "as_of_date", and "actions" (list).
    """
    actions = [
        a
        for a in _CORPORATE_ACTIONS.get(security_id, [])
        if a.get("ex_date", "9999-12-31") <= as_of_date
    ]
    return {"security_id": security_id, "as_of_date": as_of_date, "actions": actions}
