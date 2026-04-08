"""Define shared STT backend contracts and normalized event types."""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol, TypeAlias

from ..errors import SttSessionError
from ..runtime import AudioChunk, DropOldestAsyncQueue


class SttStatus(str, Enum):
    """Enumerate lifecycle and transport states emitted by STT backends."""

    CONNECTING = "connecting"
    READY = "ready"
    RETRYING = "retrying"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


@dataclass(slots=True, frozen=True)
class TranscriptRevisionEvent:
    """Represent one normalized transcript revision for an utterance."""

    utterance_id: str
    revision: int
    text: str
    is_final: bool


@dataclass(slots=True, frozen=True)
class SttStatusEvent:
    """Represent one normalized STT status or transport update."""

    status: SttStatus
    message: str | None = None
    attempt: int | None = None


SttEvent: TypeAlias = TranscriptRevisionEvent | SttStatusEvent


@dataclass(slots=True)
class AttemptContext:
    """Describe the resources and callbacks shared with one connection attempt."""

    audio_queue: DropOldestAsyncQueue[AudioChunk]
    publish_event: Callable[[SttEvent], None]
    mark_ready: Callable[[str], None]
    stop_requested: asyncio.Event
    connect_timeout_seconds: float
    logger: logging.Logger


class ConnectionAttempt(Protocol):
    """Define one provider-specific connection attempt."""

    async def run(self) -> None:
        """Execute one end-to-end attempt until stop or failure."""
        ...


class SttBackend(Protocol):
    """Describe one configured STT backend ready to create attempts."""

    name: str

    @property
    def logger(self) -> logging.Logger:
        """Return the backend logger used for diagnostics."""
        ...

    def describe(self) -> str:
        """Return a CLI-friendly backend description."""
        ...

    def connecting_message(self) -> str:
        """Return the first-attempt status message emitted before connect."""
        ...

    def closing_message(self) -> str:
        """Return the status message emitted when shutdown begins."""
        ...

    def closed_message(self) -> str:
        """Return the status message emitted after the runner exits."""
        ...

    def stop_timeout_message(self) -> str:
        """Return the error raised when shutdown exceeds the timeout."""
        ...

    def create_attempt(self, *, context: AttemptContext) -> ConnectionAttempt:
        """Create a fresh provider-specific connection attempt."""
        ...

    def is_retriable_error(self, exc: BaseException) -> bool:
        """Return whether a failure should trigger reconnect logic."""
        ...

    def retrying_message(
        self, exc: BaseException, attempt: int, backoff_seconds: float
    ) -> str:
        """Return the retry status message for a retriable failure."""
        ...

    def exhausted_error(self, exc: BaseException) -> BaseException:
        """Return the terminal error raised after the retry budget is exhausted."""
        ...


__all__ = [
    "AttemptContext",
    "ConnectionAttempt",
    "SttBackend",
    "SttEvent",
    "SttSessionError",
    "SttStatus",
    "SttStatusEvent",
    "TranscriptRevisionEvent",
]
