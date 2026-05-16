# Requirements Document

## Introduction

This feature refines the demo code plan for the Grayscale Technical Case Study with **thought-leadership hooks** that signal subject-matter expertise to reviewers, beyond the baseline two-layer pipeline already described in #[[file:.kiro/steering/tech.md]]. The brief in `GS-Technical-Case-Study/Instructions.md` weights "thought leadership" and "how you think" above shippable code, and explicitly rewards specific data observations, open questions, and considered/rejected alternatives.

The canonical Break schema, the specific data observations that drive the test names, and the open-questions list are defined in #[[file:.kiro/steering/product.md]] and are not duplicated here. This document references those steering files as the source of truth so the requirements stay in sync as the product and tech docs evolve.

The hooks are scoped into two priority bands:

- **Priority 1** (Requirements 1 through 4) — must-include hooks scoped to Layer 1 (the deterministic pipeline). These make data-quality thinking, identifier ambiguity, domain-spec test naming, and the "no book of record" framing visible in the artifacts a reviewer actually opens.
- **Priority 2** (Requirements 5 and 6) — include if scope allows. Requirement 5 (output metadata block) belongs to Layer 1. Requirement 6 (cost and token summary) belongs to the Layer 2 agent path and SHALL degrade gracefully when only Layer 1 is run.

The Layer 1 / Layer 2 boundary in #[[file:.kiro/steering/tech.md]] is binding: Layer 1 SHALL NOT invoke any foundation-model client. Hooks 1 through 5 belong to Layer 1; Hook 6 belongs to Layer 2.

A real eval harness, mock-vs-real tool surface markers, and a Mermaid architecture diagram are out of scope for this spec and deferred to a follow-up if Layer 2 / agent code is built.

## Glossary

- **Recon_Pipeline**: The end-to-end reconciliation workflow consisting of Layer 1 (deterministic) and Layer 2 (agentic) as defined in #[[file:.kiro/steering/tech.md]].
- **Ingest_Module**: The Layer 1 component responsible for parsing custodian CSV files and emitting normalized Position records.
- **Identifier_Resolver**: The Layer 1 component (and corresponding `@tool` wrapper) that maps a custodian-supplied ticker or free-text description to a `security_id` from the security master.
- **Reconciler**: The Layer 1 component that joins normalized positions across custodians and emits Break records.
- **Agent_Runtime**: The Layer 2 Strands agent that consumes the Break set, calls `@tool` functions, and produces resolved breaks.
- **Run_Summary**: The stdout output produced at the end of a Recon_Pipeline run.
- **Cost_Reporter**: The Run_Summary subsystem that aggregates and prints token usage, cost, and runtime metrics.
- **SecurityMatch**: A Pydantic v2 record returned by Identifier_Resolver with fields `security_id`, `confidence`, `alternatives`, and `reason`.
- **Break**: The canonical break record schema defined in #[[file:.kiro/steering/product.md]] under "Canonical Break Record".
- **ingest_warnings**: A list field on each Break record holding warnings emitted during ingestion of the source row.
- **data_quality.json**: The Layer 1 output artifact at `out/data_quality.json` listing all ingest warnings produced during a run.
- **baseline pipeline**: The two-layer architecture (Layer 1 deterministic plus Layer 2 agentic) described in #[[file:.kiro/steering/tech.md]] without the thought-leadership hooks specified in this document.
- **fuzzy_threshold**: The minimum `rapidfuzz` similarity score (0.0 to 1.0) at which Identifier_Resolver accepts a candidate as a confident match.
- **ambiguity_epsilon**: The maximum allowed difference between the top-two candidate confidence values before the result is treated as ambiguous.

## Requirements

### Requirement 1: Structured ingest warnings as first-class output

**User Story:** As a reviewer evaluating the case study, I want every coercion the ingest layer performs to be captured as a typed, structured warning attached to its source row, so that I can see at a glance that the author treats data-quality observations as audit artifacts rather than silent transforms.

#### Acceptance Criteria

1. WHEN the Ingest_Module encounters a quantity or market-value field whose value is wrapped in parentheses, THE Ingest_Module SHALL coerce the value to a signed negative number and emit an ingest warning of type `paren_negative_coerced` recording the source file, row index, raw value, and coerced value.
2. WHEN the Ingest_Module encounters a date value whose format is not ISO 8601 (`YYYY-MM-DD`), THE Ingest_Module SHALL parse the value to an ISO 8601 date using `dateutil.parser` and emit an ingest warning of type `non_iso_date_coerced` recording the source file, row index, raw value, parsed value, and detected format token.
3. WHEN the year of a parsed as-of date does not equal the year of the run's configured as-of date, THE Ingest_Module SHALL emit an ingest warning of type `year_mismatch` recording the source file, row index, parsed year, and configured year.
4. WHEN the Ingest_Module encounters a ticker symbol containing a period character, THE Ingest_Module SHALL preserve the period in the normalized ticker and emit an ingest warning of type `ticker_dot_preserved` recording the source file, row index, and the preserved ticker value.
5. WHEN the Identifier_Resolver returns a match whose confidence is less than or equal to the configured `fuzzy_threshold`, THE Ingest_Module SHALL emit an ingest warning of type `fuzzy_match_below_threshold` recording the source file, row index, query string, top candidate `security_id`, confidence score, and the alternatives list.
6. THE Ingest_Module SHALL attach every warning produced for a given source row to the corresponding Break record's `ingest_warnings` field as defined in the Break schema in #[[file:.kiro/steering/product.md]].
7. THE Recon_Pipeline SHALL write a consolidated artifact at `out/data_quality.json` listing every ingest warning produced during the run, grouped by source file and row index.
8. THE Ingest_Module SHALL emit each ingest warning as a structured object with at minimum the fields `type` (string), `severity` (one of `info`, `warning`, `error`), `source_file` (string), `source_row_index` (integer, zero-based), and `message` (string).

### Requirement 2: Identifier resolution with confidence, alternatives, and a dedicated `identifier_ambiguous` break type

**User Story:** As a reviewer evaluating the case study, I want the security-resolution layer to return calibrated confidence and runner-up candidates rather than forcing a single match, so that I can see that the author understands why description-based matching is fragile in finance and how to surface that fragility in code instead of hiding it.

#### Acceptance Criteria

1. THE Identifier_Resolver SHALL return a `SecurityMatch` record containing the fields `security_id` (string or null), `confidence` (float in the closed interval `[0.0, 1.0]`), `alternatives` (list of objects with `security_id` and `confidence`), and `reason` (string explaining the match decision).
2. WHEN no candidate scores at or above the configured `fuzzy_threshold`, THE Identifier_Resolver SHALL set `SecurityMatch.security_id` to `null` and SHALL include in `alternatives` every candidate whose confidence is at least one half of `fuzzy_threshold`.
3. IF the difference between the top-two candidates' confidence values is less than the configured `ambiguity_epsilon`, THEN THE Identifier_Resolver SHALL set `SecurityMatch.security_id` to `null` and include both candidates in `alternatives`, and THE Reconciler SHALL emit a Break record of type `identifier_ambiguous` for that source row.
4. WHEN the Identifier_Resolver receives the input description `"Alphabet Inc"` (case-insensitive), THE Identifier_Resolver SHALL return a `SecurityMatch` whose `alternatives` list contains both `SEC0004` (ticker `GOOGL`) and `SEC0005` (ticker `GOOG`) with their respective confidence scores.
5. THE Reconciler SHALL emit a Break record of type `identifier_ambiguous` for any source row whose `SecurityMatch.security_id` is `null`, with `raw_source_row` set to the original custodian record and `ingest_warnings` containing the `fuzzy_match_below_threshold` warning from Requirement 1 acceptance criterion 5.
6. WHERE a ticker symbol contains one or more period characters (such as `BRK.A`), THE Identifier_Resolver SHALL preserve every period in that ticker without removing, replacing, or altering the period when matching against the security master defined in #[[file:.kiro/steering/product.md]]; ticker symbols that do not contain a period SHALL be matched without modification by this rule.

### Requirement 3: Test names that read as the domain spec

**User Story:** As a reviewer evaluating the case study, I want the pytest test names to read as a domain specification of the data quirks the author noticed, so that the test list itself communicates subject-matter expertise without requiring me to read implementation code.

#### Acceptance Criteria

1. THE pytest test suite SHALL include a test named `test_paren_quantity_in_custodian_b_is_parsed_as_short_position` that asserts the Ingest_Module parses a parenthesized quantity in `custodian_b.csv` (for example `(5000)`) into a signed negative integer and labels the resulting Position as `SHORT`.
2. THE pytest test suite SHALL include a test named `test_alphabet_inc_is_ambiguous_between_googl_and_goog_class_shares` that asserts the Identifier_Resolver returns both `GOOGL` and `GOOG` as alternatives for the input `"Alphabet Inc"` and that the Reconciler emits a Break of type `identifier_ambiguous` for the corresponding row.
3. THE pytest test suite SHALL include a test named `test_brka_dot_in_ticker_is_preserved_through_normalization` that asserts the Ingest_Module and Identifier_Resolver preserve the period in `BRK.A` end-to-end and resolve it to `SEC0009`.
4. THE pytest test suite SHALL include a test named `test_2025_date_in_2026_eod_file_emits_year_mismatch_warning` that asserts a row whose parsed as-of year is `2025` in a run configured for as-of date `2026-01-02` produces an ingest warning of type `year_mismatch`.
5. THE pytest test suite SHALL include a test named `test_position_only_in_one_custodian_is_classified_as_one_sided` that, by way of executable assertions, verifies that a security present at exactly one custodian (for example `TSLA` only at `custodian_a` or `BAC` only at `custodian_b`) produces a Break whose type indicates a one-sided position per the canonical Break schema in #[[file:.kiro/steering/product.md]] and whose quantity field for the absent custodian is `null`.
6. THE pytest test suite SHALL pass when invoked via `uv run pytest -v` from the project root as defined in #[[file:.kiro/steering/tech.md]].

### Requirement 4: Honest "no book of record" framing in code output

**User Story:** As a reviewer evaluating the case study, I want the run output to explicitly state that the case-study inputs do not include a book of record and to label the reconciliation pair accordingly, so that I can see the author distinguishes between book-versus-custodian recon (the production case) and custodian-versus-custodian recon (this case study) without conflating them.

#### Acceptance Criteria

1. WHEN the Recon_Pipeline finishes a run, THE Run_Summary SHALL print to stdout a statement that the book of record is absent from the case-study inputs and that the reconciliation is performed between `custodian_a` and `custodian_b`.
2. THE Pydantic v2 Break schema in `code/models.py` SHALL define `book_quantity` and `book_market_value` as nullable fields, consistent with the canonical schema in #[[file:.kiro/steering/product.md]], so that the same schema accommodates a future book-of-record source without modification.
3. WHILE no book-of-record source is configured for the run, THE Reconciler SHALL set `book_quantity` and `book_market_value` to `null` on every emitted Break record.
4. THE Run_Summary SHALL label the reconciliation pair in its output as `custodian_a vs custodian_b` rather than `book vs custodian`.
5. THE Recon_Pipeline (Layer 1) SHALL operate end-to-end without invoking any foundation-model client or any function in the Agent_Runtime module, consistent with #[[file:.kiro/steering/tech.md]].

### Requirement 5: Output metadata block on every JSON artifact

**User Story:** As a reviewer evaluating the case study, I want every JSON artifact in `out/` to begin with a metadata block describing how it was generated, so that I can see audit-trail thinking made tangible: ruleset version, code commit, input file hashes, as-of date, and generation timestamp.

#### Acceptance Criteria

1. THE Recon_Pipeline SHALL produce JSON output artifacts whose top-level structure is the object `{"metadata": {...}, "data": [...]}` where `data` holds the artifact's payload (for example, the list of Break records).
2. THE `metadata` object SHALL contain the fields `ruleset_version` (semantic version string), `code_commit` (git short SHA or the literal string `"uncommitted"`), `input_file_sha256s` (object mapping each input filename to its hex-encoded SHA-256 digest), `as_of_date` (ISO 8601 date string), and `generated_at` (ISO 8601 timestamp with timezone offset).
3. WHERE the working directory is not a git repository or the git short SHA cannot be determined, THE Recon_Pipeline SHALL set `code_commit` to the literal string `"uncommitted"` and continue without error.
4. THE Recon_Pipeline SHALL apply the metadata-block format to every JSON artifact it writes to `out/`, including `out/raw_breaks.json`, `out/resolved_breaks.json`, `out/data_quality.json`, and `out/escalations.json`.
5. THE Recon_Pipeline SHALL compute each entry of `input_file_sha256s` over the byte content of the corresponding input file as it was read for the run.

### Requirement 6: Run-end cost and token summary

**User Story:** As a reviewer evaluating the case study, I want the Layer 2 agent run to print a per-run summary of breaks detected, breaks auto-cleared, breaks escalated, tokens consumed, estimated USD cost, and runtime, so that the author's stated cost-ceiling concern from #[[file:.kiro/steering/tech.md]] is demonstrated in code rather than only described in prose.

#### Acceptance Criteria

1. WHEN the Agent_Runtime (Layer 2) completes a run, THE Cost_Reporter SHALL print to stdout a summary containing the fields `total_breaks` (integer), `auto_cleared_count` (integer), `escalated_count` (integer), `tokens_input` (integer), `tokens_output` (integer), `estimated_cost_usd` (float), and `runtime_seconds` (float).
2. WHEN the Recon_Pipeline runs Layer 1 only (Agent_Runtime is not invoked), THE Cost_Reporter SHALL print to stdout a summary containing `total_breaks` and `runtime_seconds`, omit the token and cost fields, and complete without raising an error.
3. THE Cost_Reporter SHALL compute `estimated_cost_usd` as `(tokens_input / 1_000_000) * input_rate_usd_per_million + (tokens_output / 1_000_000) * output_rate_usd_per_million`, with both rate constants defined in source code and accompanied by an inline comment citing the Bedrock pricing source for the configured Claude Sonnet model.
4. IF the Bedrock client response contains no token usage metadata for one or more turns, THEN THE Cost_Reporter SHALL set `tokens_input` and `tokens_output` for those turns to `0`, set the corresponding `estimated_cost_usd` contribution to `0.0`, and print a `missing_token_usage` warning line to stdout naming the affected turn count.
5. THE Cost_Reporter SHALL measure `runtime_seconds` as the elapsed wall-clock time from the start of the Recon_Pipeline entrypoint to the moment the summary is printed, using a monotonic clock.

### Requirement 7: Implementation budget and layer-boundary constraints

**User Story:** As the author of the case study, I want the thought-leadership hooks to honor the documented two-hour budget and the Layer 1 / Layer 2 boundary, so that adding these signals does not crowd out the rest of the deliverable or violate the determinism contract for Layer 1.

#### Acceptance Criteria

1. THE combined implementation of Requirements 1 through 5 SHALL add no more than 80 lines of executable Python code beyond the baseline pipeline as described in #[[file:.kiro/steering/tech.md]], excluding blank lines, comments, docstrings, and test code.
2. THE implementation of Requirements 1, 2, 3, 4, and 5 SHALL reside entirely within Layer 1 modules (`code/pipeline/`, `code/models.py`, `code/tools/securities.py`, `code/tests/`) and SHALL NOT import from `code/agent.py` or any Strands Agents SDK module.
3. THE implementation of Requirement 6 SHALL reside in the Layer 2 entrypoint path (`code/run.py` or `code/agent.py`) and SHALL degrade to the Layer-1-only behavior specified in Requirement 6 acceptance criterion 2 when invoked through `uv run python -m code.pipeline.reconcile`.
4. THE implementation of Requirements 1 through 6 SHALL preserve the project layout, dependencies, and Python version pinned in #[[file:.kiro/steering/tech.md]] (Python 3.13+, uv, `pyproject.toml`, `pandas`, `rapidfuzz`, `dateutil.parser`, `pydantic` v2, `pytest`, `ruff`, Strands Agents SDK with `BedrockModel`).
