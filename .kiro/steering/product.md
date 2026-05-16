# Product

This is a **Grayscale Technical Case Study** — a financial operations tool for automating end-of-day position reconciliation across multiple custodians.

## Domain

A small fund's ops team currently reconciles positions manually every morning before markets reopen. The goal is to automate that process — and, ideally, to *eliminate* the structural causes of breaks rather than just speed up the hunt.

**Core workflow:**
1. Ingest end-of-day position files from multiple custodians (each with different schemas and formatting quirks)
2. Normalize positions against a canonical security master (`securities_reference.csv`)
3. Identify and surface mismatches (quantity, market value, position type, missing/extra positions)
4. Classify and explain breaks — settlement timing, corporate actions, FX, custodian errors, etc.

## Key Data Sources

- `securities_reference.csv` — canonical security master (security_id, ticker, name, asset_class)
- `custodian_a.csv` — positions using ticker symbols, explicit LONG/SHORT position_type
- `custodian_b.csv` — positions using security descriptions, parentheses for negative values, inconsistent date formats

## Specific Observations from the Provided Files

These are the concrete things a domain expert would flag after reading the three CSVs. The README should surface them explicitly.

### Data quality / formatting
- **Date year discrepancy.** `Instructions.md` says positions are end-of-day for **Jan 2, 2026**, but every row in both custodian files shows year **2025** (`01/02/2025`, `1/2/25`, `2025-01-02`, `02-JAN-2025`). Either the brief has a typo or this is a deliberate signal — call it out as a data-quality issue worth raising before processing.
- **Custodian B encodes shorts as parenthesized negatives** (`(5000)`, `(4250000)`, `(10)`, `(6500000)`). Common in finance exports; trivial to parse but easy to miss.
- **Custodian B uses free-text security descriptions** with no consistent format: `"Apple Inc Common Stock"`, `"Microsoft Corp"`, `"NVIDIA CORP"`, `"Amazon.com, Inc."`, `"Visa Inc Class A"`. Requires fuzzy match → ticker → security_id mapping.
- **Custodian A column `trade_date` is misleading** — for an EOD position file this is really an `as_of_date`, not a trade date. Worth flagging in the writeup.
- **Four different date formats** in Custodian B alone: `01/02/2025`, `1/2/25`, `2025-01-02`, `02-JAN-2025`.

### Identification ambiguity
- **"Alphabet Inc"** in Custodian B is ambiguous: the security master has both `GOOGL` (Class A) and `GOOG` (Class C). Fuzzy string match alone cannot disambiguate share classes; this is a perfect example of why FIGI/ISIN/CUSIP matter and why description-based matching is fragile.
- **`BRK.A` vs Custodian B's "Berkshire Hathaway Class A Inc"** — the dot in the ticker is a known PITA across systems and a frequent source of identifier-mismatch breaks.

### Position-level anomalies (real or apparent breaks)
- **AAPL**: Custodian A shows 75,000 / Custodian B shows 25,000. Likely different fund sleeves, possibly a real break. Cannot be resolved without the book of record.
- **AMZN**: A=15,000 / B=4,000. Largest *proportional* delta in the file (~73%). Same sleeve-vs-break ambiguity as AAPL.
- **BRK.A**: A=30 LONG / B=-10 SHORT (parens in the source). This is a *position_type_mismatch*, not just a quantity delta — the sides disagree on direction. The quantity values themselves are also tiny because BRK.A trades near $700k/share, so delta dollars matter more than delta shares.
- **NVDA**: A=10,000 LONG / B=-5,000 SHORT. Position-type flip; same diagnostic class as BRK.A.
- **META**: 20,000 LONG at A only — completely absent from B. Worth surfacing because it's a top-10 holding by market value (~$12M).
- **TSLA, JPM**: only on Custodian A (TSLA short, JPM short).
- **BAC, SHOP, MA**: only on Custodian B.
- **MSFT**: A=18,000 / B=15,000 — quantity delta with no obvious explanation.
- **V**: A=5,000 / B=3,000.
- **GOOGL**: present at A as a clean ticker resolution. The `"Alphabet Inc"` row at B *could* be the B-side counterpart but cannot be confidently assigned to GOOGL or GOOG (see Identification ambiguity above), so it's a separate `identifier_ambiguous` break and GOOGL itself appears one-sided at A.

These are not "the answer" — they're the questions the recon system must surface. Without the book of record we cannot say which are legitimate sleeve splits and which are true breaks. *That's the point.*

#### Expected break ledger (full reconciliation against the supplied data)

When the deterministic Reconciler runs against the three CSVs, the headline numbers a reviewer should see are:

| break_type | Count | Securities |
|---|---|---|
| `position_type_mismatch` | 2 | NVDA, BRK.A |
| `quantity_mismatch` | 4 | AAPL, AMZN, MSFT, V |
| `missing_at_custodian` | 7 | META, TSLA, JPM, GOOGL (B-side absent), BAC, SHOP, MA |
| `identifier_ambiguous` | 1 | "Alphabet Inc" (GOOGL vs GOOG) |
| **Total** | **14** | |

Note: NVDA and BRK.A also have non-zero quantity deltas alongside their direction flip; the canonical Break record carries both `position_type_*` and `quantity_*` fields populated on the same row so the value is not lost (see #[[file:.kiro/specs/recon-demo-thought-leadership-hooks/design.md]] §5.C).

## Canonical Break Record (sub-problem 1's primary artifact)

Sub-problem 1 asks how we'd represent and process the data. The deterministic pipeline emits a normalized break record per security, per custodian. Sketch:

```
Break {
  break_id: str                       # stable hash of (as_of_date, security_id, custodian)
  as_of_date: date                    # ISO 8601
  security_id: str                    # resolved against the master; never a raw ticker
  custodian: str                      # "custodian_a" | "custodian_b"
  break_type: enum                    # missing_in_book | missing_at_custodian
                                      # | quantity_mismatch | value_mismatch
                                      # | position_type_mismatch | identifier_unresolved
  book_quantity: int | None           # signed
  custodian_quantity: int | None      # signed
  quantity_delta: int | None
  book_market_value: float | None
  custodian_market_value: float | None
  value_delta: float | None
  position_type_book: "LONG" | "SHORT" | None
  position_type_custodian: "LONG" | "SHORT" | None
  raw_source_row: dict                # original custodian record, for audit
  ingest_warnings: list[str]          # e.g. "non-ISO date coerced", "ticker dot normalized"
}
```

Layer 2 enriches each break with `classification`, `evidence`, `confidence`, `proposed_resolution`, and `human_action_required`. Pydantic v2 model definitions live in `code/models.py`.

## Open Questions for the Business (must be in the README)

These are the questions a real engagement starts with. They're as important to the deliverable as any code.

**Operating context**
- What's the current daily break volume and ops headcount? (Sizes the business case.)
- What's the SLA between custodian file arrival and market open?
- What's the book of record — OMS or PMS? Is there a read API, or is it also a file drop?
- Which custodians? (Goldman PB, BNY, State Street, IBKR, Coinbase Custody all have very different integration paths.)
- Asset classes in scope? Equities only here; derivatives, FX, fixed income, **crypto** (Grayscale-relevant) are very different problems.
- Multi-entity, multi-currency, multi-fund?

**Break composition**
- Today's mix: roughly what % of breaks are settlement timing vs. corporate action vs. FX vs. custodian error vs. data formatting?
- What do "cleared" and "understood-but-not-cleared" look like operationally — is there a ticket system? An audit trail?

**Risk and compliance**
- Audit/regulatory retention requirements? (SEC 17a-4? SOC 2?)
- Dollar threshold for auto-clear? (Confidence threshold alone is not enough.)
- Who is the human-in-the-loop, and through what surface (Slack? ServiceNow? email?)?

**Strategic**
- Is the goal to *automate the existing process* (low-risk, ~6 months) or to *eliminate the problem class* (multi-year, requires custodian renegotiation, real-time feeds, standard identifiers)?
- Budget envelope for the build? Recurring run cost ceiling?

## What the README Must Cover

The brief weights "thought leadership" and "how you think" above shippable code. The README should be structured to make those signals easy to find:

1. **Summary** — one-paragraph problem statement and proposed approach.
2. **Specific observations** — the bullets in *Specific Observations* above; what stood out from the actual data.
3. **Open questions** — the list above; what we would ask before building.
4. **Sub-problem 1: data representation** — the canonical break record, normalization rules, security-master resolution strategy.
5. **Sub-problem 2: architecture** — Layer 1 (deterministic) + Layer 2 (agentic) + Layer 3 (eliminate). One diagram in `img/`.
6. **Where AI fits — and where it doesn't.** AI is for judgment-heavy break investigation, not for arithmetic. Layer 1 must be deterministic. Be explicit about this boundary.
7. **Pivots and rejected alternatives** — what we considered and didn't choose, and why. The brief explicitly invites this.
8. **Honest framing of "eliminate."** Structural fixes (real-time feeds, FIGI/ISIN/CUSIP, STP) reduce break volume by orders of magnitude. *Residual exceptions will always exist* (corp actions, custodian errors, settlement edge cases) and the agent owns those. Avoid claiming we can drive the number to zero.
9. **Phased roadmap** — today → 6 months → 18 months → 3 years. Eliminate is a program, not a sprint.
10. **Cost framing** — back-of-envelope per-run and per-day cost. Use the AWS pricing MCP server.
11. **Trust, safety, evaluation** — Guardrails, eval harness, HITL thresholds, audit trail.
12. **What I'd do next** — given another two weeks vs. another two quarters.

## Deliverables (per Instructions)

- `README.md` — written summary, architecture, analysis, questions, next steps (structure above)
- `code/` or `src/` — any implementation code
- `img/` — architecture diagrams
