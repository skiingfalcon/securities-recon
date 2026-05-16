"""Layer 2 @tool factory: break classifier (judgement layer for the agent).

This is the only "tool" that does meaningful work beyond a dict lookup:
it lets the agent record a verdict + confidence + rationale on a break,
which the run loop then writes into ``out/resolved_breaks.json`` or
``out/escalations.json`` depending on the verdict.

We expose a *factory* rather than a module-level @tool because the agent
runner needs to capture each verdict back out of the tool call. Strands'
``@tool`` plumbing returns a value to the model but the runner has no
clean post-hoc way to inspect what was returned across turns. The
factory pattern (mirroring ``build_lookup_security_tool`` in agent.py)
binds the tool to a caller-owned ``captured`` list, so the runner reads
the verdict directly after each per-break agent invocation.
"""

from collections.abc import Callable
from typing import Literal

from strands import tool


def build_classify_break_tool(captured: list[dict]) -> Callable[..., dict]:
    """Construct a classify_break @tool bound to a captured-verdicts list.

    Args:
        captured: A caller-owned list. Each successful tool call appends
            one verdict dict to it. The runner reads ``captured[-1]``
            after the agent call to extract the agent's final verdict.

    Returns:
        A Strands @tool-decorated function that records its arguments
        into ``captured`` and returns them.
    """

    @tool
    def classify_break(
        break_id: str,
        verdict: Literal["auto_clear", "investigate", "escalate"],
        confidence: float,
        rationale: str,
    ) -> dict:
        """Record the agent's verdict on a break.

        The runner inspects this dict to decide which artifact the break
        lands in. ``auto_clear`` and high-confidence ``investigate``
        verdicts go to ``resolved_breaks.json``; ``escalate`` and
        low-confidence verdicts go to ``escalations.json`` for human
        review.

        Args:
            break_id: The 12-char break_id from the raw_breaks artifact.
            verdict: One of "auto_clear", "investigate", "escalate".
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
            "verdict": verdict,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "rationale": rationale,
            "accepted": True,
        }
        captured.append(record)
        return record

    return classify_break
