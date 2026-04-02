"""Implement the OpenAI Realtime transcription backend and connection attempts."""

import asyncio
import base64
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, InvalidStatus

from ..config import CaptureConfig, OpenAIRealtimeProviderConfig, SttRetryConfig
from ..errors import SttProviderFatalError, SttSessionError
from ..runtime import QueueClosedError
from .resample import LinearPcm16Resampler
from .types import (
    AttemptContext,
    ConnectionAttempt,
    SttBackend,
    SttEvent,
    SttStatus,
    SttStatusEvent,
    TranscriptRevisionEvent,
)

_EVENT_POLL_TIMEOUT_SECONDS = 0.1
_FLUSH_TIMEOUT_SECONDS = 1.0
_OPENAI_REALTIME_PCM_SAMPLE_RATE = 24_000
_OPENAI_REALTIME_TRANSCRIPTION_URL = (
    "wss://api.openai.com/v1/realtime?intent=transcription"
)


class FatalRealtimeServerError(SttProviderFatalError):
    """Raised when a server event indicates a non-retriable auth or config error."""


@dataclass(slots=True)
class OpenAIUtteranceState:
    """Track accumulated text and revision count for one OpenAI utterance."""

    text: str = ""
    revision: int = 0


@dataclass(slots=True)
class OpenAIConnectionState:
    """Store OpenAI attempt-scoped mutable state."""

    utterances: dict[str, OpenAIUtteranceState] = field(default_factory=dict)
    resampler: LinearPcm16Resampler | None = None


def normalize_openai_realtime_event(
    event: Any,
    utterances: dict[str, OpenAIUtteranceState],
) -> list[SttEvent]:
    """Normalize raw OpenAI Realtime events into shared transcript or status events."""
    event_type = _get_value(event, "type")
    if event_type == "conversation.item.input_audio_transcription.delta":
        utterance_id = _get_value(event, "item_id")
        delta = _get_value(event, "delta", "") or ""
        state = utterances.setdefault(utterance_id, OpenAIUtteranceState())
        state.text += delta
        state.revision += 1
        return [
            TranscriptRevisionEvent(
                utterance_id=utterance_id,
                revision=state.revision,
                text=state.text,
                is_final=False,
            )
        ]

    if event_type == "conversation.item.input_audio_transcription.completed":
        utterance_id = _get_value(event, "item_id")
        transcript = _get_value(event, "transcript", "") or ""
        state = utterances.setdefault(utterance_id, OpenAIUtteranceState())
        state.text = transcript
        state.revision += 1
        normalized = TranscriptRevisionEvent(
            utterance_id=utterance_id,
            revision=state.revision,
            text=state.text,
            is_final=True,
        )
        utterances.pop(utterance_id, None)
        return [normalized]

    if event_type == "conversation.item.input_audio_transcription.failed":
        utterance_id = _get_value(event, "item_id", "unknown")
        error = _get_value(event, "error")
        message = (
            _format_openai_error_message(error) or "input audio transcription failed"
        )
        utterances.pop(utterance_id, None)
        return [
            SttStatusEvent(status=SttStatus.ERROR, message=f"{utterance_id}: {message}")
        ]

    if event_type == "error":
        error = _get_value(event, "error")
        message = _format_openai_error_message(error) or "OpenAI realtime error"
        return [SttStatusEvent(status=SttStatus.ERROR, message=message)]

    return []


def is_fatal_openai_realtime_error(event: Any) -> bool:
    """Return whether a server error event indicates a non-retriable failure."""
    error = _get_value(event, "error")
    error_type = (_get_value(error, "type", "") or "").lower()
    error_code = (_get_value(error, "code", "") or "").lower()
    message = (_get_value(error, "message", "") or "").lower()

    if error_type in {
        "authentication_error",
        "invalid_request_error",
        "permission_error",
    }:
        return True
    fatal_markers = (
        "auth",
        "unauthorized",
        "permission",
        "invalid_api_key",
        "invalid_request",
        "unsupported",
        "configuration",
    )
    return any(marker in error_code or marker in message for marker in fatal_markers)


def is_retriable_openai_realtime_error(exc: BaseException) -> bool:
    """Return whether a transport exception should trigger reconnect logic."""
    if isinstance(exc, ConnectionClosed):
        reason = (getattr(exc, "reason", "") or "").lower()
        return not _contains_fatal_error_marker(reason)
    if isinstance(exc, (asyncio.TimeoutError, OSError)):
        return True
    if isinstance(exc, InvalidStatus):
        return False
    return False


class OpenAIRealtimeAttempt(ConnectionAttempt):
    """Run one OpenAI Realtime websocket attempt."""

    def __init__(
        self,
        *,
        state: OpenAIConnectionState,
        context: AttemptContext,
        provider_config: OpenAIRealtimeProviderConfig,
        capture_config: CaptureConfig,
        api_key: str,
        logger: logging.Logger,
    ) -> None:
        self._state = state
        self._context = context
        self._provider_config = provider_config
        self._capture_config = capture_config
        self._api_key = api_key
        self._logger = logger
        self._state.resampler = LinearPcm16Resampler(
            source_rate=capture_config.sample_rate,
            target_rate=_OPENAI_REALTIME_PCM_SAMPLE_RATE,
        )

    async def run(self) -> None:
        self._logger.debug(
            "Opening OpenAI realtime transcription websocket: model=%s source_rate=%sHz target_rate=%sHz",
            self._provider_config.model,
            self._capture_config.sample_rate,
            _OPENAI_REALTIME_PCM_SAMPLE_RATE,
        )
        connection = await asyncio.wait_for(
            connect(
                _OPENAI_REALTIME_TRANSCRIPTION_URL,
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
            ),
            timeout=self._context.connect_timeout_seconds,
        )

        try:
            await self._send_client_event(
                connection,
                _build_transcription_session_update_event(self._provider_config),
            )
            await self._await_session_updated(connection)
            self._context.mark_ready(
                f"OpenAI realtime transcription ready ({self._provider_config.model})"
            )

            receiver_task = asyncio.create_task(self._receive_events(connection))
            sender_task = asyncio.create_task(self._send_audio(connection))

            done, pending = await asyncio.wait(
                {receiver_task, sender_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if sender_task in done and self._context.stop_requested.is_set():
                sender_task.result()
                self._logger.debug(
                    "Sender finished after stop request; waiting for final server events"
                )
                await asyncio.sleep(_FLUSH_TIMEOUT_SECONDS)
                await connection.close()
                await receiver_task
                return

            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()

            if receiver_task in done:
                raise SttSessionError("OpenAI realtime connection closed unexpectedly")
            raise SttSessionError("OpenAI realtime sender stopped unexpectedly")
        finally:
            await connection.close()
            self._logger.debug("Closed OpenAI realtime transcription websocket")

    async def _await_session_updated(self, connection: Any) -> None:
        while True:
            event = await asyncio.wait_for(
                self._recv_server_event(connection),
                timeout=self._context.connect_timeout_seconds,
            )
            if self._handle_server_event(event):
                self._logger.debug(
                    "Received OpenAI realtime session.updated acknowledgement"
                )
                return

    async def _send_audio(self, connection: Any) -> None:
        assert self._state.resampler is not None
        while True:
            try:
                chunk = await self._context.audio_queue.get(
                    timeout=_EVENT_POLL_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                if self._context.stop_requested.is_set():
                    pending_audio = self._state.resampler.flush()
                    if pending_audio:
                        await self._send_client_event(
                            connection,
                            {
                                "type": "input_audio_buffer.append",
                                "audio": _encode_audio(pending_audio),
                            },
                        )
                    return
                continue
            except QueueClosedError:
                return

            resampled = self._state.resampler.convert(chunk.pcm16)
            if not resampled:
                continue
            await self._send_client_event(
                connection,
                {
                    "type": "input_audio_buffer.append",
                    "audio": _encode_audio(resampled),
                },
            )

    async def _receive_events(self, connection: Any) -> None:
        while True:
            try:
                event = await self._recv_server_event(connection)
            except ConnectionClosedOK:
                self._logger.debug("OpenAI realtime websocket closed cleanly")
                return
            self._handle_server_event(event)

    def _handle_server_event(self, event: Any) -> bool:
        if _get_value(event, "type") == "session.updated":
            return True

        if _get_value(event, "type") == "error" and is_fatal_openai_realtime_error(
            event
        ):
            message = (
                _format_openai_error_message(_get_value(event, "error"))
                or "fatal OpenAI realtime error"
            )
            raise FatalRealtimeServerError(message)

        for normalized_event in normalize_openai_realtime_event(
            event, self._state.utterances
        ):
            self._context.publish_event(normalized_event)
        return False

    async def _recv_server_event(self, connection: Any) -> Any:
        message = await connection.recv()
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        if isinstance(message, str):
            return json.loads(message)
        return message

    async def _send_client_event(
        self, connection: Any, event: Mapping[str, Any]
    ) -> None:
        await connection.send(json.dumps(event))


class OpenAIRealtimeBackend(SttBackend):
    """Define the configured OpenAI Realtime backend."""

    name = "openai_realtime"

    def __init__(
        self,
        *,
        capture_config: CaptureConfig,
        retry_config: SttRetryConfig,
        provider_config: OpenAIRealtimeProviderConfig,
        api_key: str,
        logger: logging.Logger,
    ) -> None:
        if capture_config.channels != 1:
            raise SttSessionError(
                "OpenAI realtime transcription currently requires capture.channels = 1"
            )
        if capture_config.dtype != "int16":
            raise SttSessionError(
                "OpenAI realtime transcription currently requires capture.dtype = int16"
            )
        self._capture_config = capture_config
        self._provider_config = provider_config
        self._api_key = api_key
        self._logger = logger

    @property
    def logger(self) -> logging.Logger:
        """Return the backend logger used for transport diagnostics."""
        return self._logger

    def describe(self) -> str:
        """Return the CLI-friendly description of the configured backend."""
        return f"{self.name} ({self._provider_config.model})"

    def connecting_message(self) -> str:
        """Return the status message used before the first connection attempt."""
        return "connecting to OpenAI realtime transcription"

    def closing_message(self) -> str:
        """Return the status message used when shutdown begins."""
        return "closing OpenAI realtime session"

    def closed_message(self) -> str:
        """Return the status message used after the runner fully exits."""
        return "OpenAI realtime session closed"

    def stop_timeout_message(self) -> str:
        """Return the shutdown timeout error message for this backend."""
        return "Timed out waiting for OpenAI realtime session to stop"

    def create_attempt(self, *, context: AttemptContext) -> ConnectionAttempt:
        """Create a fresh OpenAI realtime connection attempt and state object."""
        return OpenAIRealtimeAttempt(
            state=OpenAIConnectionState(),
            context=context,
            provider_config=self._provider_config,
            capture_config=self._capture_config,
            api_key=self._api_key,
            logger=self._logger,
        )

    def is_retriable_error(self, exc: BaseException) -> bool:
        """Return whether the transport failure should trigger reconnect logic."""
        return is_retriable_openai_realtime_error(exc)

    def retrying_message(
        self, exc: BaseException, attempt: int, backoff_seconds: float
    ) -> str:
        """Build the CLI-visible retry status message for one transport failure."""
        return f"transport error: {exc}; retrying in {backoff_seconds:.1f}s"

    def exhausted_error(self, exc: BaseException) -> BaseException:
        """Return the terminal error surfaced after the retry budget is exhausted."""
        return SttSessionError("OpenAI realtime transport failed after retries")


def _build_transcription_session_update_event(
    provider_config: OpenAIRealtimeProviderConfig,
) -> dict[str, Any]:
    transcription: dict[str, Any] = {"model": provider_config.model}
    if provider_config.language:
        transcription["language"] = provider_config.language
    if provider_config.prompt:
        transcription["prompt"] = provider_config.prompt

    return {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": _OPENAI_REALTIME_PCM_SAMPLE_RATE,
                    },
                    "noise_reduction": {"type": provider_config.noise_reduction},
                    "transcription": transcription,
                    "turn_detection": {
                        "type": provider_config.turn_detection,
                        "prefix_padding_ms": provider_config.vad_prefix_padding_ms,
                        "silence_duration_ms": provider_config.vad_silence_duration_ms,
                        "threshold": provider_config.vad_threshold,
                    },
                }
            },
        },
    }


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _format_openai_error_message(error: Any) -> str | None:
    if error is None:
        return None
    message = _get_value(error, "message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    code = _get_value(error, "code")
    error_type = _get_value(error, "type")
    parts = [
        part for part in (error_type, code) if isinstance(part, str) and part.strip()
    ]
    if not parts:
        return None
    return " / ".join(parts)


def _contains_fatal_error_marker(message: str) -> bool:
    fatal_markers = (
        "auth",
        "unauthorized",
        "permission",
        "invalid_api_key",
        "invalid_request",
        "unsupported",
        "configuration",
    )
    return any(marker in message for marker in fatal_markers)


def _encode_audio(audio: bytes) -> str:
    return base64.b64encode(audio).decode("ascii")


__all__ = [
    "FatalRealtimeServerError",
    "OpenAIConnectionState",
    "OpenAIRealtimeBackend",
    "OpenAIUtteranceState",
    "_OPENAI_REALTIME_TRANSCRIPTION_URL",
    "_build_transcription_session_update_event",
    "is_fatal_openai_realtime_error",
    "is_retriable_openai_realtime_error",
    "normalize_openai_realtime_event",
]
