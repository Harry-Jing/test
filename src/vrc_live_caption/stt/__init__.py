"""Expose STT backends, runner helpers, and shared normalized event types."""

from ..errors import SttSessionError
from .factory import create_stt_backend, describe_stt_backend, validate_stt_secrets
from .iflytek_rtasr import (
    FatalIflytekServerError,
    IflytekRtasrBackend,
    RetriableIflytekServerError,
)
from .openai_realtime import FatalRealtimeServerError, OpenAIRealtimeBackend
from .runner import AsyncSttSessionRunner
from .types import (
    AttemptContext,
    ConnectionAttempt,
    SttBackend,
    SttEvent,
    SttStatus,
    SttStatusEvent,
    TranscriptRevisionEvent,
)

__all__ = [
    "AsyncSttSessionRunner",
    "AttemptContext",
    "ConnectionAttempt",
    "create_stt_backend",
    "describe_stt_backend",
    "FatalIflytekServerError",
    "FatalRealtimeServerError",
    "IflytekRtasrBackend",
    "OpenAIRealtimeBackend",
    "RetriableIflytekServerError",
    "SttBackend",
    "SttEvent",
    "SttSessionError",
    "SttStatus",
    "SttStatusEvent",
    "TranscriptRevisionEvent",
    "validate_stt_secrets",
]
