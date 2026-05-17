"""Layer 2 — Agent_Runtime (Strands + Bedrock Claude Sonnet 4.5).

This module is the *only* place where Strands SDK code lives. The Layer-1
firewall in design.md §3 forbids ``code/pipeline/`` and ``code/models.py``
from importing anything in here, so a missing AWS credential never breaks
the deterministic reconciliation.

Per design.md §5.E, the agent is invoked once per raw break. We accumulate
token usage across all turns; turns that return no usage metadata are
counted as ``missing_turns`` (Req 6 AC 4) and surfaced in the run summary.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from strands import Agent, tool
from strands.models.bedrock import BedrockModel

from code.models import ArtifactMetadata, Break, BreakRecommendation
from code.pipeline.reconcile import write_envelope
from code.tools.corporate_actions import lookup_corporate_actions
from code.tools.recommendation import build_recommend_disposition_tool
from code.tools.securities import IdentifierResolver
from code.tools.trades import get_recent_trades

# Per design.md §3 Agent_Runtime: us-west-2 is the only region where the
# project is approved for Bedrock model access.
BEDROCK_REGION = "us-west-2"

# Claude Sonnet 4.5 US cross-region inference profile (Bedrock).
# https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# System prompt scopes the agent to break-investigation only. The agent
# may call lookup_security, get_recent_trades, lookup_corporate_actions,
# and MUST call recommend_disposition exactly once with its final disposition.
SYSTEM_PROMPT = """You are a securities reconciliation analyst.
For each break you receive, recommend whether ops can clear it, should
investigate it once, or must have a human review it. Use the available
tools to gather evidence (recent trades, corporate actions, security
master lookups). End every turn by calling recommend_disposition() exactly
once with your disposition (recommend_clear, recommend_investigate, or
require_human), a confidence in [0,1], and a one-sentence rationale that
cites the specific tool outputs you relied on."""


def build_lookup_security_tool(resolver: IdentifierResolver):
    """Construct the lookup_security @tool bound to a shared resolver.

    Per the design, this tool MUST be a thin wrapper around the Layer-1
    IdentifierResolver — duplicating the matching logic in two places
    would let Layer 1 and Layer 2 drift apart and silently disagree
    about the master.
    """

    @tool
    def lookup_security(query: str, query_kind: str = "description") -> dict:
        """Resolve a free-text description or ticker to a security_id.

        Args:
            query: The raw identifier (ticker symbol or description).
            query_kind: Either "ticker" or "description" (default).

        Returns:
            dict with security_id, confidence, alternatives, reason.
        """
        kind = "ticker" if query_kind == "ticker" else "description"
        match = resolver.resolve(query, query_kind=kind)  # type: ignore[arg-type]
        return {
            "security_id": match.security_id,
            "confidence": match.confidence,
            "alternatives": match.alternatives,
            "reason": match.reason,
        }

    return lookup_security


@dataclass
class AgentRunResult:
    """Aggregated output of iterating the agent over a break set."""

    agent_recommendations: list[dict]
    human_review: list[dict]
    tokens_input: int
    tokens_output: int
    missing_turns: int


def _default_require_human(break_id: str, final_text: str) -> dict:
    """Fallback disposition when the agent failed to call recommend_disposition.

    The primary disposition source is the captured-list populated by
    ``build_recommend_disposition_tool``; this function only fires when that
    list is empty after the agent call (model went off-script, tool
    invocation failed, etc.). Defaulting to ``require_human`` keeps the
    safety invariant: any uncategorised break lands in front of a human.
    """
    rationale = (
        final_text.strip()
        or "Agent produced no disposition recommendation; defaulting to require_human."
    )
    return {
        "break_id": break_id,
        "disposition": "require_human",
        "confidence": 0.0,
        "rationale": rationale,
        "auto_extracted": True,
    }


def _accumulate_usage(result: Any, tokens_input: int, tokens_output: int) -> tuple[int, int, bool]:
    """Pull input/output token counts off an AgentResult.metrics.

    Returns the updated totals plus a boolean indicating whether usage
    was actually present on this turn (False => caller increments
    ``missing_turns`` per Req 6 AC 4).
    """
    metrics = getattr(result, "metrics", None)
    usage = getattr(metrics, "accumulated_usage", None) if metrics is not None else None
    if not usage:
        return tokens_input, tokens_output, False
    # ``accumulated_usage`` is a TypedDict-like object: keys may be
    # camelCase ("inputTokens") in some SDK versions and snake_case in
    # others. Cover both.
    in_t = usage.get("inputTokens") if hasattr(usage, "get") else None
    out_t = usage.get("outputTokens") if hasattr(usage, "get") else None
    if in_t is None and out_t is None:
        in_t = getattr(usage, "inputTokens", None) or getattr(usage, "input_tokens", None)
        out_t = getattr(usage, "outputTokens", None) or getattr(usage, "output_tokens", None)
    if in_t is None and out_t is None:
        return tokens_input, tokens_output, False
    return tokens_input + (in_t or 0), tokens_output + (out_t or 0), True


def _to_break_recommendation(record: dict) -> BreakRecommendation:
    return BreakRecommendation(
        break_id=record["break_id"],
        disposition=record["disposition"],
        confidence=record["confidence"],
        rationale=record["rationale"],
        auto_extracted=record.get("auto_extracted", False),
    )


def run_agent_over_breaks(
    breaks: list[Break],
    resolver: IdentifierResolver,
    out_dir: Path,
    metadata: ArtifactMetadata,
) -> AgentRunResult:
    """Iterate the agent over every break and write the L2 artifacts.

    Constructs the Bedrock-backed Agent once, then calls it per-break so
    each break gets its own conversation. ``recommend_clear`` and
    ``recommend_investigate`` dispositions go to ``out/agent_recommendations.json``;
    ``require_human`` goes to ``out/human_review_queue.json``. Both files use
    the same ``write_envelope`` helper from Phase 5 so the metadata block
    matches the Layer-1 artifacts (Req 5 AC 4).
    """
    # The BedrockModel and the lookup_security tool are stateless across
    # breaks and can be built once. recommend_disposition is *per-break*
    # because it closes over a fresh ``captured`` list each iteration so the
    # runner can read the agent's disposition back out cleanly.
    model = BedrockModel(model_id=BEDROCK_MODEL_ID, region_name=BEDROCK_REGION)
    lookup_security = build_lookup_security_tool(resolver)

    agent_recommendations: list[dict] = []
    human_review: list[dict] = []
    tokens_input = 0
    tokens_output = 0
    missing_turns = 0

    for b in breaks:
        captured: list[dict] = []
        recommend_disposition = build_recommend_disposition_tool(captured)
        agent = Agent(
            model=model,
            tools=[
                lookup_security,
                get_recent_trades,
                lookup_corporate_actions,
                recommend_disposition,
            ],
            system_prompt=SYSTEM_PROMPT,
        )

        prompt = (
            f"Investigate break_id={b.break_id} for security_id={b.security_id!r} "
            f"on {b.as_of_date.isoformat()}. break_type={b.break_type}, "
            f"custodian={b.custodian}, raw_source_row={b.raw_source_row}. "
            f"Recommend recommend_clear, recommend_investigate, or require_human "
            f"and call recommend_disposition()."
        )
        result = agent(prompt)
        tokens_input, tokens_output, had_usage = _accumulate_usage(
            result, tokens_input, tokens_output
        )
        if not had_usage:
            missing_turns += 1

        # Primary disposition source: whatever the agent passed to recommend_disposition.
        # Fallback: synthesize require_human so the break still lands somewhere safe
        # if the agent went off-script.
        record = captured[-1] if captured else _default_require_human(b.break_id, str(result))
        if record["disposition"] == "require_human":
            human_review.append(record)
        else:
            agent_recommendations.append(record)

    write_envelope(
        out_dir / "agent_recommendations.json",
        [_to_break_recommendation(r) for r in agent_recommendations],
        metadata,
    )
    write_envelope(
        out_dir / "human_review_queue.json",
        [_to_break_recommendation(r) for r in human_review],
        metadata,
    )

    return AgentRunResult(
        agent_recommendations=agent_recommendations,
        human_review=human_review,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        missing_turns=missing_turns,
    )
