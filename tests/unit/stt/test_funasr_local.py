import asyncio
import logging
import ssl
from types import SimpleNamespace
from typing import Any, cast

import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from vrc_live_caption.config import (
    CaptureConfig,
    FunasrLocalProviderConfig,
    SttRetryConfig,
)
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.protocol import (
    build_client_start_message,
    build_client_stop_message,
    build_error_message,
    build_ready_message,
    build_transcript_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt import AsyncSttSessionRunner, SttStatus
from vrc_live_caption.stt.funasr_local import (
    FatalFunasrLocalServerError,
    FunasrLocalAttempt,
    FunasrLocalBackend,
    FunasrLocalConnectionState,
    FunasrLocalReadyEvent,
    _build_ssl_context,
    build_funasr_local_url,
    is_retriable_funasr_local_error,
    normalize_funasr_local_transcript_event,
    parse_funasr_local_ready_event,
    probe_funasr_local_service,
)
from vrc_live_caption.stt.types import (
    AttemptContext,
    SttStatusEvent,
    TranscriptRevisionEvent,
)


def _make_audio_queue() -> DropOldestAsyncQueue[AudioChunk]:
    return DropOldestAsyncQueue(
        max_items=4,
        logger=logging.getLogger("test.funasr_local.audio"),
        label="audio queue",
    )


class _FakeConnection:
    def __init__(self, responses: list[bytes | str | BaseException]) -> None:
        self._responses = list(responses)
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes | str:
        if not self._responses:
            raise ConnectionClosedOK(Close(1000, "done"), Close(1000, "done"))
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


def _make_attempt_context(
    *,
    audio_queue: DropOldestAsyncQueue[AudioChunk] | None = None,
) -> tuple[AttemptContext, list[Any], list[str]]:
    events: list[Any] = []
    ready_messages: list[str] = []
    context = AttemptContext(
        audio_queue=audio_queue or _make_audio_queue(),
        publish_event=events.append,
        mark_ready=ready_messages.append,
        stop_requested=asyncio.Event(),
        connect_timeout_seconds=0.1,
        logger=logging.getLogger("test.funasr_local.attempt"),
    )
    return context, events, ready_messages


def test_normalize_funasr_local_transcript_event_tracks_revisions() -> None:
    revisions: dict[int, int] = {}

    first = normalize_funasr_local_transcript_event(
        build_transcript_message(
            phase="online", segment_id=3, text="hello", is_final=False
        ),
        revisions,
    )
    second = normalize_funasr_local_transcript_event(
        build_transcript_message(
            phase="offline", segment_id=3, text="hello world", is_final=True
        ),
        revisions,
    )

    assert first == [
        TranscriptRevisionEvent(
            utterance_id="segment-3",
            revision=1,
            text="hello",
            is_final=False,
        )
    ]
    assert second == [
        TranscriptRevisionEvent(
            utterance_id="segment-3",
            revision=2,
            text="hello world",
            is_final=True,
        )
    ]
    assert revisions == {}


def test_normalize_funasr_local_transcript_event_ignores_non_transcripts() -> None:
    assert normalize_funasr_local_transcript_event({"type": "ready"}, {}) == []


def test_normalize_funasr_local_transcript_event_requires_segment_id() -> None:
    with pytest.raises(SttSessionError, match="missing segment_id"):
        normalize_funasr_local_transcript_event({"type": "transcript"}, {})


def test_parse_funasr_local_ready_event_keeps_device_metadata() -> None:
    ready_event = parse_funasr_local_ready_event(
        build_ready_message(
            "ready for test",
            resolved_device="cuda:0",
            device_policy="auto",
        )
    )

    assert ready_event is not None
    assert ready_event.message == "ready for test"
    assert ready_event.resolved_device == "cuda:0"
    assert ready_event.device_policy == "auto"


def test_parse_funasr_local_ready_event_handles_objects_and_empty_strings() -> None:
    ready_event = parse_funasr_local_ready_event(
        SimpleNamespace(
            type="ready",
            message=None,
            resolved_device="   ",
            device_policy="auto",
        )
    )

    assert ready_event is not None
    assert ready_event.message == ""
    assert ready_event.resolved_device is None
    assert ready_event.device_policy == "auto"
    assert parse_funasr_local_ready_event({"type": "transcript"}) is None


def test_is_retriable_funasr_local_error_classifies_transport_failures() -> None:
    invalid_status = InvalidStatus(Response(503, "busy", Headers(), b""))

    assert is_retriable_funasr_local_error(ConnectionClosedOK(None, None)) is True
    assert is_retriable_funasr_local_error(asyncio.TimeoutError()) is True
    assert is_retriable_funasr_local_error(OSError("boom")) is True
    assert is_retriable_funasr_local_error(invalid_status) is False
    assert (
        is_retriable_funasr_local_error(FatalFunasrLocalServerError("fatal")) is False
    )
    assert is_retriable_funasr_local_error(RuntimeError("other")) is False


def test_funasr_local_backend_publishes_ready_and_transcripts() -> None:
    async def scenario() -> None:
        received_messages: list[dict] = []

        async def handler(websocket) -> None:
            received_messages.append(decode_json_message(await websocket.recv()))
            await websocket.send(
                encode_json_message(build_ready_message("ready for test"))
            )
            await websocket.send(
                encode_json_message(
                    build_transcript_message(
                        phase="online",
                        segment_id=1,
                        text="hello",
                        is_final=False,
                    )
                )
            )
            await websocket.send(
                encode_json_message(
                    build_transcript_message(
                        phase="offline",
                        segment_id=1,
                        text="hello world",
                        is_final=True,
                    )
                )
            )
            received_messages.append(decode_json_message(await websocket.recv()))

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            runner = AsyncSttSessionRunner(
                backend=FunasrLocalBackend(
                    capture_config=CaptureConfig(),
                    retry_config=SttRetryConfig(),
                    provider_config=FunasrLocalProviderConfig(port=port),
                    logger=logging.getLogger("test.funasr_local.backend"),
                ),
                retry_config=SttRetryConfig(
                    connect_timeout_seconds=1.0, max_attempts=1
                ),
                audio_queue=_make_audio_queue(),
                event_buffer_max_items=16,
                logger=logging.getLogger("test.funasr_local.runner"),
            )

            await runner.start()
            events = [await runner.get_event(timeout=0.2) for _ in range(4)]
            await runner.close(timeout_seconds=1.0)
            events.extend(
                [
                    await runner.get_event(timeout=0.2),
                    await runner.get_event(timeout=0.2),
                ]
            )

        statuses = [event.status for event in events if hasattr(event, "status")]
        transcripts = [
            event for event in events if isinstance(event, TranscriptRevisionEvent)
        ]

        assert received_messages[0]["type"] == "start"
        assert received_messages[1]["type"] == "stop"
        assert statuses[:2] == [SttStatus.CONNECTING, SttStatus.READY]
        assert statuses[-2:] == [SttStatus.CLOSING, SttStatus.CLOSED]
        assert [event.text for event in transcripts] == ["hello", "hello world"]

    asyncio.run(scenario())


def test_funasr_local_backend_retries_after_unexpected_disconnect() -> None:
    async def scenario() -> None:
        connection_count = 0

        async def handler(websocket) -> None:
            nonlocal connection_count
            connection_count += 1
            await websocket.recv()
            await websocket.send(encode_json_message(build_ready_message("ready")))
            if connection_count == 1:
                await websocket.close()
                return
            await websocket.recv()

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            runner = AsyncSttSessionRunner(
                backend=FunasrLocalBackend(
                    capture_config=CaptureConfig(),
                    retry_config=SttRetryConfig(),
                    provider_config=FunasrLocalProviderConfig(port=port),
                    logger=logging.getLogger("test.funasr_local.retry.backend"),
                ),
                retry_config=SttRetryConfig(
                    connect_timeout_seconds=1.0,
                    max_attempts=2,
                    initial_backoff_seconds=0.1,
                    max_backoff_seconds=0.1,
                ),
                audio_queue=_make_audio_queue(),
                event_buffer_max_items=16,
                logger=logging.getLogger("test.funasr_local.retry.runner"),
            )

            await runner.start()
            await asyncio.sleep(0.1)
            events = []
            for _ in range(5):
                event = await runner.get_event(timeout=0.2)
                if event is not None:
                    events.append(event)
            await runner.close(timeout_seconds=1.0)

        statuses = [event.status for event in events if hasattr(event, "status")]

        assert connection_count >= 2
        assert SttStatus.RETRYING in statuses
        assert statuses.count(SttStatus.READY) >= 2

    asyncio.run(scenario())


def test_funasr_local_backend_surfaces_fatal_server_errors() -> None:
    async def scenario() -> None:
        async def handler(websocket) -> None:
            await websocket.recv()
            await websocket.send(
                encode_json_message(build_error_message("fatal boom", fatal=True))
            )

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            runner = AsyncSttSessionRunner(
                backend=FunasrLocalBackend(
                    capture_config=CaptureConfig(),
                    retry_config=SttRetryConfig(),
                    provider_config=FunasrLocalProviderConfig(port=port),
                    logger=logging.getLogger("test.funasr_local.fatal.backend"),
                ),
                retry_config=SttRetryConfig(
                    connect_timeout_seconds=1.0, max_attempts=1
                ),
                audio_queue=_make_audio_queue(),
                event_buffer_max_items=16,
                logger=logging.getLogger("test.funasr_local.fatal.runner"),
            )

            with pytest.raises(SttSessionError, match="fatal boom"):
                await runner.start()

        return None

    asyncio.run(scenario())


def test_probe_funasr_local_service_returns_ready_device_metadata() -> None:
    async def scenario() -> None:
        async def handler(websocket) -> None:
            await websocket.recv()
            await websocket.send(
                encode_json_message(
                    build_ready_message(
                        "ready",
                        resolved_device="cuda:0",
                        device_policy="auto",
                    )
                )
            )
            await websocket.recv()

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            result = await probe_funasr_local_service(
                capture_config=CaptureConfig(),
                provider_config=FunasrLocalProviderConfig(port=port),
                timeout_seconds=1.0,
            )

        assert result.message == "ready"
        assert result.resolved_device == "cuda:0"
        assert result.device_policy == "auto"

    asyncio.run(scenario())


def test_probe_funasr_local_service_uses_ssl_and_ignores_binary_frames(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        captured: dict[str, object] = {}
        connection = _FakeConnection(
            [
                b"binary-frame",
                encode_json_message(
                    build_ready_message(
                        "ready",
                        resolved_device="cuda:0",
                        device_policy="auto",
                    )
                ),
            ]
        )

        async def fake_connect(url, ssl=None):
            captured["url"] = url
            captured["ssl"] = ssl
            return connection

        monkeypatch.setattr("vrc_live_caption.stt.funasr_local.connect", fake_connect)
        result = await probe_funasr_local_service(
            capture_config=CaptureConfig(),
            provider_config=FunasrLocalProviderConfig(port=10096, use_ssl=True),
            timeout_seconds=0.1,
        )

        assert result.resolved_device == "cuda:0"
        assert captured["url"] == "wss://127.0.0.1:10096"
        assert isinstance(captured["ssl"], ssl.SSLContext)
        assert decode_json_message(cast(str, connection.sent[0]))["type"] == "start"
        assert decode_json_message(cast(str, connection.sent[1]))["type"] == "stop"
        assert connection.closed is True

    asyncio.run(scenario())


def test_probe_funasr_local_service_surfaces_server_errors(monkeypatch) -> None:
    async def scenario() -> None:
        async def run_case(
            *, fatal: bool, expected_exception: type[BaseException]
        ) -> None:
            connection = _FakeConnection(
                [encode_json_message(build_error_message("server boom", fatal=fatal))]
            )

            async def fake_connect(url, ssl=None):
                return connection

            monkeypatch.setattr(
                "vrc_live_caption.stt.funasr_local.connect",
                fake_connect,
            )
            with pytest.raises(expected_exception, match="server boom"):
                await probe_funasr_local_service(
                    capture_config=CaptureConfig(),
                    provider_config=FunasrLocalProviderConfig(),
                    timeout_seconds=0.1,
                )
            assert connection.closed is True

        await run_case(fatal=True, expected_exception=FatalFunasrLocalServerError)
        await run_case(fatal=False, expected_exception=SttSessionError)

    asyncio.run(scenario())


def test_funasr_local_attempt_formats_ready_message() -> None:
    context, _, _ = _make_attempt_context()
    attempt = FunasrLocalAttempt(
        state=FunasrLocalConnectionState(),
        context=context,
        provider_config=FunasrLocalProviderConfig(host="localhost", port=9001),
        capture_config=CaptureConfig(),
        logger=logging.getLogger("test.funasr_local.format_ready"),
    )

    assert (
        attempt._format_ready_message(
            FunasrLocalReadyEvent(
                message="ready",
                resolved_device="cuda:0",
                device_policy="auto",
            )
        )
        == "FunASR local sidecar ready (localhost:9001, device=cuda:0, policy=auto)"
    )
    assert (
        attempt._format_ready_message(FunasrLocalReadyEvent(message="ready"))
        == "FunASR local sidecar ready (localhost:9001)"
    )


def test_funasr_local_attempt_publishes_non_fatal_server_error_status() -> None:
    context, events, _ = _make_attempt_context()
    attempt = FunasrLocalAttempt(
        state=FunasrLocalConnectionState(),
        context=context,
        provider_config=FunasrLocalProviderConfig(),
        capture_config=CaptureConfig(),
        logger=logging.getLogger("test.funasr_local.handle_error"),
    )

    ready = attempt._handle_server_message(build_ready_message("ready"))
    assert ready is not None

    result = attempt._handle_server_message(
        build_error_message("soft boom", fatal=False)
    )

    assert result is None
    assert events == [SttStatusEvent(status=SttStatus.ERROR, message="soft boom")]


def test_funasr_local_attempt_flushes_after_stop(monkeypatch) -> None:
    async def scenario() -> None:
        context, _, ready_messages = _make_attempt_context()
        context.stop_requested.set()

        class _FlushConnection(_FakeConnection):
            async def recv(self) -> bytes | str:
                if self._responses:
                    return await super().recv()
                while len(self.sent) < 2:
                    await asyncio.sleep(0)
                raise ConnectionClosedOK(
                    Close(1000, "closed"),
                    Close(1000, "closed"),
                    True,
                )

        connection = _FlushConnection(
            [
                encode_json_message(
                    build_ready_message(
                        "ready",
                        resolved_device="cuda:0",
                        device_policy="auto",
                    )
                )
            ]
        )

        async def fake_connect(url, ssl=None):
            return connection

        monkeypatch.setattr("vrc_live_caption.stt.funasr_local.connect", fake_connect)
        attempt = FunasrLocalAttempt(
            state=FunasrLocalConnectionState(),
            context=context,
            provider_config=FunasrLocalProviderConfig(),
            capture_config=CaptureConfig(),
            logger=logging.getLogger("test.funasr_local.flush"),
        )

        await attempt.run()

        assert decode_json_message(
            cast(str, connection.sent[0])
        ) == build_client_start_message(
            sample_rate=16_000,
            channels=1,
        )
        assert (
            decode_json_message(cast(str, connection.sent[1]))
            == build_client_stop_message()
        )
        assert ready_messages == [
            "FunASR local sidecar ready (127.0.0.1:10095, device=cuda:0, policy=auto)"
        ]
        assert connection.closed is True

    asyncio.run(scenario())


def test_funasr_local_attempt_raises_when_receiver_disconnects(monkeypatch) -> None:
    async def scenario() -> None:
        context, _, _ = _make_attempt_context()
        connection = _FakeConnection(
            [
                encode_json_message(build_ready_message("ready")),
                ConnectionClosedOK(
                    Close(1000, "closed"),
                    Close(1000, "closed"),
                    True,
                ),
            ]
        )

        async def fake_connect(url, ssl=None):
            return connection

        monkeypatch.setattr("vrc_live_caption.stt.funasr_local.connect", fake_connect)
        attempt = FunasrLocalAttempt(
            state=FunasrLocalConnectionState(),
            context=context,
            provider_config=FunasrLocalProviderConfig(),
            capture_config=CaptureConfig(),
            logger=logging.getLogger("test.funasr_local.receiver_disconnect"),
        )

        with pytest.raises(OSError, match="connection closed unexpectedly"):
            await attempt.run()

        assert connection.closed is True

    asyncio.run(scenario())


def test_funasr_local_backend_helpers_cover_ssl_and_retry_text() -> None:
    backend = FunasrLocalBackend(
        capture_config=CaptureConfig(),
        retry_config=SttRetryConfig(),
        provider_config=FunasrLocalProviderConfig(
            host="localhost", port=9002, use_ssl=True
        ),
        logger=logging.getLogger("test.funasr_local.backend_helpers"),
    )

    assert build_funasr_local_url(backend._provider_config) == "wss://localhost:9002"
    assert (
        backend.retrying_message(OSError("boom"), 2, 1.5)
        == "local sidecar error: boom; retrying in 1.5s"
    )
    assert isinstance(backend.exhausted_error(OSError("boom")), SttSessionError)


def test_funasr_local_backend_messages_and_description() -> None:
    backend = FunasrLocalBackend(
        capture_config=CaptureConfig(),
        retry_config=SttRetryConfig(),
        provider_config=FunasrLocalProviderConfig(host="localhost", port=9002),
        logger=logging.getLogger("test.funasr_local.messages"),
    )

    assert backend.describe() == "funasr_local (localhost:9002)"
    assert backend.connecting_message() == "connecting to local FunASR sidecar"
    assert backend.closing_message() == "closing local FunASR sidecar session"
    assert backend.closed_message() == "local FunASR sidecar session closed"
    assert (
        backend.stop_timeout_message()
        == "Timed out waiting for the local FunASR sidecar session to stop"
    )


@pytest.mark.parametrize(
    ("capture_config", "message"),
    [
        (
            CaptureConfig(sample_rate=8_000),
            "FunASR local sidecar currently requires capture.sample_rate = 16000",
        ),
        (
            CaptureConfig(channels=2),
            "FunASR local sidecar currently requires capture.channels = 1",
        ),
        (
            CaptureConfig(dtype="float32"),
            'FunASR local sidecar currently requires capture.dtype = "int16"',
        ),
    ],
)
def test_funasr_local_backend_rejects_unsupported_capture_format(
    capture_config: CaptureConfig,
    message: str,
) -> None:
    with pytest.raises(SttSessionError, match=message):
        FunasrLocalBackend(
            capture_config=capture_config,
            retry_config=SttRetryConfig(),
            provider_config=FunasrLocalProviderConfig(),
            logger=logging.getLogger("test.funasr_local.invalid_capture"),
        )


def test_build_ssl_context_returns_expected_value() -> None:
    assert _build_ssl_context(FunasrLocalProviderConfig(use_ssl=False)) is None

    context = _build_ssl_context(FunasrLocalProviderConfig(use_ssl=True))

    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE
