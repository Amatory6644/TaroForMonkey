from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Typed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(Typed):
    text: str
    evidence_ids: list[str]


class Visual(Typed):
    prompt: str
    required_references: list[str]
    exclusions: list[str]


class Draft(Typed):
    text: str
    visual: Visual
    claims: list[Claim]
    rationale: str


class Finding(Typed):
    category: Literal["factual", "brand", "editorial"]
    message: str
    fix: str


class Review(Typed):
    findings: list[Finding]
    summary: str


class Concept(Typed):
    title: str
    hook: str
    goal: str
    format: Literal["TEXT_POST", "IMAGE_POST"]
    rationale: str


class Concepts(Typed):
    ideas: list[Concept]


class Direction(Typed):
    ranked_indexes: list[int]
    rationale: str


class Strategy(Typed):
    audience_hypotheses: list[str]
    positioning_hypotheses: list[str]
    recommendations: list[str]
    evidence_gaps: list[str]


class Intent(Typed):
    intent: Literal["ideas", "plan", "produce", "status"]
    count: int
    brief: str
    target_date: str
    missing_fields: list[str]


class Scene(Typed):
    image_asset_id: str
    duration: float = Field(ge=1, le=30)
    narration: str
    caption: str = Field(max_length=180)


class Storyboard(Typed):
    title: str
    scenes: list[Scene] = Field(min_length=1, max_length=12)
