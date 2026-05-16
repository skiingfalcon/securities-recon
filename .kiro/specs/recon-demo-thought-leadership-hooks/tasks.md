# Implementation Plan

## Overview

This plan executes the design in #[[file:.kiro/specs/recon-demo-thought-leadership-hooks/design.md]] against the requirements in #[[file:.kiro/specs/recon-demo-thought-leadership-hooks/requirements.md]]. Phases are sequenced so a partial completion still ships value: Phases 0 through 6 produce a working Layer 1 with thought-leadership hooks, Phase 7 makes it review-ready, Phase 8 is the Layer 2 bonus, and Phase 9 polishes for delivery and authors the brief's primary deliverables (`README.md`, `img/architecture.png`).

The 80-LOC executable cap from Req 7 AC 1 covers the *delta* over the baseline pipeline in #[[file:.kiro/steering/tech.md]] across Requirements 1 through 5. All paths follow design.md §3 (Components and Interfaces) and tech.md "Project Layout"; do not invent new modules.

Layer 1 modules (`code/pipeline/`, `code/models.py`, `code/tools/securities.py`, `code/tests/`) MUST NOT import from `code/agent.py` or any Strands SDK module. Layer 2 work lives in `code/run.py` and `code/agent.py`.

## Task Dependency Graph

Dependencies flow forward only. Phase N depends only on Phases 0..N-1. Phases 0–3 are already complete; the active work starts at Phase 4.

```json
{
  "waves": [
    {"wave": 1, "name": "Phase 0–3 — COMPLETE", "tasks": ["1","2","3","4","5","6","7","8","9","10","11","12","13","14","15","16"]},
    {"wave": 2, "name": "Phase 4 — Reconciler", "tasks": ["17","18","19","20","21"], "depends_on": [1]},
    {"wave": 3, "name": "Phase 5 — OutputArtifact envelope", "tasks": ["22","23","24","25","26"], "depends_on": [2]},
    {"wave": 4, "name": "Phase 6 — Run_Summary + Layer-1 entrypoint", "tasks": ["27","28","29"], "depends_on": [3]},
    {"wave": 5, "name": "Phase 7 — Tests", "tasks": ["30","31","32","33","34","35"], "depends_on": [4]},
    {"wave": 6, "name": "Phase 8 — Layer 2 (optional)", "tasks": ["36","37","38","39","40"], "depends_on": [5]},
    {"wave": 7, "name": "Phase 9 — Verify and ship", "tasks": ["41","42","43","44","45","46"], "depends_on": [5]}
  ]
}
```

Stop-points that still deliver value: end of Phase 6 (working Layer 1, both JSON artifacts, no-book-of-record line); end of Phase 7 (review-ready, full test suite green); end of Phase 9 with Phase 8 skipped (Layer 1 only, lint/format/tests clean, README + diagram authored).

## Current Status

**Phases 0–4 are complete.** The following files are fully implemented:
- `pyproject.toml`, `uv.lock`, `.gitignore`, `out/` — Phase 0 ✅
- `code/models.py` — Phase 1 ✅
- `code/pipeline/ingest.py` — Phase 2 ✅ (all helpers + both custodian entrypoints)
- `code/tools/securities.py` — Phase 3 ✅ (IdentifierResolver with ambiguity detection)
- `code/pipeline/reconcile.py` — Phase 4 ✅ (reconcile() + all five break-type branches; task 21 smoke verified — see borderline note below)

**Phases 5–9 are pending.** The following are stubs:
- `code/run.py` — stub only
- `code/agent.py` — stub only
- `code/pipeline/report.py`, `code/tools/{trades,corporate_actions,classification}.py` — stubs
- `code/tests/` — empty (only `__init__.py`)

**Task 21 smoke result (recorded 2026-05-16):** Total = **15** breaks, not 14. Counter = `{missing_at_custodian: 8, quantity_mismatch: 4, identifier_ambiguous: 2, position_type_mismatch: 1}`. The borderline case from task 16 fires: `"Berkshire Hathaway Class A Inc"` resolves as `ambiguous_top_two` against SEC0009 vs SEC0010 (top-two delta below `AMBIGUITY_EPSILON=0.05`). As a result, BRK.A from custodian_a appears as `missing_at_custodian` rather than contributing to a `position_type_mismatch` with the B-side row. Expected break ledger in `product.md` needs an asterisk for BRK.A; behavior is correct per Req 2 AC 4.

## Tasks

### Phase 0 — Project bootstrap ✅ COMPLETE

- [x] 1. Initialize uv project and pin dependencies in `pyproject.toml`
- [x] 2. Create the `code/` package skeleton and `code/tests/fixtures/` directory
- [x] 3. Create `out/` directory and add `.gitignore` policy
- [x] 4. Configure ruff in `pyproject.toml`

### Phase 1 — Data models (Layer 1) ✅ COMPLETE

- [x] 5. Define all Pydantic v2 models in `code/models.py`
  - `Position`, `IngestWarning`, `SecurityMatch`, `Break`, `RunSummary`, `OutputArtifact`, `ArtifactMetadata` all implemented with correct field/constraint shapes per design.md §4.
  - `Break` schema has nullable `book_quantity`, `book_market_value`, `position_type_book` (Req 4 AC 2).
  - `IngestWarning.type` is a closed `Literal` over the five warning types.
  - _Acceptance:_ Req 1 AC 8; Req 2 AC 1; Req 4 AC 2; Req 5 AC 1, 2; Req 6 AC 1
  - _Files:_ `code/models.py`

### Phase 2 — Ingest_Module (Req 1) ✅ COMPLETE

- [x] 6. Implement `parse_paren_int` helper for paren-negative coercion
- [x] 7. Implement `parse_flexible_date` helper covering non-ISO + year-mismatch warnings
- [x] 8. Implement ticker-dot preservation branch (`normalize_identifier`)
- [x] 9. Implement `ingest_custodian_a` entrypoint
- [x] 10. Implement `ingest_custodian_b` entrypoint
- [x] 11. Verify Ingest_Module against the case-study CSVs (smoke check)

### Phase 3 — Identifier_Resolver (Req 2) ✅ COMPLETE

- [x] 12. Build the security-master index in `code/tools/securities.py`
- [x] 13. Implement exact-ticker resolution branch
- [x] 14. Implement fuzzy-name match with alternatives
- [x] 15. Implement ambiguity detection branch (the Alphabet Inc case)
- [x] 16. Verify Identifier_Resolver on diagnostic queries (smoke check)
  - Note: Berkshire borderline check (design.md §6) should be run — confirm `"Berkshire Hathaway Class A Inc"` resolves to SEC0009 with delta > `ambiguity_epsilon`. If not, the expected break ledger in #[[file:.kiro/steering/product.md]] needs an asterisk.

### Phase 4 — Reconciler (Reqs 1.6, 2.5, 4) ✅ COMPLETE

- [x] 17. Implement cross-custodian join in `code/pipeline/reconcile.py`
  - Add `reconcile(positions_a, positions_b, warnings, as_of) -> list[Break]` per design.md §3 (Reconciler) and §5.C.
  - For each Position, call `IdentifierResolver.resolve(...)`. Partition into `ambiguous` (security_id is None) and `resolved` grouped by `security_id`.
  - Compute a stable `break_id = sha256(f"{as_of}|{security_id_or_query}|{custodian}")[:12]` per design.md §5.C step 5.
  - _Acceptance:_ Req 7 AC 2
  - _Files:_ `code/pipeline/reconcile.py`

- [x] 18. Implement break-type classification
  - Per design.md §5.C step 4, emit one Break per `(security_id, group)` that disagrees:
    - One-sided ⇒ `break_type="missing_at_custodian"`, absent side's `custodian_quantity=None` (Req 3 AC 5).
    - Both present, position_type differs ⇒ `position_type_mismatch`. Populate BOTH `position_type_*` AND `quantity_*`/`quantity_delta` on the same Break (NVDA and BRK.A cases).
    - Both present, quantities differ ⇒ `quantity_mismatch` with `quantity_delta = pa.quantity - pb.quantity` (AAPL, AMZN, MSFT, V).
    - Both present, quantities tie but `abs(value_delta) > 0.01` USD ⇒ `value_mismatch`.
  - _Acceptance:_ Req 4 AC 5; Req 7 AC 2
  - _Files:_ `code/pipeline/reconcile.py`

- [x] 19. Implement `identifier_ambiguous` branch and warning attachment
  - For every position whose `SecurityMatch.security_id is None`, emit a Break with `break_type="identifier_ambiguous"`, `security_id=None`, `raw_source_row` from the Position, and a `fuzzy_match_below_threshold` IngestWarning attached (Req 1 AC 5; Req 2 AC 5).
  - _Acceptance:_ Req 1 AC 5; Req 2 AC 3, 5; Req 7 AC 2
  - _Files:_ `code/pipeline/reconcile.py`

- [x] 20. Attach ingest_warnings to each Break and null the book_* fields
  - For each emitted Break, set `book_quantity = book_market_value = position_type_book = None` (Req 4 AC 3).
  - Attach every IngestWarning from the participating source row(s) to `Break.ingest_warnings` (Req 1 AC 6).
  - _Acceptance:_ Req 1 AC 6; Req 4 AC 2, 3, 5
  - _Files:_ `code/pipeline/reconcile.py`

- [x] 21. Verify Reconciler on the case-study inputs
  - Smoke run: ingest both CSVs, call `reconcile(...)`, print `Counter(b.break_type for b in breaks)`.
  - Expected ledger (from #[[file:.kiro/steering/product.md]] "Expected break ledger"): **2 `position_type_mismatch`** (NVDA, BRK.A), **4 `quantity_mismatch`** (AAPL, AMZN, MSFT, V), **7 `missing_at_custodian`** (META, TSLA, JPM, GOOGL B-side, BAC, SHOP, MA), **1 `identifier_ambiguous`** ("Alphabet Inc"). **Total: 14 breaks.**
  - If BRK.A surfaces as `identifier_ambiguous` instead, that is the borderline epsilon case from task 16 — document the result.
  - _Acceptance:_ Req 4 AC 5
  - _Files:_ (no new files; verification only)

### Phase 5 — OutputArtifact envelope (Req 5) ✅ COMPLETE

- [x] 22. Implement input-file SHA-256 helper
  - Add `compute_input_hashes(paths: dict[str, Path]) -> dict[str, str]` to `code/pipeline/reconcile.py`.
  - Use `hashlib.file_digest(f, "sha256")` (Python 3.11+ streaming); fall back to `hashlib.sha256(f.read()).hexdigest()` if needed (Req 5 AC 5).
  - _Acceptance:_ Req 5 AC 2, 5; Req 7 AC 2
  - _Files:_ `code/pipeline/reconcile.py`
  - _Estimated LOC:_ ~5

- [x] 23. Implement git short-SHA helper with `"uncommitted"` fallback
  - Add `read_git_short_sha() -> str` that runs `git rev-parse --short HEAD` via `subprocess.run(check=False)`.
  - On non-zero return, missing git binary, or any `OSError`, return the literal string `"uncommitted"` — do NOT raise (Req 5 AC 3).
  - _Acceptance:_ Req 5 AC 2, 3; Req 7 AC 2
  - _Files:_ `code/pipeline/reconcile.py`
  - _Estimated LOC:_ ~6

- [x] 24. Implement `write_envelope` helper
  - Add `write_envelope(path: Path, records: list[BaseModel], metadata: ArtifactMetadata) -> None` that constructs `OutputArtifact(metadata=metadata, data=[r.model_dump(mode="json") for r in records])` and writes it as JSON via `json.dump(..., indent=2, default=str)`.
  - Build `ArtifactMetadata` once at the top of the run with `ruleset_version="0.1.0"`, `code_commit=read_git_short_sha()`, `input_file_sha256s=compute_input_hashes(...)`, `as_of_date`, and `generated_at=datetime.now(timezone.utc)` per design.md §5.D.
  - _Acceptance:_ Req 5 AC 1, 2; Req 7 AC 2
  - _Files:_ `code/pipeline/reconcile.py`
  - _Estimated LOC:_ ~9

- [x] 25. Wire the envelope into `out/raw_breaks.json` and `out/data_quality.json`
  - In the Reconciler entry path, call `write_envelope` for `out/raw_breaks.json` (Break records) and `out/data_quality.json` (IngestWarning records grouped by `source_file` then `source_row_index`).
  - Both files share the same `ArtifactMetadata` instance (single hash + commit per run).
  - _Acceptance:_ Req 1 AC 7; Req 5 AC 4
  - _Files:_ `code/pipeline/reconcile.py`
  - _Estimated LOC:_ ~5

- [x] 26. Verify envelope correctness on a smoke run
  - Run the Reconciler end-to-end and inspect `out/raw_breaks.json`. Confirm top level is `{"metadata": {...}, "data": [...]}` and `metadata` contains all five fields from Req 5 AC 2.
  - Confirm `code_commit` is either a 7-char hex SHA or the literal `"uncommitted"`.
  - _Acceptance:_ Req 5 AC 1, 2, 3
  - _Files:_ (no new files; verification only)

### Phase 6 — Run_Summary + Layer-1 entrypoint (Req 4 AC 1, 4) ✅ COMPLETE

- [x] 27. Implement Layer-1 `print_run_summary` in `code/pipeline/reconcile.py`
  - First line printed MUST be: *"No book of record was supplied; reconciling custodian_a vs custodian_b only."* (Req 4 AC 1)
  - Subsequent lines: `Reconciliation pair: custodian_a vs custodian_b` (Req 4 AC 4), break counts by `break_type` (Counter-style), `runtime_seconds=...` via `time.monotonic()`.
  - _Acceptance:_ Req 4 AC 1, 4; Req 6 AC 2; Req 7 AC 2, 3
  - _Files:_ `code/pipeline/reconcile.py`
  - _Estimated LOC:_ ~4

- [x] 28. Wire the `python -m code.pipeline.reconcile` entrypoint
  - Add an `if __name__ == "__main__":` block that resolves the three input CSVs from `GS-Technical-Case-Study/`, calls Ingest → Reconciler → write_envelope → print_run_summary in order, with `as_of_date=date(2026, 1, 2)`.
  - This entrypoint MUST NOT import from `code.agent` or any Strands SDK module (Req 7 AC 2).
  - _Acceptance:_ Req 4 AC 5; Req 6 AC 2; Req 7 AC 2, 3
  - _Files:_ `code/pipeline/reconcile.py`

- [x] 29. Verify the Layer-1 entrypoint runs end-to-end
  - Run `uv run python -m code.pipeline.reconcile` from the project root.
  - Confirm: stdout opens with the no-book-of-record line; `out/raw_breaks.json` and `out/data_quality.json` both exist and are valid JSON with the metadata envelope; run terminates with a non-zero `runtime_seconds` and no traceback.
  - This is the first reviewable Layer-1 artifact set — a reasonable stopping point for a 2-hour budget.
  - _Acceptance:_ Req 4 AC 1, 4, 5; Req 5 AC 4; Req 6 AC 2
  - _Files:_ (no new files; verification only)

### Phase 7 — Tests (Req 3) ✅ COMPLETE

- [x] 30. Write `test_paren_quantity_in_custodian_b_is_parsed_as_short_position`
  - Fixture: `code/tests/fixtures/custodian_b_paren.csv` — one row with `(5000)` quantity and `(market_value_usd)`.
  - Asserts: `position.quantity == -5000`, `position.position_type == "SHORT"`, exactly one `IngestWarning` with `type == "paren_negative_coerced"`.
  - _Acceptance:_ Req 3 AC 1
  - _Files:_ `code/tests/test_ingest_paren.py`, `code/tests/fixtures/custodian_b_paren.csv`

- [x] 31. Write `test_alphabet_inc_is_ambiguous_between_googl_and_goog_class_shares`
  - Use the full `securities_reference.csv` and an in-memory `"Alphabet Inc"` query.
  - Asserts: `match.security_id is None`; `{a["security_id"] for a in match.alternatives} == {"SEC0004", "SEC0005"}`; Reconciler emits exactly one Break with `break_type == "identifier_ambiguous"`.
  - _Acceptance:_ Req 2 AC 4, 5; Req 3 AC 2
  - _Files:_ `code/tests/test_resolver_ambiguity.py`

- [x] 32. Write `test_brka_dot_in_ticker_is_preserved_through_normalization`
  - Fixture: `code/tests/fixtures/custodian_a_brka.csv` — one row with `BRK.A`.
  - Asserts: ingested Position's `raw_query == "BRK.A"` (period intact); `IdentifierResolver.resolve("BRK.A", "ticker").security_id == "SEC0009"`.
  - _Acceptance:_ Req 2 AC 6; Req 3 AC 3
  - _Files:_ `code/tests/test_ticker_dot.py`, `code/tests/fixtures/custodian_a_brka.csv`

- [x] 33. Write `test_2025_date_in_2026_eod_file_emits_year_mismatch_warning`
  - Fixture: `code/tests/fixtures/custodian_b_year.csv` — one row dated `2025-01-02`. Run ingest with `configured_year=2026`.
  - Asserts: exactly one `IngestWarning` with `type == "year_mismatch"`; Position's `as_of_date.year == 2025` (source-row truth preserved).
  - _Acceptance:_ Req 1 AC 3; Req 3 AC 4
  - _Files:_ `code/tests/test_year_mismatch.py`, `code/tests/fixtures/custodian_b_year.csv`

- [x] 34. Write `test_position_only_in_one_custodian_is_classified_as_one_sided`
  - Fixture: `code/tests/fixtures/custodian_a_tsla_only.csv` plus an empty Custodian B list.
  - Asserts: exactly one Break with `security_id == "SEC0008"`, `break_type == "missing_at_custodian"`, `custodian == "custodian_b"`, `custodian_quantity is None` for the absent custodian, and the present custodian's quantity populated.
  - _Acceptance:_ Req 3 AC 5
  - _Files:_ `code/tests/test_one_sided.py`, `code/tests/fixtures/custodian_a_tsla_only.csv`

- [x] 35. Write the suite-level `OutputArtifact` envelope invariant test
  - Add `test_every_json_artifact_has_metadata_envelope` in `code/tests/test_envelope_invariant.py`.
  - Runs the Layer-1 pipeline against the case-study CSVs into a `tmp_path` `out/`, then for every `*.json` file written, asserts the top level is `{"metadata": {...}, "data": [...]}` and all five `metadata` fields from Req 5 AC 2 are present and non-empty.
  - _Acceptance:_ Req 3 AC 6; Req 5 AC 1, 2, 4
  - _Files:_ `code/tests/test_envelope_invariant.py`

### Phase 8 — Layer 2 stub: Agent_Runtime + Cost_Reporter (Req 6) ✅ COMPLETE

- [x] 36. (Optional — time-permitting) Build the Strands Agent skeleton in `code/agent.py`
  - Search `strands-agents` MCP per #[[file:.kiro/steering/strands-agents-patterns.md]] before importing — confirm the SDK package name and `BedrockModel` import path.
  - Construct `BedrockModel(model_id=..., region_name="us-west-2")` per design.md §3 Agent_Runtime, then build an `Agent` with the `@tool` set listed in #[[file:.kiro/steering/tech.md]] Agent Tools.
  - Tool implementations may be mocked from in-line dicts. The `lookup_security` `@tool` SHALL be a thin wrapper around the Layer-1 `IdentifierResolver` from `code/tools/securities.py`.
  - _Acceptance:_ Req 7 AC 3, 4
  - _Files:_ `code/agent.py`, `code/tools/trades.py`, `code/tools/corporate_actions.py`, `code/tools/classification.py`

- [x] 37. (Optional — time-permitting) Implement TokenUsage capture and missing-usage warning
  - Iterate the agent over the raw break set per design.md §5.E. For each turn, accumulate `input_tokens`/`output_tokens` or increment `missing_turns` (Req 6 AC 4).
  - Write `out/resolved_breaks.json` and `out/escalations.json` through the same `write_envelope` helper from Phase 5 (Req 5 AC 4).
  - _Acceptance:_ Req 5 AC 4; Req 6 AC 4; Req 7 AC 3
  - _Files:_ `code/agent.py`

- [x] 38. (Optional — time-permitting) Implement Cost_Reporter in `code/run.py`
  - Define `INPUT_RATE = 3.00` and `OUTPUT_RATE = 15.00` constants (USD per million tokens) with an inline comment citing the Bedrock pricing source — confirm via `awslabs.aws-pricing-mcp-server` per #[[file:.kiro/steering/mcp-tools-usage.md]] (Req 6 AC 3).
  - Compute `estimated_cost_usd = (tokens_input / 1_000_000) * INPUT_RATE + (tokens_output / 1_000_000) * OUTPUT_RATE`.
  - Measure `runtime_seconds` via `time.monotonic()` from entry to summary print (Req 6 AC 5).
  - Print the full `RunSummary` per Req 6 AC 1; if `missing_turns > 0`, print a `missing_token_usage warning: N turn(s) had no usage metadata; their cost is treated as $0.00` line first.
  - _Acceptance:_ Req 6 AC 1, 3, 4, 5; Req 7 AC 3
  - _Files:_ `code/run.py`

- [x] 39. (Optional — time-permitting) Wire `python -m code.run` with graceful Layer-1 degradation
  - The Layer-2 entrypoint MUST first run the Layer-1 path (Ingest → Reconciler → Layer-1 artifacts) so reviewers without Bedrock access still get `out/raw_breaks.json` + `out/data_quality.json`.
  - Then attempt the agent path. If Bedrock initialization fails (missing creds, no model access), catch the SDK's auth/transport exception, print the Layer-1-only `RunSummary` per Req 6 AC 2, and exit zero.
  - The no-book-of-record line from Phase 6 task 27 MUST print in both modes (Req 4 AC 1).
  - _Acceptance:_ Req 4 AC 1; Req 6 AC 2; Req 7 AC 3
  - _Files:_ `code/run.py`

- [x] 40. (Optional — time-permitting) Verify the Layer-2 path and its degradation
  - With Bedrock creds: run `uv run python -m code.run` and confirm `out/resolved_breaks.json` exists with the metadata envelope; the `RunSummary` prints all seven fields from Req 6 AC 1.
  - Without Bedrock creds: temporarily unset `AWS_PROFILE` / `AWS_ACCESS_KEY_ID` and re-run; confirm the run exits zero with the trimmed Layer-1-only summary.
  - _Acceptance:_ Req 6 AC 1, 2; Req 7 AC 3
  - _Files:_ (no new files; verification only)

### Phase 9 — Verify and ship

- [x] 41. Run lint and format checks
  - `uv run ruff check .` and `uv run ruff format .`; both commands exit zero.
  - _Acceptance:_ Req 7 AC 4
  - _Files:_ (touch-up only)

- [x] 42. Run the full test suite
  - `uv run pytest -v`; all five named tests from Phase 7 plus the suite-level envelope invariant pass.
  - _Acceptance:_ Req 3 AC 6
  - _Files:_ (no new files)

- [x] 43. Run the Layer-1 smoke
  - `uv run python -m code.pipeline.reconcile`; confirm no-book line, both JSON artifacts, runtime reported.
  - _Acceptance:_ Req 4 AC 1, 4; Req 5 AC 4; Req 6 AC 2
  - _Files:_ (no new files)

- [x] 44. (Optional — if Phase 8 done and Bedrock creds present) Run the full demo smoke
  - `uv run python -m code.run`; confirm `out/resolved_breaks.json` and the full cost summary line.
  - _Acceptance:_ Req 6 AC 1, 2
  - _Files:_ (no new files)

- [x] 45. Author `README.md` from the 12-section structure in #[[file:.kiro/steering/product.md]]
  - The brief weights "thought leadership" above shippable code, so `README.md` is the primary deliverable. Lift the structure from product.md "What the README Must Cover" verbatim.
  - Cite the actual run output: paste the no-book-of-record line, the 14-break ledger from #[[file:.kiro/steering/product.md]] "Expected break ledger", and a representative sample Break record (e.g. NVDA's `position_type_mismatch` showing both direction flip and quantity delta on the same record per design.md §5.C step 4).
  - Use `out/data_quality.json` to back the "Specific Observations" section with real warning counts.
  - Include the "Pivots and Rejected Alternatives" list from #[[file:.kiro/steering/tech.md]] verbatim.
  - Embed the architecture Mermaid block from design.md §2 directly in the README so it renders on GitHub; reference the static export from task 46.
  - _Acceptance:_ Brief deliverable §1 (README structure); product.md "What the README Must Cover" items 1–12
  - _Files:_ `GS-Technical-Case-Study/README.md`

- [x] 46. Export the architecture diagram to `img/architecture.png`
  - Take the Mermaid `flowchart` block from design.md §2 (Architecture) and render it as a static PNG or SVG. Two acceptable workflows:
    - VS Code "Markdown Preview Mermaid" extension → right-click → Export, OR
    - Mermaid CLI: `npx -y @mermaid-js/mermaid-cli -i architecture.mmd -o img/architecture.png`
  - The image must show the Layer 1 / Layer 2 firewall (solid edges = deterministic, dotted edges = agent path).
  - Optional: also export the sequence diagram from design.md §10 to `img/sequence.png`.
  - _Acceptance:_ Brief deliverable §3 (img/ architecture diagram)
  - _Files:_ `GS-Technical-Case-Study/img/architecture.png`

## Notes

**Budget-tight tasks.** Phase 5 (tasks 22–25) is the largest single contributor to the Req 7 AC 1 80-LOC delta — design.md §12 estimates ~25 LOC for the envelope helpers, leaving ~2 LOC of headroom. If the cap is hit, the recovery path is to inline `compute_input_hashes` and `read_git_short_sha` into a single `write_envelope` body (saves ~5 LOC at a small readability cost) per design.md §12.

**Risky tasks.**
- Task 15 (ambiguity_epsilon branch) MUST NOT introduce any tiebreaker. The diagnostic `"Alphabet Inc"` case is the test that catches a regression here.
- Task 23 (git short-SHA helper) MUST swallow every error mode (missing git, non-zero exit, `OSError`) and return `"uncommitted"`. A raise here breaks Req 5 AC 3 and the suite-level envelope invariant test (task 35).
- Task 18 (break-type classification) MUST populate both `position_type_*` AND `quantity_*`/`quantity_delta` on the same Break for `position_type_mismatch` cases (NVDA, BRK.A) — the quantity delta must not be lost.
- Task 39 (Layer-2 graceful degradation) requires distinguishing Bedrock auth/transport failures from real bugs. Catch narrowly — wrap only the agent construction and run, not the Layer-1 path.

**Deferrals (out of scope per the spec).**
- Real eval harness, mock-vs-real tool flags — explicitly deferred per `requirements.md` Introduction.
- Production / Scale Stack items (DynamoDB, AgentCore deployment, Step Functions, Guardrails, Slack integration) — writeup-only per #[[file:.kiro/steering/tech.md]] Production / Scale Stack table.
- `out/report.csv` envelope format — design.md §13 leaves this CSV without a metadata block.

**Phase 8 model id and pricing.** Confirm the exact Bedrock Sonnet model id and the `$3 / $15` per-million-token rates at implementation time via `awslabs.aws-pricing-mcp-server` and `awslabs.aws-documentation-mcp-server` per #[[file:.kiro/steering/mcp-tools-usage.md]]. The constants in task 38 are placeholders inherited from design.md §6.
