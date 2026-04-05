"""Define provider-neutral translation request and backend contracts."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class TranslationRequest:
    """Describe one text translation request tied to a transcript revision."""

    utterance_id: str
    revision: int
    text: str
    target_language: str
    source_language: str | None = None


@dataclass(slots=True, frozen=True)
class TranslationResult:
    """Store one translated transcript revision for chatbox rendering."""

    utterance_id: str
    revision: int
    source_text: str
    translated_text: str


class TranslationBackend(Protocol):
    """Describe one configured translation backend ready to serve requests."""

    name: str

    def describe(self) -> str:
        """Return a CLI-friendly description of the configured backend."""
        ...

    def validate_environment(self) -> None:
        """Raise when the backend cannot run in the current environment."""
        ...

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        """Translate one transcript revision request."""
        ...


__all__ = [
    "TranslationBackend",
    "TranslationRequest",
    "TranslationResult",
]
