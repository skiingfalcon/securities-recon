"""Identifier_Resolver: fuzzy-match security descriptions and tickers to the master.

Layer 1 — deterministic, no LLM. Builds a normalized index over
securities_reference.csv once per run and uses rapidfuzz for description
queries. Preserves periods in tickers (BRK.A is matched as brk.a, not brka).

This module is also the Layer 2 @tool surface: lookup_security wraps the
same resolve() logic so the agent calls the same code path.
"""

import csv
from pathlib import Path
from typing import Literal

from rapidfuzz import fuzz
from rapidfuzz import process as fuzz_process

from code.models import SecurityMatch

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# Minimum confidence for a fuzzy match to be accepted as a clean resolution.
# Empirically, WRatio on the case-study description set scores clean matches
# at ~90+ and noise at <80. 0.85 admits clean matches and rejects the tail.
FUZZY_THRESHOLD: float = 0.85

# If the top-two candidates are within this epsilon of each other, the match
# is considered ambiguous and security_id is set to None. The canonical case
# is "Alphabet Inc" → GOOGL vs GOOG (both score ~90, delta ~1-2 points).
AMBIGUITY_EPSILON: float = 0.05


class IdentifierResolver:
    """Resolves security identifiers against the canonical security master.

    Builds two indexes on construction:
    - by_ticker: lowercase ticker → security_id (dot-preserving)
    - by_name_choices: list of (security_id, lowercase_name) for rapidfuzz

    Args:
        master_path: Path to securities_reference.csv.
        fuzzy_threshold: Minimum confidence for a clean fuzzy match.
        ambiguity_epsilon: Max delta between top-two candidates before
            the match is flagged as ambiguous.
    """

    def __init__(
        self,
        master_path: Path,
        fuzzy_threshold: float = FUZZY_THRESHOLD,
        ambiguity_epsilon: float = AMBIGUITY_EPSILON,
    ) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        self.ambiguity_epsilon = ambiguity_epsilon

        # Build indexes from the security master CSV.
        # We read the CSV directly (not pandas) to keep Layer 1 lightweight
        # and avoid a pandas dependency in the resolver's hot path.
        self._by_ticker: dict[str, str] = {}  # lowercase ticker → security_id
        self._by_name_choices: list[tuple[str, str]] = []  # (security_id, lowercase_name)

        with master_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                security_id = row["security_id"].strip()
                ticker = row["ticker"].strip()
                name = row["name"].strip()

                # Preserve periods in tickers: "BRK.A" → key "brk.a"
                # Many systems strip the dot and produce "brka", which would
                # fail to match. We keep the dot so the exact-ticker branch
                # works correctly for dotted tickers.
                self._by_ticker[ticker.lower()] = security_id

                # Name index uses lowercase for case-insensitive fuzzy matching.
                self._by_name_choices.append((security_id, name.lower()))

    def resolve(
        self,
        query: str,
        query_kind: Literal["ticker", "description"],
    ) -> SecurityMatch:
        """Resolve a security identifier against the master.

        Implements Algorithm B from the design spec:
        1. Ticker queries: exact lookup in the lowercase ticker index.
           BRK.A → brk.a → SEC0009 (dot preserved throughout).
        2. Description queries: rapidfuzz WRatio extraction with ambiguity
           detection (implemented in tasks 14 and 15).

        Args:
            query: The raw identifier string (ticker or description).
            query_kind: "ticker" for Custodian A symbols; "description" for
                Custodian B free-text names.

        Returns:
            A SecurityMatch with security_id, confidence, alternatives, and
            reason. security_id is None when the match is ambiguous or below
            the fuzzy threshold.
        """
        if query_kind == "ticker":
            # Exact ticker lookup — case-insensitive, dot-preserving.
            # We lowercase the query to match the index keys (e.g. "BRK.A" → "brk.a").
            # The dot is preserved in both the query and the index key, so
            # "BRK.A" correctly resolves to SEC0009 and "BRKA" would miss.
            hit = self._by_ticker.get(query.lower())
            if hit is not None:
                return SecurityMatch(
                    security_id=hit,
                    confidence=1.0,
                    alternatives=[],
                    reason="exact_ticker",
                )
            # Ticker not found in the index — fall through to fuzzy path.
            # This handles the edge case where a Custodian A row has a ticker
            # that doesn't exist in the master (e.g. a delisted security).
            # The fuzzy path will attempt a name match and may return below_threshold.

        # ---------------------------------------------------------------------------
        # Fuzzy description path — Algorithm B steps 2.2–2.6 (design spec §Key Algorithms B)
        # ---------------------------------------------------------------------------

        # Step 2.2: Run rapidfuzz WRatio extraction against the lowercase name index.
        # WRatio is a weighted combination of several ratio algorithms; it handles
        # partial matches, token reordering, and case differences well — important
        # because custodian descriptions like "Apple Inc Common Stock" need to match
        # "APPLE INC COMMON STOCK" in the master.
        raw_results = fuzz_process.extract(
            query.lower(),
            [n for _, n in self._by_name_choices],
            scorer=fuzz.WRatio,
            limit=5,
        )

        # Step 2.2 (cont): Map each result back to its security_id and normalize
        # the score from [0, 100] to [0, 1] so confidence is always in [0, 1].
        # raw_results is a list of (matched_string, score, index) tuples.
        candidates: list[dict[str, str | float]] = []
        for matched_name, score, idx in raw_results:
            security_id, _ = self._by_name_choices[idx]
            candidates.append({"security_id": security_id, "confidence": score / 100.0})

        # Step 2.3: Sort by confidence descending (rapidfuzz already returns sorted,
        # but we sort explicitly to guarantee the invariant regardless of library version).
        candidates.sort(key=lambda c: c["confidence"], reverse=True)

        # Step 2.4: Below-threshold branch — no candidates or top confidence too low.
        # We still surface any candidates that clear the half-threshold bar as
        # alternatives so the caller can decide whether to escalate or discard.
        top_confidence = candidates[0]["confidence"] if candidates else 0.0

        if not candidates or top_confidence < self.fuzzy_threshold:
            alternatives = [c for c in candidates if c["confidence"] >= self.fuzzy_threshold / 2]
            return SecurityMatch(
                security_id=None,
                confidence=top_confidence,
                alternatives=alternatives,
                reason="below_threshold",
            )

        # Step 2.5: Ambiguity branch — top-two candidates within ambiguity_epsilon.
        # This is the "Alphabet Inc" case: GOOGL and GOOG both score ~90 because
        # the only distinguishing token is "CLASS A" vs "CLASS C". We refuse to
        # pick a winner silently; instead we return both as alternatives so the
        # Reconciler can emit an identifier_ambiguous break.
        if len(candidates) >= 2:
            top = candidates[0]
            second = candidates[1]
            delta = float(top["confidence"]) - float(second["confidence"])
            if delta < self.ambiguity_epsilon:
                return SecurityMatch(
                    security_id=None,
                    confidence=float(top["confidence"]),
                    alternatives=[top, second],
                    reason="ambiguous_top_two",
                )

        # Step 2.6: Clean match — top candidate clears the threshold and is
        # unambiguously ahead of the second. Return the winner plus any runner-up
        # candidates that clear the half-threshold bar as informational alternatives.
        top = candidates[0]
        alternatives = [c for c in candidates[1:] if c["confidence"] >= self.fuzzy_threshold / 2]
        return SecurityMatch(
            security_id=str(top["security_id"]),
            confidence=float(top["confidence"]),
            alternatives=alternatives,
            reason="fuzzy_name",
        )
