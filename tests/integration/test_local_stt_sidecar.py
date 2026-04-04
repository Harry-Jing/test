import asyncio
import logging
import time
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from tests.support.replay import iter_wav_chunks
from vrc_live_caption.config import (
    CaptureConfig,
    FunasrLocalProviderConfig,
    SttRetryConfig,
)
from vrc_live_caption.local_stt.funasr.config import FunasrLocalServiceConfig
from vrc_live_caption.local_stt.funasr.session import FunasrWebsocketSession
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt import AsyncSttSessionRunner
from vrc_live_caption.stt.funasr_local import (
    FunasrLocalBackend,
    probe_funasr_local_service,
)
from vrc_live_caption.stt.types import TranscriptRevisionEvent

_TEST_WAV_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "audio" / "test.wav"


class _FakeBundle:
    def __init__(self) -> None:
        self._vad_call_count = 0
        self._online_call_count = 0
        self._offline_call_count = 0

    def detect_speech_boundary(self, *, audio: bytes, state: dict) -> tuple[int, int]:
        self._vad_call_count += 1
        if self._vad_call_count == 1:
            return (0, -1)
        if self._vad_call_count == 4:
            return (-1, 240)
        return (-1, -1)

    def transcribe_online(self, *, audio: bytes, state: dict) -> str:
        self._online_call_count += 1
        return "hello local stt" if self._online_call_count == 1 else "hello local stt"

    def transcribe_offline(
        self,
        *,
        audio: bytes,
        state: dict,
        punc_state: dict,
    ) -> str:
        self._offline_call_count += 1
        return "hello local stt final"


@pytest.mark.integration
def test_local_stt_sidecar_replays_wav_through_websocket_runner() -> None:
    async def scenario() -> None:
        audio_queue = DropOldestAsyncQueue[AudioChunk](
            max_items=32,
            logger=logging.getLogger("test.integration.local_stt.audio"),
            label="audio queue",
        )
        fake_bundle = _FakeBundle()

        async def handler(websocket) -> None:
            session = FunasrWebsocketSession(
                websocket=websocket,
                config=FunasrLocalServiceConfig(chunk_size=(0, 1, 0), chunk_interval=1),
                models=fake_bundle,
                executor=None,
                resolved_device="cuda:0",
                device_policy="auto",
                logger=logging.getLogger("test.integration.local_stt.session"),
            )
            await session.run()

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            probe_result = await probe_funasr_local_service(
                capture_config=CaptureConfig(),
                provider_config=FunasrLocalProviderConfig(port=port),
                timeout_seconds=1.0,
            )
            runner = AsyncSttSessionRunner(
                backend=FunasrLocalBackend(
                    capture_config=CaptureConfig(),
                    retry_config=SttRetryConfig(),
                    provider_config=FunasrLocalProviderConfig(port=port),
                    logger=logging.getLogger("test.integration.local_stt.backend"),
                ),
                retry_config=SttRetryConfig(
                    connect_timeout_seconds=1.0, max_attempts=1
                ),
                audio_queue=audio_queue,
                event_buffer_max_items=32,
                logger=logging.getLogger("test.integration.local_stt.runner"),
            )

            await runner.start()
            bytes_per_frame = 2
            for sequence, pcm16 in enumerate(
                iter_wav_chunks(
                    _TEST_WAV_PATH,
                    frames_per_chunk=CaptureConfig().frames_per_chunk,
                ),
                start=1,
            ):
                audio_queue.put_nowait(
                    AudioChunk(
                        sequence=sequence,
                        pcm16=pcm16,
                        frame_count=len(pcm16) // bytes_per_frame,
                        captured_at_monotonic=time.monotonic(),
                    )
                )
                await asyncio.sleep(0)
                if fake_bundle._offline_call_count >= 1:
                    break

            await runner.close(timeout_seconds=1.0)
            events = []
            while True:
                event = await runner.get_event(timeout=0.0)
                if event is None:
                    break
                events.append(event)

        transcripts = [
            event for event in events if isinstance(event, TranscriptRevisionEvent)
        ]

        assert probe_result.resolved_device == "cuda:0"
        assert probe_result.device_policy == "auto"
        assert any(not event.is_final for event in transcripts)
        assert any(event.is_final for event in transcripts)

    asyncio.run(scenario())
