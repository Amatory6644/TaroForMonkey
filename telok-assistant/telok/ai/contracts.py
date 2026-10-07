from dataclasses import dataclass, field
from typing import Protocol

from telok.domain import DomainError


class ProviderError(DomainError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class AIResult:
    text: str
    provider: str
    model: str
    usage: dict = field(default_factory=dict)
    response_id: str = ""
    sources: list = field(default_factory=list)


class ReasoningProvider(Protocol):
    def generate(self, instructions: str, content: list, **kwargs) -> AIResult: ...
    def models(self) -> list[dict]: ...
