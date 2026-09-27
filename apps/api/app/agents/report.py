"""Part 28 — standardized agent output schema.

Every agent returns this exact structure — malformed or missing
fields are rejected (agents cannot substitute prose for the risk
assessment). `recommendation` vocabulary is fixed per layer.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "agent-report/v1.0"

RECOMMENDATIONS = {"strong_buy", "buy", "hold", "reduce", "sell",
                   "watch", "wait", "pass", "block", "no_trade",
                   "insufficient_data"}
LAYERS = {"research", "control", "capital", "executive", "execution"}


class EvidenceItem(BaseModel):
    claim: str
    ref_table: str | None = None   # fundamental_observations|ohlcv_bars|…
    ref_id: str | None = None
    note: str | None = None


class AgentReport(BaseModel):
    # identity / provenance
    schema_version: str = SCHEMA_VERSION
    execution_id: str
    agent_key: str
    layer: str
    ticker: str
    model_id: str              # 'deterministic/…' or LLM model id
    prompt_version: str
    engine_refs: list[str] = Field(default_factory=list)  # engine versions used
    produced_at: datetime

    # required content fields
    data_quality: Literal["high", "medium", "low", "insufficient"]
    recommendation: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    key_evidence: list[EvidenceItem]
    pass_criteria: list[str]
    fail_criteria: list[str]
    key_risks: list[str]
    contradictions: list[str]
    assumptions: list[str]
    data_gaps: list[str]
    required_followup: list[str]
    conclusion: str


def validate_report(raw: dict) -> AgentReport:
    """Strict boundary — raises on malformed input."""
    r = AgentReport(**raw)
    if r.layer not in LAYERS:
        raise ValueError(f"unknown layer {r.layer}")
    if r.recommendation not in RECOMMENDATIONS:
        raise ValueError(f"unknown recommendation {r.recommendation}")
    if not r.conclusion.strip():
        raise ValueError("conclusion cannot be empty")
    return r
