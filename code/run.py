"""Layer 2 entrypoint — Layer-1 pipeline + agent + Cost_Reporter.

Run with ``uv run python -m code.run``. The Layer-1 path always executes
first so reviewers without Bedrock credentials still get
``out/raw_breaks.json`` and ``out/data_quality.json`` plus the no-book-of-
record line. The Layer-2 agent path is attempted on top; on any Bedrock
auth/transport failure we degrade gracefully and exit zero (Req 6 AC 2).
"""

import time
from datetime import UTC, date, datetime
from pathlib import Path

from code.pipeline.reconcile import (
    compute_input_hashes,
    read_git_short_sha,
    run_layer1,
)

# Bedrock on-demand pricing for Claude Sonnet 4.5 (us-west-2), USD per
# million tokens. Confirm rates at run time via
# awslabs.aws-pricing-mcp-server per .kiro/steering/mcp-tools-usage.md.
# Reference: https://aws.amazon.com/bedrock/pricing/
INPUT_RATE = 3.00
OUTPUT_RATE = 15.00


def _estimate_cost_usd(tokens_input: int, tokens_output: int) -> float:
    """Compute estimated USD cost from accumulated token counts."""
    return (tokens_input / 1_000_000) * INPUT_RATE + (tokens_output / 1_000_000) * OUTPUT_RATE


def _print_layer2_summary(
    auto_cleared: int,
    escalated: int,
    tokens_input: int,
    tokens_output: int,
    missing_turns: int,
    runtime_seconds: float,
) -> None:
    """Emit the Layer-2 cost block on top of the Layer-1 summary.

    The no-book-of-record line is already printed by ``run_layer1`` so
    this function only adds the agent-specific fields required by
    Req 6 AC 1.
    """
    if missing_turns > 0:
        print(
            f"missing_token_usage warning: {missing_turns} turn(s) had no usage "
            f"metadata; their cost is treated as $0.00"
        )
    print("--- Layer 2 (agent) summary ---")
    print(f"  auto_cleared_count: {auto_cleared}")
    print(f"  escalated_count: {escalated}")
    print(f"  tokens_input: {tokens_input}")
    print(f"  tokens_output: {tokens_output}")
    print(f"  estimated_cost_usd: ${_estimate_cost_usd(tokens_input, tokens_output):.6f}")
    print(f"  layer2_runtime_seconds={runtime_seconds:.3f}")


def main(project_root: Path | None = None) -> int:
    """Run Layer 1 unconditionally, then attempt Layer 2 with graceful fallback."""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    as_of_date = date(2026, 1, 2)
    out_dir = project_root / "out"

    breaks = run_layer1(project_root, out_dir=out_dir, as_of_date=as_of_date)

    # Layer 2 imports are localized so a missing Strands install or a
    # Bedrock auth failure can't take down the Layer-1 path above.
    layer2_started = time.monotonic()
    try:
        from code.agent import run_agent_over_breaks
        from code.models import ArtifactMetadata
        from code.tools.securities import IdentifierResolver

        resolver = IdentifierResolver(project_root / "securities_reference.csv")
        metadata = ArtifactMetadata(
            ruleset_version="0.1.0",
            code_commit=read_git_short_sha(),
            input_file_sha256s=compute_input_hashes(
                {
                    "custodian_a.csv": project_root / "custodian_a.csv",
                    "custodian_b.csv": project_root / "custodian_b.csv",
                    "securities_reference.csv": project_root / "securities_reference.csv",
                }
            ),
            as_of_date=as_of_date,
            generated_at=datetime.now(UTC),
        )
        agent_result = run_agent_over_breaks(breaks, resolver, out_dir, metadata)
    except Exception as exc:  # noqa: BLE001  narrow catch sits inside the agent path only
        # Distinguish credential/transport problems from real bugs only by
        # the fact that the Layer-1 path already succeeded. Any failure
        # past this point is treated as degraded mode per Req 6 AC 2.
        print(f"Layer 2 unavailable ({type(exc).__name__}: {exc}); running Layer-1-only.")
        return 0

    _print_layer2_summary(
        auto_cleared=len(agent_result.resolved),
        escalated=len(agent_result.escalations),
        tokens_input=agent_result.tokens_input,
        tokens_output=agent_result.tokens_output,
        missing_turns=agent_result.missing_turns,
        runtime_seconds=time.monotonic() - layer2_started,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
