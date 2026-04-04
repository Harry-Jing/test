import asyncio
import logging

import pytest

from tests.support.audio_fakes import FakeBackend, FakeStream
from tests.support.config_helpers import build_config
from vrc_live_caption.errors import AudioRuntimeError
from vrc_live_caption.runtime import DropOldestAsyncQueue, MicrophoneCapture


class _RecordingBackend(FakeBackend):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.open_calls = 0

    def open_input_stream(self, *, capture_config, device_index, callback):
        self.open_calls += 1
        return super().open_input_stream(
            capture_config=capture_config,
            device_index=device_index,
            callback=callback,
        )


class _BrokenStream(FakeStream):
    def __init__(
        self,
        callback,
        *,
        stop_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        super().__init__(callback)
        self.stop_error = stop_error
        self.close_error = close_error

    def stop(self) -> None:
        if self.stop_error is not None:
            raise self.stop_error
        super().stop()

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        super().close()


class _BrokenBackend(FakeBackend):
    def __init__(
        self,
        *,
        stop_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        super().__init__()
        self.stop_error = stop_error
        self.close_error = close_error

    def open_input_stream(self, *, capture_config, device_index, callback):
        self.last_stream = _BrokenStream(
            callback,
            stop_error=self.stop_error,
            close_error=self.close_error,
        )
        return self.last_stream


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


def test_microphone_capture_start_is_idempotent(tmp_path) -> None:
    async def scenario() -> None:
        config = build_config(tmp_path)
        backend = _RecordingBackend()
        queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.capture.idempotent.queue"),
            label="audio queue",
        )
        capture = MicrophoneCapture(
            capture_config=config.capture,
            queue=queue,
            backend=backend,
            logger=logging.getLogger("test.capture.idempotent"),
        )

        await capture.start()
        await capture.start()
        await capture.stop()

        assert backend.open_calls == 1

    asyncio.run(scenario())


def test_microphone_capture_stop_is_noop_when_never_started(tmp_path) -> None:
    async def scenario() -> None:
        config = build_config(tmp_path)
        capture = MicrophoneCapture(
            capture_config=config.capture,
            queue=DropOldestAsyncQueue(
                max_items=1,
                logger=logging.getLogger("test.capture.noop.queue"),
                label="audio queue",
            ),
            backend=FakeBackend(),
            logger=logging.getLogger("test.capture.noop"),
        )

        await capture.stop()
        capture.check_health()

    asyncio.run(scenario())


def test_microphone_capture_stop_preserves_first_stream_error(tmp_path) -> None:
    async def scenario() -> None:
        config = build_config(tmp_path)
        queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.capture.stop_error.queue"),
            label="audio queue",
        )
        capture = MicrophoneCapture(
            capture_config=config.capture,
            queue=queue,
            backend=_BrokenBackend(
                stop_error=RuntimeError("stop boom"),
                close_error=RuntimeError("close boom"),
            ),
            logger=logging.getLogger("test.capture.stop_error"),
        )

        await capture.start()
        await capture.stop()

        with pytest.raises(AudioRuntimeError, match="stop boom"):
            capture.check_health()

    asyncio.run(scenario())


def test_microphone_capture_stream_callback_ignores_missing_loop(tmp_path) -> None:
    config = build_config(tmp_path)
    queue = DropOldestAsyncQueue(
        max_items=1,
        logger=logging.getLogger("test.capture.callback.queue"),
        label="audio queue",
    )
    capture = MicrophoneCapture(
        capture_config=config.capture,
        queue=queue,
        backend=FakeBackend(),
        logger=logging.getLogger("test.capture.callback"),
    )

    capture._stream_callback(b"\x01\x00", 1, None, "")

    assert queue.qsize() == 0


def test_microphone_capture_stream_callback_logs_status_and_increments_sequence(
    tmp_path,
    caplog,
) -> None:
    async def scenario() -> None:
        config = build_config(tmp_path)
        backend = FakeBackend()
        queue = DropOldestAsyncQueue(
            max_items=4,
            logger=logging.getLogger("test.capture.status.queue"),
            label="audio queue",
        )
        capture = MicrophoneCapture(
            capture_config=config.capture,
            queue=queue,
            backend=backend,
            logger=logging.getLogger("test.capture.status"),
            now=lambda: 12.5,
        )

        await capture.start()
        assert backend.last_stream is not None
        backend.last_stream.emit(b"\x01\x00", frames=1, status="overrun")
        backend.last_stream.emit(b"\x02\x00", frames=1, status="")
        first = await queue.get(timeout=0.1)
        second = await queue.get(timeout=0.1)

        assert first.sequence == 1
        assert second.sequence == 2
        assert first.captured_at_monotonic == 12.5
        assert second.captured_at_monotonic == 12.5

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())

    assert "Audio input status: overrun" in caplog.text
