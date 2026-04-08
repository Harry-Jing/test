import asyncio
import logging

import pytest
from websockets.asyncio.server import serve

from tests.unit.stt._funasr_local_support import make_audio_queue
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
from vrc_live_caption.stt import AsyncSttSessionRunner, SttStatus
from vrc_live_caption.stt.funasr_local import FunasrLocalBackend
from vrc_live_caption.stt.types import TranscriptRevisionEvent


@pytest.mark.asyncio
class TestFunasrLocalBackend:
    async def test_when_sidecar_sends_ready_and_transcripts__then_runner_publishes_normalized_events(
        self,
    ) -> None:
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
                audio_queue=make_audio_queue(),
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

    async def test_when_connection_drops_unexpectedly__then_runner_retries_and_reconnects(
        self,
    ) -> None:
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
                audio_queue=make_audio_queue(),
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

    async def test_when_server_sends_fatal_error__then_runner_start_raises_stt_session_error(
        self,
    ) -> None:
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
                audio_queue=make_audio_queue(),
                event_buffer_max_items=16,
                logger=logging.getLogger("test.funasr_local.fatal.runner"),
            )

            with pytest.raises(SttSessionError, match="fatal boom"):
                await runner.start()


class TestFunasrLocalBackendValidation:
    @pytest.mark.parametrize(
        ("capture_config", "message"),
        [
            pytest.param(
                CaptureConfig(sample_rate=8_000),
                "FunASR local sidecar currently requires capture.sample_rate = 16000",
                id="sample-rate",
            ),
            pytest.param(
                CaptureConfig(channels=2),
                "FunASR local sidecar currently requires capture.channels = 1",
                id="channels",
            ),
            pytest.param(
                CaptureConfig(dtype="float32"),
                'FunASR local sidecar currently requires capture.dtype = "int16"',
                id="dtype",
            ),
        ],
    )
    def test_when_capture_format_is_unsupported__then_backend_rejects_it(
        self,
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
