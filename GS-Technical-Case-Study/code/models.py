"""Pydantic v2 data models for the recon-demo pipeline.

All models are frozen (immutable) to prevent accidental mutation during
the deterministic Layer 1 pipeline. The Break schema is the canonical
artifact of sub-problem 1; all other models feed into it.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    """Normalized position record emitted by Ingest_Module.

    The `raw_query` field carries the original ticker or description for
    later resolution; security_id is intentionally absent at this stage
    because resolution happens in the Reconciler.
    """

    model_config = ConfigDict(frozen=True)

    custodian: Literal["custodian_a", "custodian_b"]
    raw_query: str = Field(min_length=1)  # ticker or description
    quantity: int  # signed; short = negative
    market_value: float  # signed USD
    position_type: Literal["LONG", "SHORT"]
    as_of_date: date
    source_row_index: int = Field(ge=0)
    raw_source_row: dict[str, str]


class IngestWarning(BaseModel):
    """Structured warning emitted for any coercion or quality concern.

    `type` is a closed set so downstream code (and tests) can assert on it.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[
        "paren_negative_coerced",
        "non_iso_date_coerced",
        "year_mismatch",
        "ticker_dot_preserved",
        "fuzzy_match_below_threshold",
    ]
    severity: Literal["info", "warning", "error"] = "warning"
    source_file: str
    source_row_index: int = Field(ge=0)
    message: str
    detail: dict[str, str | int | float | None] = Field(default_factory=dict)


class SecurityMatch(BaseModel):
    """Identifier_Resolver return value.

    `security_id` is None when no candidate clears `fuzzy_threshold` or when
    the top-two candidates are within `ambiguity_epsilon` of each other.
    """

    security_id: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    alternatives: list[dict[str, float | str]] = Field(default_factory=list)
    reason: Literal[
        "exact_ticker",
        "fuzzy_name",
        "ambiguous_top_two",
        "below_threshold",
    ]


class Break(BaseModel):
    """Canonical break record per the product spec.

    book_* fields are nullable so the schema accommodates a future book of
    record without modification (Requirement 4 AC 2).
    """

    break_id: str = Field(min_length=8)
    as_of_date: date
    security_id: str | None  # null on identifier_ambiguous
    custodian: Literal["custodian_a", "custodian_b", "both"]
    break_type: Literal[
        "missing_in_book",
        "missing_at_custodian",
        "quantity_mismatch",
        "value_mismatch",
        "position_type_mismatch",
        "identifier_unresolved",
        "identifier_ambiguous",
    ]
    book_quantity: int | None = None
    custodian_quantity: int | None = None
    quantity_delta: int | None = None
    book_market_value: float | None = None
    custodian_market_value: float | None = None
    value_delta: float | None = None
    position_type_book: Literal["LONG", "SHORT"] | None = None
    position_type_custodian: Literal["LONG", "SHORT"] | None = None
    raw_source_row: dict[str, str]
    ingest_warnings: list[IngestWarning] = Field(default_factory=list)


class RunSummary(BaseModel):
    """Stdout-formatted run summary; full mode includes cost fields."""

    total_breaks: int = Field(ge=0)
    breaks_by_type: dict[str, int] = Field(default_factory=dict)
    auto_cleared_count: int | None = None  # None in Layer-1-only
    escalated_count: int | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    estimated_cost_usd: float | None = None
    runtime_seconds: float = Field(ge=0.0)
    mode: Literal["full", "layer1_only"]


class ArtifactMetadata(BaseModel):
    """Metadata block written at the top of every JSON artifact in out/."""

    ruleset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    code_commit: str  # git short SHA or "uncommitted"
    input_file_sha256s: dict[str, str]  # filename -> hex digest
    as_of_date: date
    generated_at: datetime


class OutputArtifact(BaseModel):
    """Top-level envelope written to every JSON artifact in out/."""

    metadata: ArtifactMetadata
    data: list[dict]
