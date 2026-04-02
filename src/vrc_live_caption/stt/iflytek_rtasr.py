"""Implement the iFLYTEK RTASR backend and connection attempts."""

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from websockets.asyncio.client import connect
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedOK,
    InvalidMessage,
    InvalidStatus,
)

from ..config import CaptureConfig, IflytekRtasrProviderConfig, SttRetryConfig
from ..errors import (
    SttProviderFatalError,
    SttProviderRetriableError,
    SttSessionError,
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
_IFLYTEK_RTASR_PCM_SAMPLE_RATE = 16_000
_IFLYTEK_RTASR_FRAME_BYTES = 1280
_IFLYTEK_RTASR_FRAME_INTERVAL_SECONDS = 0.04
_IFLYTEK_RTASR_FINAL_RESULT_TIMEOUT_SECONDS = 5.0
_IFLYTEK_RTASR_URL = "wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1"
_IFLYTEK_FATAL_ERROR_CODES = {
    "35001",
    "35004",
    "35005",
    "35010",
    "35013",
    "35014",
    "35015",
    "35016",
    "35017",
    "35019",
    "35020",
    "35030",
    "35031",
    "37000",
    "37010",
    "37011",
    "37012",
    "100002",
    "100012",
    "100013",
    "100015",
    "100016",
    "100018",
    "100019",
    "100020",
    "100021",
}
_IFLYTEK_RETRIABLE_ERROR_CODES = {
    "35002",
    "35003",
    "35006",
    "35007",
    "35008",
    "35009",
    "35011",
    "35012",
    "35018",
    "35022",
    "35099",
    "37001",
    "37002",
    "37005",
    "37006",
    "37007",
    "37008",
    "999999",
}
_IFLYTEK_VAD_MODES = {
    "far_field": "1",
    "near_field": "2",
}


class FatalIflytekServerError(SttProviderFatalError):
    """Raised when an iFLYTEK server event indicates a non-retriable failure."""


class RetriableIflytekServerError(SttProviderRetriableError):
    """Raised when an iFLYTEK server event indicates a retriable failure."""


@dataclass(slots=True)
class IflytekUtteranceState:
    """Track the latest normalized text, revision, and final flag per segment."""

    text: str = ""
    revision: int = 0
    is_final: bool = False


class IflytekAudioChunker:
    """Split PCM16 audio into provider-sized frames for paced transmission."""

    def __init__(self, *, frame_bytes: int = _IFLYTEK_RTASR_FRAME_BYTES) -> None:
        if frame_bytes < 1:
            raise ValueError("frame_bytes must be >= 1")
        self._frame_bytes = frame_bytes
        self._buffer = bytearray()

    def append(self, audio: bytes) -> list[bytes]:
        """Append PCM data and return any full frames ready to send."""
        self._buffer.extend(audio)
        return self._drain(final=False)

    def flush(self) -> list[bytes]:
        """Return remaining buffered audio as trailing provider frames."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[bytes]:
        frames: list[bytes] = []
        while len(self._buffer) >= self._frame_bytes:
            frames.append(bytes(self._buffer[: self._frame_bytes]))
            del self._buffer[: self._frame_bytes]
        if final and self._buffer:
            frames.append(bytes(self._buffer))
            self._buffer.clear()
        return frames


@dataclass(slots=True)
class IflytekConnectionState:
    """Store iFLYTEK attempt-scoped mutable state."""

    utterances: dict[str, IflytekUtteranceState] = field(default_factory=dict)
    chunker: IflytekAudioChunker = field(default_factory=IflytekAudioChunker)
    session_id: str | None = None
    next_frame_send_at: float | None = None
    end_sent: bool = False
    received_last_result: bool = False


def normalize_iflytek_rtasr_event(
    event: Any,
    utterances: dict[str, IflytekUtteranceState],
) -> list[SttEvent]:
    """Normalize raw iFLYTEK events into shared transcript or status events."""
    if _is_iflytek_asr_result(event):
        data = _get_iflytek_data(event)
        utterance_id = str(_get_value(data, "seg_id", "unknown"))
        text = _extract_iflytek_transcript_text(data)
        is_final = str(_get_nested_value(data, ("cn", "st", "type"), "1")) == "0"
        is_last = _is_truthy(_get_value(data, "ls"))
        state = utterances.setdefault(utterance_id, IflytekUtteranceState())
        if state.text == text and state.is_final == is_final:
            return []

        state.text = text
        state.is_final = is_final
        state.revision += 1
        normalized_event = TranscriptRevisionEvent(
            utterance_id=utterance_id,
            revision=state.revision,
            text=text,
            is_final=is_final,
        )
        if is_final or is_last:
            utterances.pop(utterance_id, None)
        return [normalized_event]

    if _is_iflytek_frc_result(event) or _is_iflytek_error_event(event):
        return [
            SttStatusEvent(
                status=SttStatus.ERROR,
                message=_format_iflytek_error_message(event),
            )
        ]

    return []


def is_fatal_iflytek_error_event(event: Any) -> bool:
    """Return whether an iFLYTEK event carries a fatal server error code."""
    return _extract_iflytek_code(event) in _IFLYTEK_FATAL_ERROR_CODES


def is_retriable_iflytek_error(exc: BaseException) -> bool:
    """Return whether a provider or transport failure should be retried."""
    if isinstance(exc, RetriableIflytekServerError):
        return True
    handshake_error_code = _extract_iflytek_handshake_error_code(exc)
    if handshake_error_code in _IFLYTEK_RETRIABLE_ERROR_CODES:
        return True
    if handshake_error_code in _IFLYTEK_FATAL_ERROR_CODES:
        return False
    if isinstance(exc, ConnectionClosed):
        reason = (getattr(exc, "reason", "") or "").lower()
        return not _contains_fatal_iflytek_marker(reason)
    if isinstance(exc, (asyncio.TimeoutError, OSError)):
        return True
    if isinstance(exc, InvalidStatus):
        return False
    return False


def build_iflytek_auth_url(
    *,
    provider_config: IflytekRtasrProviderConfig,
    app_id: str,
    api_key: str,
    api_secret: str,
    utc: str | None = None,
    session_uuid: str | None = None,
) -> str:
    """Build the authenticated iFLYTEK websocket URL for a new session."""
    params = build_iflytek_auth_params(
        provider_config=provider_config,
        app_id=app_id,
        api_key=api_key,
        api_secret=api_secret,
        utc=utc,
        session_uuid=session_uuid,
    )
    return f"{_IFLYTEK_RTASR_URL}?{_encode_query_params(params)}"


def build_iflytek_auth_params(
    *,
    provider_config: IflytekRtasrProviderConfig,
    app_id: str,
    api_key: str,
    api_secret: str,
    utc: str | None = None,
    session_uuid: str | None = None,
) -> dict[str, str]:
    """Build the signed iFLYTEK query parameters required for session startup."""
    params: dict[str, str] = {
        "accessKeyId": api_key,
        "appId": app_id,
        "audio_encode": "pcm_s16le",
        "eng_vad_mdn": _IFLYTEK_VAD_MODES[provider_config.vad_mode],
        "lang": provider_config.language,
        "samplerate": str(_IFLYTEK_RTASR_PCM_SAMPLE_RATE),
        "utc": utc or get_iflytek_utc_timestamp(),
        "uuid": session_uuid or uuid.uuid4().hex,
    }
    if provider_config.domain:
        params["pd"] = provider_config.domain
    params["signature"] = build_iflytek_signature(params, api_secret=api_secret)
    return params


def build_iflytek_signature(params: Mapping[str, str], *, api_secret: str) -> str:
    """Build the base64-encoded HMAC-SHA1 signature for iFLYTEK auth params."""
    base_string = _build_iflytek_signature_base_string(params)
    digest = hmac.new(
        api_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def get_iflytek_utc_timestamp(now: dt.datetime | None = None) -> str:
    """Format a timestamp in the UTC+08:00 form expected by iFLYTEK auth."""
    current = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return current.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime(
        "%Y-%m-%dT%H:%M:%S%z"
    )


class IflytekRtasrAttempt(ConnectionAttempt):
    """Run one iFLYTEK RTASR websocket attempt."""

    def __init__(
        self,
        *,
        state: IflytekConnectionState,
        context: AttemptContext,
        provider_config: IflytekRtasrProviderConfig,
        app_id: str,
        api_key: str,
        api_secret: str,
        logger: logging.Logger,
    ) -> None:
        self._state = state
        self._context = context
        self._provider_config = provider_config
        self._app_id = app_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._logger = logger

    async def run(self) -> None:
        self._logger.debug(
            "Opening iFLYTEK RTASR websocket: language=%s vad_mode=%s",
            self._provider_config.language,
            self._provider_config.vad_mode,
        )

        connection = await asyncio.wait_for(
            connect(
                build_iflytek_auth_url(
                    provider_config=self._provider_config,
                    app_id=self._app_id,
                    api_key=self._api_key,
                    api_secret=self._api_secret,
                )
            ),
            timeout=self._context.connect_timeout_seconds,
        )

        try:
            await self._await_session_started(connection)
            self._context.mark_ready(
                "iFLYTEK RTASR ready "
                f"({self._provider_config.language}, {self._provider_config.vad_mode})"
            )

            receiver_task = asyncio.create_task(self._receive_events(connection))
            sender_task = asyncio.create_task(self._send_audio(connection))
            done, pending = await asyncio.wait(
                {receiver_task, sender_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if self._context.stop_requested.is_set():
                await sender_task
                try:
                    await asyncio.wait_for(
                        receiver_task,
                        timeout=_IFLYTEK_RTASR_FINAL_RESULT_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    await connection.close()
                    try:
                        await receiver_task
                    except ConnectionClosedOK:
                        pass
                return

            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()

            if receiver_task in done:
                raise SttSessionError("iFLYTEK RTASR connection closed unexpectedly")
            raise SttSessionError("iFLYTEK RTASR sender stopped unexpectedly")
        finally:
            await connection.close()
            self._logger.debug("Closed iFLYTEK RTASR websocket")

    async def _await_session_started(self, connection: Any) -> None:
        while True:
            event = await asyncio.wait_for(
                self._recv_server_event(connection),
                timeout=self._context.connect_timeout_seconds,
            )
            session_id = _extract_iflytek_session_id(event)
            if session_id:
                self._state.session_id = session_id
                self._logger.debug(
                    "Received iFLYTEK session identifier: session_id=%s", session_id
                )
                return
            self._raise_for_iflytek_error(event)

    async def _send_audio(self, connection: Any) -> None:
        while True:
            try:
                chunk = await self._context.audio_queue.get(
                    timeout=_EVENT_POLL_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                if self._context.stop_requested.is_set():
                    await self._flush_audio_and_end(connection)
                    return
                continue
            except QueueClosedError:
                await self._flush_audio_and_end(connection)
                return

            for frame in self._state.chunker.append(chunk.pcm16):
                await self._send_audio_frame(connection, frame)

    async def _flush_audio_and_end(self, connection: Any) -> None:
        remaining_frames = self._state.chunker.flush()
        for frame in remaining_frames:
            await self._send_audio_frame(connection, frame)
        await self._send_end_event(connection)

    async def _send_audio_frame(self, connection: Any, frame: bytes) -> None:
        await self._pace_audio_frame()
        await connection.send(frame)

    async def _send_end_event(self, connection: Any) -> None:
        if self._state.end_sent:
            return
        if not self._state.session_id:
            raise SttSessionError("iFLYTEK RTASR sessionId was not established")
        self._state.end_sent = True
        await connection.send(
            json.dumps(
                {"end": True, "sessionId": self._state.session_id}, ensure_ascii=False
            )
        )

    async def _pace_audio_frame(self) -> None:
        loop = asyncio.get_running_loop()
        if self._state.next_frame_send_at is None:
            self._state.next_frame_send_at = loop.time()
        now = loop.time()
        delay = self._state.next_frame_send_at - now
        if delay > 0:
            await asyncio.sleep(delay)
        self._state.next_frame_send_at += _IFLYTEK_RTASR_FRAME_INTERVAL_SECONDS

    async def _receive_events(self, connection: Any) -> None:
        while True:
            try:
                event = await self._recv_server_event(connection)
            except ConnectionClosedOK:
                self._logger.debug("iFLYTEK RTASR websocket closed cleanly")
                return
            self._handle_server_event(event)
            if self._state.end_sent and self._state.received_last_result:
                self._logger.debug("Received final iFLYTEK result after end event")
                return

    def _handle_server_event(self, event: Any) -> None:
        if self._state.session_id is None:
            session_id = _extract_iflytek_session_id(event)
            if session_id:
                self._state.session_id = session_id

        self._raise_for_iflytek_error(event)
        for normalized_event in normalize_iflytek_rtasr_event(
            event, self._state.utterances
        ):
            self._context.publish_event(normalized_event)
        if _is_truthy(_get_value(_get_iflytek_data(event), "ls")):
            self._state.received_last_result = True

    def _raise_for_iflytek_error(self, event: Any) -> None:
        code = _extract_iflytek_code(event)
        if code in _IFLYTEK_FATAL_ERROR_CODES:
            raise FatalIflytekServerError(_format_iflytek_error_message(event))
        if code in _IFLYTEK_RETRIABLE_ERROR_CODES:
            raise RetriableIflytekServerError(_format_iflytek_error_message(event))

    async def _recv_server_event(self, connection: Any) -> Any:
        message = await connection.recv()
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        if isinstance(message, str):
            return json.loads(message)
        return message


class IflytekRtasrBackend(SttBackend):
    """Define the configured iFLYTEK RTASR backend."""

    name = "iflytek_rtasr"

    def __init__(
        self,
        *,
        capture_config: CaptureConfig,
        retry_config: SttRetryConfig,
        provider_config: IflytekRtasrProviderConfig,
        app_id: str,
        api_key: str,
        api_secret: str,
        logger: logging.Logger,
    ) -> None:
        if capture_config.sample_rate != _IFLYTEK_RTASR_PCM_SAMPLE_RATE:
            raise SttSessionError(
                "iFLYTEK RTASR currently requires capture.sample_rate = 16000"
            )
        if capture_config.channels != 1:
            raise SttSessionError(
                "iFLYTEK RTASR currently requires capture.channels = 1"
            )
        if capture_config.dtype != "int16":
            raise SttSessionError(
                'iFLYTEK RTASR currently requires capture.dtype = "int16"'
            )
        self._retry_config = retry_config
        self._provider_config = provider_config
        self._app_id = app_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._logger = logger

    @property
    def logger(self) -> logging.Logger:
        """Return the backend logger used for transport diagnostics."""
        return self._logger

    def describe(self) -> str:
        """Return the CLI-friendly description of the configured backend."""
        return (
            f"{self.name} ({self._provider_config.language}, "
            f"{self._provider_config.vad_mode})"
        )

    def connecting_message(self) -> str:
        """Return the status message used before the first connection attempt."""
        return "connecting to iFLYTEK RTASR"

    def closing_message(self) -> str:
        """Return the status message used when shutdown begins."""
        return "closing iFLYTEK RTASR session"

    def closed_message(self) -> str:
        """Return the status message used after the runner fully exits."""
        return "iFLYTEK RTASR session closed"

    def stop_timeout_message(self) -> str:
        """Return the shutdown timeout error message for this backend."""
        return "Timed out waiting for iFLYTEK RTASR session to stop"

    def create_attempt(self, *, context: AttemptContext) -> ConnectionAttempt:
        """Create a fresh iFLYTEK RTASR connection attempt and state object."""
        return IflytekRtasrAttempt(
            state=IflytekConnectionState(),
            context=context,
            provider_config=self._provider_config,
            app_id=self._app_id,
            api_key=self._api_key,
            api_secret=self._api_secret,
            logger=self._logger,
        )

    def is_retriable_error(self, exc: BaseException) -> bool:
        """Return whether the transport failure should trigger reconnect logic."""
        return is_retriable_iflytek_error(exc)

    def retrying_message(
        self, exc: BaseException, attempt: int, backoff_seconds: float
    ) -> str:
        """Build the CLI-visible retry status message for one transport failure."""
        return f"transport error: {exc}; retrying in {backoff_seconds:.1f}s"

    def exhausted_error(self, exc: BaseException) -> BaseException:
        """Return the terminal error surfaced after the retry budget is exhausted."""
        return SttSessionError("iFLYTEK RTASR transport failed after retries")


def _is_iflytek_asr_result(event: Any) -> bool:
    return (
        (_get_value(event, "msg_type", "") or "").lower() == "result"
        and (_get_value(event, "res_type", "") or "").lower() == "asr"
        and isinstance(_get_iflytek_data(event), Mapping)
    )


def _is_iflytek_frc_result(event: Any) -> bool:
    return (_get_value(event, "msg_type", "") or "").lower() == "result" and (
        _get_value(event, "res_type", "") or ""
    ).lower() == "frc"


def _is_iflytek_error_event(event: Any) -> bool:
    action = (_get_value(event, "action", "") or "").lower()
    msg_type = (_get_value(event, "msg_type", "") or "").lower()
    if action == "error" or msg_type == "error":
        return True
    return _extract_iflytek_code(event) is not None and not _is_iflytek_asr_result(
        event
    )


def _extract_iflytek_transcript_text(data: Any) -> str:
    parts: list[str] = []
    for rt in _coerce_iterable(_get_nested_value(data, ("cn", "st", "rt"), [])):
        for ws in _coerce_iterable(_get_value(rt, "ws", [])):
            word = _extract_iflytek_ws_word(ws)
            if word is not None:
                parts.append(word)
    return "".join(parts)


def _extract_iflytek_ws_word(ws: Any) -> str | None:
    for cw in _coerce_iterable(_get_value(ws, "cw", [])):
        word = _get_value(cw, "w")
        if isinstance(word, str):
            candidate = word.strip()
            if candidate:
                return candidate
    return None


def _extract_iflytek_code(event: Any) -> str | None:
    code = _get_value(event, "code")
    if code is None:
        code = _get_value(_get_iflytek_data(event), "code")
    if code is None:
        return None
    normalized = str(code).strip()
    return normalized or None


def _extract_iflytek_handshake_error_code(exc: BaseException) -> str | None:
    if not isinstance(exc, InvalidMessage):
        return None

    messages: list[str] = [str(exc)]
    if exc.__cause__ is not None:
        messages.append(str(exc.__cause__))

    for message in messages:
        for candidate in re.findall(r"\b\d{5,6}\b", message):
            if (
                candidate in _IFLYTEK_FATAL_ERROR_CODES
                or candidate in _IFLYTEK_RETRIABLE_ERROR_CODES
            ):
                return candidate
    return None


def _extract_iflytek_session_id(event: Any) -> str | None:
    data = _get_iflytek_data(event)
    for value in (
        _get_value(data, "sessionId"),
        _get_value(event, "sessionId"),
        _get_value(event, "sid"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _format_iflytek_error_message(event: Any) -> str:
    code = _extract_iflytek_code(event)
    data = _get_iflytek_data(event)
    description = _first_non_empty_str(
        _get_value(event, "desc"),
        _get_value(data, "desc"),
        _get_value(_get_value(data, "detail"), "domain"),
    )
    if code and description:
        return f"{code}: {description}"
    if description:
        return description
    if code:
        return code
    if _is_iflytek_frc_result(event):
        return "iFLYTEK RTASR returned an abnormal frc result"
    return "iFLYTEK RTASR error"


def _build_iflytek_signature_base_string(params: Mapping[str, str]) -> str:
    filtered = {
        key: value
        for key, value in params.items()
        if key != "signature" and value is not None and str(value).strip()
    }
    return "&".join(
        f"{quote(str(key), safe='')}={quote(str(filtered[key]), safe='')}"
        for key in sorted(filtered)
    )


def _encode_query_params(params: Mapping[str, str]) -> str:
    return "&".join(
        f"{quote(str(key), safe='')}={quote(str(params[key]), safe='')}"
        for key in sorted(params)
    )


def _get_iflytek_data(event: Any) -> Mapping[str, Any]:
    data = _get_value(event, "data", {})
    if isinstance(data, Mapping):
        return data
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return parsed
    return {}


def _get_nested_value(value: Any, keys: tuple[str, ...], default: Any = None) -> Any:
    current = value
    for key in keys:
        current = _get_value(current, key)
        if current is None:
            return default
    return current


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _coerce_iterable(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _contains_fatal_iflytek_marker(message: str) -> bool:
    return any(
        marker in message
        for marker in (
            "auth",
            "signature",
            "invalid",
            "permission",
            "unauthorized",
            "account",
            "expired",
        )
    )


def _first_non_empty_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            candidate = value.strip()
            if candidate:
                return candidate
    return None


__all__ = [
    "FatalIflytekServerError",
    "IflytekAudioChunker",
    "IflytekConnectionState",
    "IflytekRtasrBackend",
    "IflytekUtteranceState",
    "RetriableIflytekServerError",
    "_IFLYTEK_RTASR_URL",
    "_build_iflytek_signature_base_string",
    "build_iflytek_auth_params",
    "build_iflytek_auth_url",
    "build_iflytek_signature",
    "get_iflytek_utc_timestamp",
    "is_fatal_iflytek_error_event",
    "is_retriable_iflytek_error",
    "normalize_iflytek_rtasr_event",
]
