"""Versioned SSE events and answer blocks for Ask Cryo."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

EVENT_VERSION = 1


class AssistantEvent(BaseModel):
    v: int = EVENT_VERSION
    message_id: str
    seq: int
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AnswerBlock(BaseModel):
    type: Literal["answer"] = "answer"
    text: str
    as_of: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class RankedTableBlock(BaseModel):
    type: Literal["ranked_table"] = "ranked_table"
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    total: int
    truncated: bool = False
    cursor: str | None = None
    open_url: str | None = None
    csv_url: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ComparisonBlock(BaseModel):
    type: Literal["comparison"] = "comparison"
    left: dict[str, Any]
    right: dict[str, Any]
    unit: str | None = None
    comparable: bool
    note: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ChartBlock(BaseModel):
    type: Literal["chart"] = "chart"
    kind: Literal[
        "ranked_bars",
        "time_series",
        "before_after",
        "distribution",
        "stacked_share",
        "run_lanes",
    ]
    title: str
    question: str | None = None
    answer_line: str | None = None
    unit: str | None = None
    window: str | None = None
    timezone: str | None = None
    as_of: str | None = None
    coverage: dict[str, Any] = Field(default_factory=dict)
    provenance: str = "measured"
    series: list[dict[str, Any]] = Field(default_factory=list)
    markers: list[dict[str, Any]] | None = None
    target: dict[str, Any] | None = None
    table: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class InvestigationBlock(BaseModel):
    type: Literal["investigation"] = "investigation"
    symptom: str
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    next_step: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ActionReviewBlock(BaseModel):
    type: Literal["action_review"] = "action_review"
    proposal_id: str
    evidence_ids: list[str] = Field(default_factory=list)


class ExecutionBlock(BaseModel):
    type: Literal["execution"] = "execution"
    proposal_id: str
    evidence_ids: list[str] = Field(default_factory=list)


class OutcomeBlock(BaseModel):
    type: Literal["outcome"] = "outcome"
    proposal_id: str
    requested: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class GapBlock(BaseModel):
    type: Literal["gap"] = "gap"
    missing_input: str
    setup_url: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class VerifiedBlock(BaseModel):
    type: Literal["verified"] = "verified"
    handler: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


def format_sse(event: AssistantEvent) -> str:
    body = event.model_dump(mode="json")
    return f"id: {event.seq}\nevent: {event.type}\ndata: {json.dumps(body)}\n\n"
