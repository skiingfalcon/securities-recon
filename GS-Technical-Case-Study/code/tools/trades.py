"""Layer 2 @tool: recent-trades lookup (mocked for the demo).

In production this would query the OMS or a trade-blotter API. For the
demo we keep a small inline dict so the agent can demonstrate the
"pull recent trades to explain a quantity break" workflow without
external dependencies.
"""

from strands import tool

# Mocked "blotter" keyed by security_id. Each entry is a list of recent
# trade tickets the agent can use to explain a quantity break (e.g.
# "AAPL: bought 50000 shares yesterday, custodian_b hasn't settled yet").
_RECENT_TRADES: dict[str, list[dict]] = {
    # --- quantity_mismatch breaks ---
    # AAPL: A=75k, B=25k. BUY ticket on T+1 explains the +50k delta at A.
    "SEC0001": [
        {
            "trade_date": "2026-01-02",
            "side": "BUY",
            "quantity": 50_000,
            "venue": "NASDAQ",
            "settlement_status": "pending_t1",
        },
    ],
    # MSFT: A=18k, B=15k. BUY ticket on T+1 explains the +3k delta at A.
    "SEC0002": [
        {
            "trade_date": "2026-01-02",
            "side": "BUY",
            "quantity": 3_000,
            "venue": "NASDAQ",
            "settlement_status": "pending_t1",
        },
    ],
    # AMZN: A=15k, B=4k. BUY ticket on T+1 explains the +11k delta at A.
    "SEC0006": [
        {
            "trade_date": "2026-01-02",
            "side": "BUY",
            "quantity": 11_000,
            "venue": "NASDAQ",
            "settlement_status": "pending_t1",
        },
    ],
    # V: A=5k, B=3k. BUY ticket on T+1 explains the +2k delta at A.
    "SEC0012": [
        {
            "trade_date": "2026-01-02",
            "side": "BUY",
            "quantity": 2_000,
            "venue": "NASDAQ",
            "settlement_status": "pending_t1",
        },
    ],
    # --- one-sided breaks (missing_at_custodian) ---
    # TSLA: SHORT at A only. SELL_SHORT ticket on T+1 — locate not yet
    # confirmed at custodian_b, hence the absence.
    "SEC0008": [
        {
            "trade_date": "2026-01-02",
            "side": "SELL_SHORT",
            "quantity": 8_000,
            "venue": "NASDAQ",
            "settlement_status": "pending_t1",
        },
    ],
    # JPM: SHORT at A only. SELL_SHORT ticket on T+1, similar story to TSLA.
    "SEC0011": [
        {
            "trade_date": "2026-01-02",
            "side": "SELL_SHORT",
            "quantity": 5_000,
            "venue": "NYSE",
            "settlement_status": "pending_t1",
        },
    ],
    # MA: only at B. Position transferred IN from a prior custodian and
    # settled at custodian_b on T-1; custodian_a never saw it.
    "SEC0013": [
        {
            "trade_date": "2026-01-01",
            "side": "TRANSFER_IN",
            "quantity": 4_000,
            "venue": "DTC_TRANSFER",
            "settlement_status": "settled",
        },
    ],
    # SHOP: only at B. Same transfer-in story as MA.
    "SEC0015": [
        {
            "trade_date": "2026-01-01",
            "side": "TRANSFER_IN",
            "quantity": 18_000,
            "venue": "DTC_TRANSFER",
            "settlement_status": "settled",
        },
    ],
    # Deliberately empty: SEC0003 (NVDA position_type_mismatch — §11
    # never auto-clearable), SEC0009 (BRK.A — B-side ambiguous, escalate),
    # SEC0004 (GOOGL — no plausible settlement story, escalate),
    # SEC0014 (BAC — corp-actions tool carries the explanation, see
    # corporate_actions.py).
}


@tool
def get_recent_trades(security_id: str) -> dict:
    """Return recent trade tickets for a given security_id.

    Used by the agent to explain quantity breaks via settlement timing
    (e.g. a T+1 trade booked at the OMS but not yet settled at the
    custodian). Returns an empty list when no recent trades are on file.

    Args:
        security_id: The canonical security identifier from the master
            (e.g. "SEC0001" for AAPL).

    Returns:
        dict with "security_id" and "trades" (list of ticket dicts).
    """
    return {"security_id": security_id, "trades": _RECENT_TRADES.get(security_id, [])}
