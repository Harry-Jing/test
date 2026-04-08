"""Implement the local FunASR websocket-backed STT backend."""

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, InvalidStatus

from ..config import CaptureConfig, FunasrLocalProviderConfig, SttRetryConfig
from ..errors import SttProviderFatalError, SttSessionError
from ..local_stt.funasr.protocol import (
    build_client_start_message,
    build_client_stop_message,
    decode_json_message,
)
from ..runtime import QueueClosedError
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
_FLUSH_TIMEOUT_SECONDS = 2.0


class FatalFunasrLocalServerError(SttProviderFatalError):
    """Raised when the local FunASR sidecar reports a fatal error."""


@dataclass(frozen=True, slots=True)
class FunasrLocalReadyEvent:
    """Store the ready metadata returned by the local sidecar."""

    message: str
    resolved_device: str | None = None
    device_policy: str | None = None


@dataclass(slots=True)
class FunasrLocalConnectionState:
    """Store attempt-scoped transcript revision tracking."""

    segment_revisions: dict[int, int] = field(default_factory=dict)


def normalize_funasr_local_transcript_event(
    event: Any,
    segment_revisions: dict[int, int],
) -> list[SttEvent]:
    """Normalize local sidecar transcript events into revision events."""
    event_type = _get_value(event, "type")
    if event_type != "transcript":
        return []

    segment_id_raw = _get_value(event, "segment_id")
    if segment_id_raw is None:
        raise SttSessionError("FunASR local transcript event is missing segment_id")
    segment_id = int(segment_id_raw)
    text = _coerce_text(_get_value(event, "text", ""))
    is_final = bool(_get_value(event, "is_final", False))
    revision = segment_revisions.get(segment_id, 0) + 1
    segment_revisions[segment_id] = revision
    normalized = TranscriptRevisionEvent(
        utterance_id=f"segment-{segment_id}",
        revision=revision,
        text=text,
        is_final=is_final,
    )
    if is_final:
        segment_revisions.pop(segment_id, None)
    return [normalized]


def is_retriable_funasr_local_error(exc: BaseException) -> bool:
    """Return whether a local sidecar failure should trigger reconnect logic."""
    if isinstance(exc, FatalFunasrLocalServerError):
        return False
    if isinstance(exc, ConnectionClosed):
        return True
    if isinstance(exc, (asyncio.TimeoutError, OSError)):
        return True
    if isinstance(exc, InvalidStatus):
        return False
    return False


async def probe_funasr_local_service(
    *,
    capture_config: CaptureConfig,
    provider_config: FunasrLocalProviderConfig,
    timeout_seconds: float = 3.0,
) -> FunasrLocalReadyEvent:
    """Verify that the local sidecar accepts a session and reports ready."""
    connection = await asyncio.wait_for(
        connect(
            build_funasr_local_url(provider_config),
            ssl=_build_ssl_context(provider_config),
        ),
        timeout=timeout_seconds,
    )
    try:
        await connection.send(
            json.dumps(
                build_client_start_message(
                    sample_rate=capture_config.sample_rate,
                    channels=capture_config.channels,
                ),
                ensure_ascii=False,
            )
        )
        while True:
            message = await asyncio.wait_for(connection.recv(), timeout=timeout_seconds)
            if isinstance(message, bytes):
                continue
            event = decode_json_message(message)
            ready_event = parse_funasr_local_ready_event(event)
            if ready_event is not None:
                await connection.send(
                    json.dumps(build_client_stop_message(), ensure_ascii=False)
                )
                return ready_event
            event_type = _get_value(event, "type")
            if event_type == "error":
                message = _coerce_text(
                    _get_value(event, "message", "FunASR local sidecar error")
                )
                if bool(_get_value(event, "fatal", True)):
                    raise FatalFunasrLocalServerError(message)
                raise SttSessionError(message)
    finally:
        await connection.close()


class FunasrLocalAttempt(ConnectionAttempt):
    """Run one websocket-backed local FunASR attempt."""

    def __init__(
        self,
        *,
        state: FunasrLocalConnectionState,
        context: AttemptContext,
        provider_config: FunasrLocalProviderConfig,
        capture_config: CaptureConfig,
        logger: logging.Logger,
    ) -> None:
        self._state = state
        self._context = context
        self._provider_config = provider_config
        self._capture_config = capture_config
        self._logger = logger
        self._stop_sent = False

    async def run(self) -> None:
        connection = await asyncio.wait_for(
            connect(
                build_funasr_local_url(self._provider_config),
                ssl=_build_ssl_context(self._provider_config),
            ),
            timeout=self._context.connect_timeout_seconds,
        )
        try:
            await self._send_json(
                connection,
                build_client_start_message(
                    sample_rate=self._capture_config.sample_rate,
                    channels=self._capture_config.channels,
                ),
            )
            ready_event = await self._await_ready(connection)
            self._context.mark_ready(self._format_ready_message(ready_event))

            receiver_task = asyncio.create_task(self._receive_events(connection))
            sender_task = asyncio.create_task(self._send_audio(connection))
            done, pending = await asyncio.wait(
                {receiver_task, sender_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if sender_task in done and self._stop_sent:
                sender_task.result()
                try:
                    await asyncio.wait_for(
                        receiver_task, timeout=_FLUSH_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    await connection.close()
                    await asyncio.gather(receiver_task, return_exceptions=True)
                return

            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()

            if receiver_task in done:
                raise OSError("FunASR local sidecar connection closed unexpectedly")
            raise SttSessionError("FunASR local sidecar sender stopped unexpectedly")
        finally:
            await connection.close()

    async def _await_ready(self, connection: Any) -> FunasrLocalReadyEvent:
        while True:
            message = await asyncio.wait_for(
                connection.recv(),
                timeout=self._context.connect_timeout_seconds,
            )
            if isinstance(message, bytes):
                continue
            ready_event = self._handle_server_message(decode_json_message(message))
            if ready_event is not None:
                return ready_event

    async def _send_audio(self, connection: Any) -> None:
        while True:
            try:
                chunk = await self._context.audio_queue.get(
                    timeout=_EVENT_POLL_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                if self._context.stop_requested.is_set():
                    await self._send_stop(connection)
                    return
                continue
            except QueueClosedError:
                await self._send_stop(connection)
                return

            await connection.send(chunk.pcm16)
            if self._context.stop_requested.is_set():
                await self._send_stop(connection)
                return

    async def _receive_events(self, connection: Any) -> None:
        while True:
            try:
                message = await connection.recv()
            except ConnectionClosedOK:
                return
            if isinstance(message, bytes):
                continue
            self._handle_server_message(decode_json_message(message))

    def _handle_server_message(self, event: Any) -> FunasrLocalReadyEvent | None:
        ready_event = parse_funasr_local_ready_event(event)
        if ready_event is not None:
            return ready_event
        event_type = _get_value(event, "type")
        if event_type == "error":
            message = _coerce_text(
                _get_value(event, "message", "FunASR local sidecar error")
            )
            if bool(_get_value(event, "fatal", True)):
                raise FatalFunasrLocalServerError(message)
            self._context.publish_event(
                SttStatusEvent(status=SttStatus.ERROR, message=message)
            )
            return None

        for normalized_event in normalize_funasr_local_transcript_event(
            event, self._state.segment_revisions
        ):
            self._context.publish_event(normalized_event)
        return None

    async def _send_stop(self, connection: Any) -> None:
        if self._stop_sent:
            return
        self._stop_sent = True
        await self._send_json(connection, build_client_stop_message())

    async def _send_json(self, connection: Any, payload: dict[str, Any]) -> None:
        await connection.send(json.dumps(payload, ensure_ascii=False))

    def _format_ready_message(self, ready_event: FunasrLocalReadyEvent) -> str:
        location = f"{self._provider_config.host}:{self._provider_config.port}"
        if ready_event.resolved_device:
            if ready_event.device_policy:
                return (
                    "FunASR local sidecar ready "
                    f"({location}, device={ready_event.resolved_device}, policy={ready_event.device_policy})"
                )
            return (
                "FunASR local sidecar ready "
                f"({location}, device={ready_event.resolved_device})"
            )
        return f"FunASR local sidecar ready ({location})"


class FunasrLocalBackend(SttBackend):
    """Define the configured local FunASR websocket backend."""

    name = "funasr_local"

    def __init__(
        self,
        *,
        capture_config: CaptureConfig,
        retry_config: SttRetryConfig,
        provider_config: FunasrLocalProviderConfig,
        logger: logging.Logger,
    ) -> None:
        if capture_config.sample_rate != 16_000:
            raise SttSessionError(
                "FunASR local sidecar currently requires capture.sample_rate = 16000"
            )
        if capture_config.channels != 1:
            raise SttSessionError(
                "FunASR local sidecar currently requires capture.channels = 1"
            )
        if capture_config.dtype != "int16":
            raise SttSessionError(
                'FunASR local sidecar currently requires capture.dtype = "int16"'
            )
        self._capture_config = capture_config
        self._provider_config = provider_config
        self._logger = logger

    @property
    def logger(self) -> logging.Logger:
        """Return the backend logger used for sidecar diagnostics."""
        return self._logger

    def describe(self) -> str:
        """Return the CLI-friendly description of the configured backend."""
        return (
            f"{self.name} ({self._provider_config.host}:{self._provider_config.port})"
        )

    def connecting_message(self) -> str:
        """Return the status message used before the first connection attempt."""
        return "connecting to local FunASR sidecar"

    def closing_message(self) -> str:
        """Return the status message used when shutdown begins."""
        return "closing local FunASR sidecar session"

    def closed_message(self) -> str:
        """Return the status message used after the runner fully exits."""
        return "local FunASR sidecar session closed"

    def stop_timeout_message(self) -> str:
        """Return the shutdown timeout error message for this backend."""
        return "Timed out waiting for the local FunASR sidecar session to stop"

    def create_attempt(self, *, context: AttemptContext) -> ConnectionAttempt:
        """Create a fresh local-sidecar connection attempt and state object."""
        return FunasrLocalAttempt(
            state=FunasrLocalConnectionState(),
            context=context,
            provider_config=self._provider_config,
            capture_config=self._capture_config,
            logger=self._logger,
        )

    def is_retriable_error(self, exc: BaseException) -> bool:
        """Return whether the failure should trigger reconnect logic."""
        return is_retriable_funasr_local_error(exc)

    def retrying_message(
        self, exc: BaseException, attempt: int, backoff_seconds: float
    ) -> str:
        """Build the CLI-visible retry status message for one local failure."""
        return f"local sidecar error: {exc}; retrying in {backoff_seconds:.1f}s"

    def exhausted_error(self, exc: BaseException) -> BaseException:
        """Return the terminal error raised after the retry budget is exhausted."""
        return SttSessionError("Local FunASR sidecar transport failed after retries")


def build_funasr_local_url(provider_config: FunasrLocalProviderConfig) -> str:
    """Return the websocket URL for the local sidecar connection."""
    scheme = "wss" if provider_config.use_ssl else "ws"
    return f"{scheme}://{provider_config.host}:{provider_config.port}"


def _build_ssl_context(
    provider_config: FunasrLocalProviderConfig,
) -> ssl.SSLContext | None:
    if not provider_config.use_ssl:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def parse_funasr_local_ready_event(event: Any) -> FunasrLocalReadyEvent | None:
    """Parse one sidecar ready event and keep optional device metadata."""
    if _get_value(event, "type") != "ready":
        return None
    return FunasrLocalReadyEvent(
        message=_coerce_text(
            _get_value(event, "message", "FunASR local sidecar ready")
        ),
        resolved_device=_coerce_optional_text(_get_value(event, "resolved_device")),
        device_policy=_coerce_optional_text(_get_value(event, "device_policy")),
    )


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _coerce_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _coerce_text(value).strip()
    return text or None


__all__ = [
    "FatalFunasrLocalServerError",
    "FunasrLocalBackend",
    "FunasrLocalConnectionState",
    "FunasrLocalReadyEvent",
    "build_funasr_local_url",
    "is_retriable_funasr_local_error",
    "normalize_funasr_local_transcript_event",
    "parse_funasr_local_ready_event",
    "probe_funasr_local_service",
]
