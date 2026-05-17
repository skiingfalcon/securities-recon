"""Layer 2 @tool factory: disposition recommendation (judgement layer for the agent).

This is the only "tool" that does meaningful work beyond a dict lookup:
it lets the agent record a disposition recommendation + confidence + rationale
on a break, which the run loop then writes into ``out/agent_recommendations.json``
or ``out/human_review_queue.json`` depending on the disposition.

We expose a *factory* rather than a module-level @tool because the agent
runner needs to capture each recommendation back out of the tool call. Strands'
``@tool`` plumbing returns a value to the model but the runner has no
clean post-hoc way to inspect what was returned across turns. The
factory pattern (mirroring ``build_lookup_security_tool`` in agent.py)
binds the tool to a caller-owned ``captured`` list, so the runner reads
the disposition directly after each per-break agent invocation.
"""

from collections.abc import Callable
from typing import Literal

from strands import tool

Disposition = Literal["recommend_clear", "recommend_investigate", "require_human"]


def build_recommend_disposition_tool(captured: list[dict]) -> Callable[..., dict]:
    """Construct a recommend_disposition @tool bound to a captured-dispositions list.

    Args:
        captured: A caller-owned list. Each successful tool call appends
            one recommendation dict to it. The runner reads ``captured[-1]``
            after the agent call to extract the agent's final disposition.

    Returns:
        A Strands @tool-decorated function that records its arguments
        into ``captured`` and returns them.
    """

    @tool
    def recommend_disposition(
        break_id: str,
        disposition: Disposition,
        confidence: float,
        rationale: str,
    ) -> dict:
        """Record the agent's disposition recommendation on a break.

        The runner inspects this dict to decide which artifact the break
        lands in. ``recommend_clear`` and ``recommend_investigate`` go to
        ``agent_recommendations.json``; ``require_human`` goes to
        ``human_review_queue.json`` for ops review.

        Args:
            break_id: The 12-char break_id from the raw_breaks artifact.
            disposition: One of "recommend_clear", "recommend_investigate",
                "require_human".
            confidence: Agent's self-reported confidence in [0.0, 1.0].
            rationale: One- or two-sentence explanation citing the tools
                consulted (e.g. "Recent BUY ticket of 50k shares on T+1
                at custodian_a explains the quantity delta of +50k vs
                custodian_b").

        Returns:
            dict echoing the inputs plus an ``accepted`` flag.
        """
        record = {
            "break_id": break_id,
            "disposition": disposition,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "rationale": rationale,
            "accepted": True,
        }
        captured.append(record)
        return record

    return recommend_disposition
