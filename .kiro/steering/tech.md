# Tech Stack

## Scope Reminder (from `Instructions.md`)

This is a **2-hour case study**. A polished implementation is not expected. Architecture-only submissions are acceptable and even favored when accompanied by a diagram. The deliverable is a single `README.md`, an optional `code/` folder, and an `img/` folder for diagrams. Optimize for **thought leadership** and a clear answer to the second sub-problem: *how would you completely eliminate this problem for the business?*

Two distinct stacks live in this document: a **Demo Stack** that's small enough to write and run in two hours, and a **Production / Scale Stack** that lives in the README narrative and the architecture diagram.

## The Two Problems, and Why They Get Different Tools

The brief frames two sub-problems. They map cleanly to two different layers and we should not conflate them.

### Layer 1 — Deterministic Pipeline (no LLM)
For work with one correct answer.
- Parse each custodian CSV
- Normalize identifiers (description/ticker → `security_id`), dates (→ ISO 8601), signs (parenthesized negatives → signed ints), position_type (LONG/SHORT)
- Join custodian positions to the security master and to the fund's book of record
- Emit a raw break set: missing positions, extra positions, quantity deltas, market-value deltas, position-type mismatches

### Layer 2 — Agentic Resolution (Strands Agents SDK)
For work that currently requires ops judgment: pulling trade tickets, checking corporate action calendars, reasoning about settlement timing, FX, custodian errors. The agent receives the raw break set, investigates each break with tools, produces a classified and explained record, auto-clears what it can confidently resolve, and escalates the rest with a documented reasoning trail.

### Layer 3 — Eliminate (architecture writeup, not code)
The brief's ambition is to *eliminate* recon, not just automate it. That answer lives in the README and the diagram. Key moves:
- Replace EOD CSV batch with **real-time position streams** (FIX, ISO 20022, custodian APIs) so breaks surface intra-day instead of next morning
- Adopt **standard security identifiers** (FIGI, ISIN, CUSIP) so description matching disappears as a problem class
- Push toward **straight-through processing** — books reconciled continuously, exceptions-only workflow
- The agent owns *only* the residual exceptions that survive structural fixes

**Honest framing for the README.** "Completely eliminate" is the brief's framing, but a credible answer doesn't promise zero breaks. Structural fixes drive *volume* down by orders of magnitude; corporate actions, custodian errors, and settlement edge cases will always produce residual exceptions. The right claim is: *eliminate the structural causes, manage the residual via agentic exception handling, and prove it with a falling break rate over time.*

## Pivots and Rejected Alternatives (write into the README)

The brief explicitly invites a "considered and rejected" narrative. Each alternative below should appear in the README with a short rationale:

- **RPA (UI automation over the existing manual process)** — automates the symptom, not the cause; brittle to custodian portal changes; doesn't shrink the break population.
- **Pure rules engine for classification** — handles known break types but fails on novel ones; ops still trace anything new. The agent generalizes; the rules engine doesn't.
- **Fully autonomous agent that mutates the book** — unacceptable risk surface for a financial system. Our agent recommends and audits only; state-changing actions require human approval at the tool boundary.
- **OpenAI / multi-provider model layer** — considered for reviewer convenience; rejected to keep the AWS architecture story coherent end-to-end. Strands `BedrockModel` would make a swap trivial later if needed.
- **Polars over pandas for Layer 1** — faster on large datasets but irrelevant at this scale (~10–20 rows). Reviewer familiarity wins. Keep it as a scaling option.
- **Custom ML classifier instead of an LLM agent** — viable for break categorization, but the *explanation* of a break is as valuable as the category, and LLMs produce both with the same call. Pivoted to LLM agent for that reason.
- **DynamoDB / S3 / Step Functions in the demo** — over-engineering for a 2-hour deliverable. Local files in the demo; cloud in the production stack.

## Demo Stack (what we actually build in two hours)

| Concern | Choice |
|---|---|
| Language | **Python 3.13+** (current stable, broad ecosystem support, safe for AWS Lambda / AgentCore) |
| Env + package mgmt | **uv** (replaces pip + venv; lockfile-driven; the 2026 default) |
| Project metadata | **`pyproject.toml`** + `uv.lock` (no `requirements.txt`) |
| Lint + format | **ruff** (replaces black + isort + flake8 + pylint) |
| Tests | `pytest` |
| Data processing | `pandas` (familiar; dataset is tiny so speed differences vs. `polars` are irrelevant here) |
| Fuzzy matching | `rapidfuzz` (Rust-backed; modern successor to `thefuzz`) for description → ticker mapping (Custodian B) |
| Date parsing | `dateutil.parser` for mixed custodian date formats (`2025-01-02`, `02-JAN-2025`, `1/2/25`) |
| Models / validation | **Pydantic v2** for `Position`, `Break`, `Resolution`, and agent tool I/O |
| Storage | **Local filesystem** — CSV inputs, JSON intermediates, JSON/CSV outputs in `out/` |
| Agent SDK | Strands Agents SDK (`@tool` decorator pattern) |
| Model | **Claude Sonnet on Amazon Bedrock** via Strands `BedrockModel`. Authenticated with local AWS credentials (the same ones used for the AWS CLI). Reviewers with Bedrock access can run the demo as-is. |
| Mocked tools | `get_recent_trades`, `get_corporate_actions`, etc. return fixtures from `code/fixtures/` |
| Orchestration | Single Python entrypoint (`python -m code.run`) |
| Region | `us-west-2`, specified explicitly (Bedrock model invocations and any AWS CLI calls) |

### Why Bedrock-Only

Sticking with one model provider keeps the narrative tight: the demo is a runnable slice of the production architecture, not a parallel track. The only AWS dependency the reviewer needs is Bedrock model access in `us-west-2`; everything else (DynamoDB, AgentCore, Step Functions) stays in the writeup. Reviewers without Bedrock access can still run the deterministic pipeline standalone — Layer 1 produces a meaningful artifact (`out/raw_breaks.json`) on its own.

The demo runs end-to-end on a laptop with local AWS credentials configured, against the three CSVs in the repo, and produces:
- `out/raw_breaks.json` — output of Layer 1
- `out/resolved_breaks.json` — output of Layer 2 with reasoning trace per break
- `out/report.csv` — human-readable summary

If a reviewer doesn't have Bedrock access, the deterministic pipeline (Layer 1) runs standalone and produces `out/raw_breaks.json` on its own.

## Agent Tools (`@tool` decorator)

Each tool is single-purpose, fully type-hinted, with Google-style docstrings. For the demo, tools that would call real systems return fixture data; the *interfaces* are what we're communicating.

- `lookup_security(query: str) -> SecurityMatch` — fuzzy-match description or ticker to the security master (real, not mocked — uses `rapidfuzz`)
- `get_recent_trades(security_id: str, since: date) -> list[Trade]` — explain quantity deltas via settlement timing *(mocked from fixtures)*
- `get_corporate_actions(security_id: str, date_range: tuple) -> list[CorpAction]` — splits, dividends, mergers, spin-offs *(mocked)*
- `get_settlement_status(trade_id: str) -> SettlementStatus` — T+1/T+2 state *(mocked)*
- `get_fx_rate(ccy: str, as_of: date) -> float` — for cross-currency valuation breaks *(mocked)*
- `classify_break(break_record: dict) -> BreakCategory` — settlement_timing | corporate_action | fx | custodian_error | data_quality | unknown
- `propose_resolution(break_record: dict, evidence: list) -> Resolution` — recommendation with confidence
- `escalate_to_human(break_record: dict, context: dict) -> EscalationTicket` — when confidence is below threshold (writes to `out/escalations.json`)

State-changing actions (book updates, payments) are explicitly **not** in the tool surface. Enforce that at the tool boundary, not via prompting.

## Production / Scale Stack (writeup only — do not build)

When this graduates from a demo to a daily ops workload, the stack changes shape. Document this in the README architecture section; do not code it.

| Concern | Production Choice | Why |
|---|---|---|
| Agent runtime | **Amazon Bedrock AgentCore** | Managed sessions, identity, observability |
| Foundation models | Claude Sonnet (resolution); Haiku (classification) — same `BedrockModel` integration as the demo, just managed by AgentCore | Pick smallest model meeting accuracy bar; promotion to production reuses the demo's model wiring unchanged |
| Guardrails | **Amazon Bedrock Guardrails** | PII redaction, denied topics, prompt-injection defense |
| Source-of-truth state | **DynamoDB** | Positions, breaks, decisions; single-digit-ms reads, fits the per-break-key access pattern |
| Audit trail | DynamoDB Streams → S3 with object lock | Immutable, regulator-friendly |
| File archive | S3 | Raw custodian files, daily reports |
| Workflow | Step Functions | Daily orchestration of ingest → reconcile → agent → notify |
| Notification / approval | EventBridge → Slack approval card or ServiceNow ticket | Human-in-the-loop surface |
| Observability | CloudWatch Logs + Metrics, X-Ray, AgentCore session traces | Token cost emitted as a custom metric |
| Evaluation | Strands Evals SDK against a labeled break set | Precision/recall gates before any auto-clear |
| Identifiers | Bloomberg FIGI / ISIN / CUSIP master | Eliminate description-matching breaks structurally |

## Trust, Safety, and Production Readiness (writeup section)

Non-negotiables before any of this touches real positions:

- **Bedrock Guardrails** configured before first deploy
- **Evaluation harness** with labeled historical breaks; no auto-clear until precision/recall meet a stated bar
- **Human-in-the-loop** with confidence + dollar thresholds; auto-clear only when *both* are below limit
- **Immutable audit trail** of inputs, tools called, evidence, model output, confidence, and human action
- **Cost ceiling** per session and per daily run, enforced and alarmed
- **Determinism where possible** — Layer 1 must not call the LLM

## MCP Tooling Available in This Workspace

Configured in `~/.kiro/settings/mcp.json` and currently enabled:

| Server | Purpose | When to use |
|---|---|---|
| `aws-core` (`awslabs.core-mcp-server`) | AWS service discovery and meta-guidance | Use first when planning an AWS integration |
| `aws-docs` (`awslabs.aws-documentation-mcp-server`) | Search and read official AWS docs | Required before writing AWS code; `search_documentation` auto-approved |
| `awslabs.aws-pricing-mcp-server` | Pull current AWS pricing | Use to build the cost-per-reconciliation estimate in the README scaling section |
| `strands-agents` (`strands-agents-mcp-server`) | Search Strands Agents SDK docs | Required before writing Strands code (`@tool`, agent config, MCP tools) |

No generic `fetch` MCP is configured. For non-AWS, non-Strands sources, use the built-in `web_fetch` tool.

Documentation-first rule: search `aws-docs` and `strands-agents` before writing integration code; cite the page in code comments.

## Project Layout

```
GS-Technical-Case-Study/
  pyproject.toml           # Project metadata + dependencies (uv-managed)
  uv.lock                  # Locked dependency versions (committed)
  code/
    run.py                 # Single entrypoint: pipeline + agent
    agent.py               # Strands agent definition + model config
    tools/                 # @tool implementations
      securities.py
      trades.py
      corporate_actions.py
      classification.py
    pipeline/
      ingest.py            # parse + normalize each custodian file
      reconcile.py         # deterministic join and raw break detection
      report.py            # final break report formatting
    fixtures/              # mocked trade tickets, corp actions, FX rates
    models.py              # Pydantic v2 models (Position, Break, Resolution)
    tests/
  out/                     # generated artifacts (gitignored except samples)
```

For the 2-hour deliverable, a realistic minimum is `pipeline/ingest.py` + `pipeline/reconcile.py` + a stub `agent.py` showing the `@tool` shape and one or two mocked tools. Everything else is described in the README.

## Common Commands

**Prerequisite:** AWS credentials configured for an account with Bedrock access in `us-west-2`. Confirm with `aws sts get-caller-identity --no-cli-pager`. Anthropic Claude models must be enabled in the Bedrock console for that account.

```bash
# One-time: install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies into a managed venv (creates uv.lock if missing)
uv sync

# Run the deterministic pipeline only (no AWS needed)
uv run python -m code.pipeline.reconcile

# Full demo: pipeline + agent (uses Bedrock — requires AWS credentials with Bedrock access in us-west-2)
uv run python -m code.run

# Lint + format
uv run ruff check .
uv run ruff format .

# Run tests
uv run pytest -v
```

When invoking AWS CLI directly, always include `--region us-west-2 --no-cli-pager`.

## Data Format Conventions

- Internal date format: ISO 8601 (`YYYY-MM-DD`)
- Quantities: signed integers (short = negative)
- Market values: signed floats in USD; FX-converted at the as-of date when source is non-USD
- Security identity: always resolve to `security_id` from the master before comparing; never compare on raw ticker or description

## Code Standards

- Type hints required on every function signature
- Google-style docstrings with Args/Returns/Raises
- Educational inline comments explaining *why*, especially for break-classification logic
- No hardcoded credentials; AWS credentials come from the standard credential chain (environment variables, `~/.aws/credentials`, or IAM role)
- Structured logging with `break_id` and `security_id` as context on every log line

## Out of Scope for the Agent

The agent does not write to the book of record, does not move money, and does not modify custodian data. Outputs are recommendations and audit-trail entries. Any state-changing action requires explicit human approval — enforced at the tool boundary, not the prompt.
