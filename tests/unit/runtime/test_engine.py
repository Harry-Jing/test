import asyncio
import logging

import pytest

from tests.support.audio_fakes import FakeBackend
from tests.support.config_helpers import build_config
from vrc_live_caption.errors import AudioRuntimeError
from vrc_live_caption.runtime import DropOldestAsyncQueue, MicrophoneCapture


def test_microphone_capture_starts_and_emits_audio(tmp_path) -> None:
    async def scenario() -> None:
        config = build_config(tmp_path)
        backend = FakeBackend()
        queue = DropOldestAsyncQueue(
            max_items=4,
            logger=logging.getLogger("test.capture.queue"),
            label="audio queue",
        )
        capture = MicrophoneCapture(
            capture_config=config.capture,
            queue=queue,
            backend=backend,
            logger=logging.getLogger("test.capture"),
        )

        await capture.start()
        assert capture.resolved_device is not None
        assert backend.last_stream is not None

        backend.last_stream.emit(b"\x01\x00\x02\x00", frames=2)
        chunk = await queue.get(timeout=0.1)

        assert chunk.sequence == 1
        assert chunk.pcm16 == b"\x01\x00\x02\x00"
        assert chunk.frame_count == 2

        await capture.stop()
        assert backend.last_stream.stopped is True
        assert backend.last_stream.closed is True

    asyncio.run(scenario())


def test_microphone_capture_surfaces_start_failures(tmp_path) -> None:
    async def scenario() -> None:
        config = build_config(tmp_path)
        queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.capture.error.queue"),
            label="audio queue",
        )
        capture = MicrophoneCapture(
            capture_config=config.capture,
            queue=queue,
            backend=FakeBackend(fail_on_start=True),
            logger=logging.getLogger("test.capture.error"),
        )

        with pytest.raises(AudioRuntimeError, match="Failed to start audio capture"):
            await capture.start()

    asyncio.run(scenario())
