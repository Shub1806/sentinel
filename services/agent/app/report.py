"""The incident report the model writes, checked and cleaned up."""

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

ACTION_KINDS = ("restart_deployment", "scale_deployment", "rollout_undo", "none", "manual")


class SuggestedAction(BaseModel):
    kind: str = "manual"
    target: str = ""
    detail: str = ""

    @field_validator("kind", mode="before")
    @classmethod
    def known_kind(cls, value):
        value = str(value or "").strip().lower()
        return value if value in ACTION_KINDS else "manual"

    @field_validator("target", "detail", mode="before")
    @classmethod
    def text(cls, value):
        return "" if value is None else str(value)


class Report(BaseModel):
    summary: str
    likely_cause: str = ""
    evidence: list[str] = Field(default_factory=list)
    suggested_action: SuggestedAction = Field(default_factory=SuggestedAction)
    confidence: Literal["low", "medium", "high"] = "low"

    @field_validator("confidence", mode="before")
    @classmethod
    def known_confidence(cls, value):
        value = str(value or "").strip().lower()
        return value if value in ("low", "medium", "high") else "low"

    @field_validator("evidence", mode="before")
    @classmethod
    def evidence_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_report(text: str) -> Report | None:
    """Finds the JSON report in the model's answer (also inside ```json fences)."""
    if not text:
        return None
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return Report.model_validate(json.loads(candidate[start : end + 1]))
    except json.JSONDecodeError, ValidationError, TypeError:
        return None
