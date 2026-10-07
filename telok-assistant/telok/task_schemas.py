from typing import Literal

from pydantic import Field

from telok.schemas import Typed


class TaskBrief(Typed):
    kind: Literal[
        "ASK", "RESEARCH", "ANALYZE_DOCUMENT", "WRITE", "CONTENT_CAMPAIGN", "GENERATE_MEDIA", "EDIT_ARTIFACT"
    ]
    goal: str
    audience: str
    platform: str
    format: str
    assumptions: list[str]
    questions: list[str] = Field(max_length=3)
    needs_search: bool


class CreativeIdea(Typed):
    title: str
    hook: str
    message: str
    cta: str
    visual: str
    difficulty: str


class ConceptSet(Typed):
    ideas: list[CreativeIdea] = Field(min_length=3, max_length=3)
    recommended_index: int = Field(ge=0, le=2)
    rationale: str


class ProductionScene(Typed):
    scene_id: str
    duration_seconds: float = Field(ge=1, le=30)
    purpose: str
    subject: str
    action: str
    camera: str
    light: str
    narration: str
    screen_text: str
    sound: str
    reference_asset_ids: list[str]
    prompt: str


class ProductionPack(Typed):
    title: str
    concept: str
    shared_visual_rules: list[str]
    verified_facts: list[str]
    assumptions: list[str]
    scenes: list[ProductionScene] = Field(min_length=1, max_length=12)
    post_text: str
    cta: str
    missing_inputs: list[str]


class PackReview(Typed):
    passed: bool
    findings: list[str]
    missing_checks: list[str]
    summary: str


class SceneEditSet(Typed):
    scenes: list[ProductionScene] = Field(min_length=1, max_length=12)
