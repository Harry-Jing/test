import asyncio
import logging

import pytest
from websockets.asyncio.server import serve

from vrc_live_caption.config import (
    CaptureConfig,
    FunasrLocalProviderConfig,
    SttRetryConfig,
)
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.protocol import (
    build_error_message,
    build_ready_message,
    build_transcript_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt import AsyncSttSessionRunner, SttStatus
from vrc_live_caption.stt.funasr_local import (
    FunasrLocalBackend,
    normalize_funasr_local_transcript_event,
)
from vrc_live_caption.stt.types import TranscriptRevisionEvent


def _make_audio_queue() -> DropOldestAsyncQueue[AudioChunk]:
    return DropOldestAsyncQueue(
        max_items=4,
        logger=logging.getLogger("test.funasr_local.audio"),
        label="audio queue",
    )


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
                retry_config=SttRetryConfig(connect_timeout_seconds=1.0, max_attempts=1),
                audio_queue=_make_audio_queue(),
                event_buffer_max_items=16,
                logger=logging.getLogger("test.funasr_local.runner"),
            )

            await runner.start()
            events = [await runner.get_event(timeout=0.2) for _ in range(4)]
            await runner.close(timeout_seconds=1.0)
            events.extend(
                [await runner.get_event(timeout=0.2), await runner.get_event(timeout=0.2)]
            )

        statuses = [event.status for event in events if hasattr(event, "status")]
        transcripts = [event for event in events if isinstance(event, TranscriptRevisionEvent)]

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
                retry_config=SttRetryConfig(connect_timeout_seconds=1.0, max_attempts=1),
                audio_queue=_make_audio_queue(),
                event_buffer_max_items=16,
                logger=logging.getLogger("test.funasr_local.fatal.runner"),
            )

            with pytest.raises(SttSessionError, match="fatal boom"):
                await runner.start()

        return None

    asyncio.run(scenario())
