import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from tests.support.fakes.stt import FakeSttBackend
from vrc_live_caption.audio import AudioDeviceInfo
from vrc_live_caption.config import SttRetryConfig
from vrc_live_caption.errors import AudioRuntimeError
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue, MicrophoneCapture
from vrc_live_caption.stt import AsyncSttSessionRunner, TranscriptRevisionEvent


class FakeCapture:
    def __init__(self, queue: DropOldestAsyncQueue[AudioChunk]) -> None:
        self.queue = queue
        self.resolved_device = AudioDeviceInfo(
            index=7,
            name="Fake Mic",
            max_input_channels=1,
            default_sample_rate=16_000.0,
            is_default=True,
        )
        self.started = False
        self.stopped = False
        self.fail_on_check: str | None = None
        self.stop_error: BaseException | None = None
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        self.started = True

    async def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped = True

    def check_health(self) -> None:
        if self.fail_on_check:
            raise AudioRuntimeError(self.fail_on_check)


class FakeTranscriptOutput:
    def __init__(self) -> None:
        self.started = False
        self.shutdown_calls = 0
        self.events: list[TranscriptRevisionEvent] = []
        self.shutdown_error: BaseException | None = None

    async def start(self) -> None:
        self.started = True

    def handle_revision(self, event: TranscriptRevisionEvent) -> None:
        self.events.append(event)

    async def shutdown(self, *, timeout_seconds: float = 1.0) -> None:
        self.shutdown_calls += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error


class StubSessionRunner:
    def __init__(
        self,
        *,
        events: list[object] | None = None,
        close_error: BaseException | None = None,
        health_error: BaseException | None = None,
        backend_description: str = "stub backend",
        event_dropped_items: int = 0,
    ) -> None:
        self._events = list(events or [])
        self.close_error = close_error
        self.health_error = health_error
        self.backend_description = backend_description
        self.event_dropped_items = event_dropped_items
        self.start_calls = 0
        self.close_calls = 0
        self.timeouts: list[float] = []

    async def start(self) -> None:
        self.start_calls += 1

    async def get_event(self, *, timeout: float):
        self.timeouts.append(timeout)
        if self._events:
            return self._events.pop(0)
        return None

    def check_health(self) -> None:
        if self.health_error is not None:
            raise self.health_error

    async def close(self, *, timeout_seconds: float) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FakeQueue:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.closed = False

    async def get(self, *, timeout: float):
        if not self._responses:
            raise asyncio.TimeoutError
        value = self._responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class FakeCaptureForRecording:
    def __init__(self, queue, *, health_error: BaseException | None = None) -> None:
        self.capture_config = SimpleNamespace(sample_rate=16_000, channels=1)
        self.queue = queue
        self.health_error = health_error
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1

    def check_health(self) -> None:
        if self.health_error is not None:
            raise self.health_error


class FakeSink:
    instances: list["FakeSink"] = []

    def __init__(self, output_path: Path, *, sample_rate: int, channels: int) -> None:
        self.output_path = output_path
        self.sample_rate = sample_rate
        self.channels = channels
        self.writes: list[AudioChunk] = []
        self.closed = False
        self.__class__.instances.append(self)

    def write(self, chunk: AudioChunk) -> None:
        self.writes.append(chunk)

    def close(self) -> None:
        self.closed = True


def make_runner(
    backend: FakeSttBackend,
    audio_queue: DropOldestAsyncQueue[AudioChunk],
) -> AsyncSttSessionRunner:
    return AsyncSttSessionRunner(
        backend=backend,
        retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
        audio_queue=audio_queue,
        event_buffer_max_items=16,
        logger=logging.getLogger("test.pipeline.runner"),
    )


def cast_stub_runner(runner: StubSessionRunner) -> AsyncSttSessionRunner:
    return cast(AsyncSttSessionRunner, runner)


def cast_recording_capture(capture: FakeCaptureForRecording) -> MicrophoneCapture:
    return cast(MicrophoneCapture, capture)
