# Project Structure

## Current Layout

```
GS-Technical-Case-Study/
  Instructions.md              # Assessment brief and deliverable requirements
  securities_reference.csv     # Canonical security master (20 equities)
  custodian_a.csv              # EOD positions from Custodian A (ticker-based)
  custodian_b.csv              # EOD positions from Custodian B (description-based)
```

## Expected Final Layout

```
GS-Technical-Case-Study/
  Instructions.md
  README.md                    # Primary deliverable: writeup, architecture, analysis
  pyproject.toml               # Project metadata + deps (managed by uv)
  uv.lock                      # Locked dependencies (committed)
  securities_reference.csv
  custodian_a.csv
  custodian_b.csv
  img/
    architecture.png           # System/data flow diagram(s)
  code/                        # Optional: implementation code
    run.py                     # Single entrypoint
    agent.py
    tools/
    pipeline/
      ingest.py
      reconcile.py
      report.py
    fixtures/                  # Mocked trade / corp action / FX data
    models.py                  # Pydantic v2 models
    tests/
  out/                         # Generated artifacts (gitignored)
```

## Conventions

- All written analysis goes in `README.md` at the root of `GS-Technical-Case-Study/` — not scattered across multiple docs
- Diagrams go in `img/` as image files (PNG or SVG preferred)
- Code goes in `code/` (or `src/`) — do not place scripts at the repo root
- Input data files (`*.csv`) stay at the `GS-Technical-Case-Study/` root; do not move or rename them
- No database files, `.env` files, or credentials should be committed

## Key File Roles

| File | Role |
|------|------|
| `securities_reference.csv` | Source of truth for security identity (security_id, ticker, name) |
| `custodian_a.csv` | Raw positions — ticker symbols, explicit LONG/SHORT, standard dates |
| `custodian_b.csv` | Raw positions — free-text descriptions, parenthetical negatives, mixed date formats |
| `README.md` | Primary deliverable — all narrative, architecture, and analysis lives here |
