import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from tests.support.stt_fakes import FakeAttempt, FakeBackend, make_transcript_event
from vrc_live_caption.audio import AudioDeviceInfo
from vrc_live_caption.config import SttRetryConfig
from vrc_live_caption.errors import AudioRuntimeError, PipelineError, SttSessionError
from vrc_live_caption.pipeline import (
    ConsoleTranscriptOutput,
    LivePipelineController,
    record_audio_sample,
)
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue, MicrophoneCapture
from vrc_live_caption.stt import (
    AsyncSttSessionRunner,
    SttStatus,
    TranscriptRevisionEvent,
)
from vrc_live_caption.stt.types import SttStatusEvent


class _FakeCapture:
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


class _FakeTranscriptOutput:
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


class _StubSessionRunner:
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


class _FakeQueue:
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


class _FakeCaptureForRecording:
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


class _FakeSink:
    instances: list["_FakeSink"] = []

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


def _make_runner(backend: FakeBackend, audio_queue: DropOldestAsyncQueue[AudioChunk]):
    return AsyncSttSessionRunner(
        backend=backend,
        retry_config=SttRetryConfig(connect_timeout_seconds=0.1, max_attempts=1),
        audio_queue=audio_queue,
        event_buffer_max_items=16,
        logger=logging.getLogger("test.pipeline.runner"),
    )


def test_live_pipeline_controller_starts_drains_events_and_stops() -> None:
    async def scenario() -> None:
        emitted_lines: list[str] = []
        audio_queue = DropOldestAsyncQueue(
            max_items=4,
            logger=logging.getLogger("test.pipeline.audio"),
            label="audio queue",
        )
        runner = _make_runner(
            FakeBackend(
                attempt_factories=[
                    lambda context: FakeAttempt(
                        context=context,
                        events=[make_transcript_event(text="hello world")],
                        auto_stop=True,
                    )
                ]
            ),
            audio_queue,
        )
        capture = _FakeCapture(audio_queue)
        output = _FakeTranscriptOutput()
        controller = LivePipelineController(
            capture=capture,
            session_runner=runner,
            transcript_output=output,
            emit_line=emitted_lines.append,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline"),
        )

        await controller.start()
        assert capture.started is True
        assert output.started is True
        assert controller.backend_description == "fake backend"

        event = await runner.get_event(timeout=0.1)
        assert event is not None
        controller._emit_event(event)
        transcript_event = await runner.get_event(timeout=0.1)
        assert transcript_event is not None
        controller._emit_event(transcript_event)

        await controller.stop()

        assert capture.stopped is True
        assert output.shutdown_calls == 1
        assert output.events[0].text == "hello world"
        assert any("[status] ready: fake ready" == line for line in emitted_lines)

    asyncio.run(scenario())


def test_live_pipeline_controller_surfaces_runner_failures() -> None:
    async def scenario() -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.error.audio"),
            label="audio queue",
        )
        backend = FakeBackend(
            attempt_factories=[
                lambda context: FakeAttempt(
                    context=context,
                    run_error=SttSessionError("boom"),
                )
            ],
            retriable_errors=(),
        )
        runner = _make_runner(backend, audio_queue)
        capture = _FakeCapture(audio_queue)
        controller = LivePipelineController(
            capture=capture,
            session_runner=runner,
            transcript_output=_FakeTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.error"),
        )

        await controller.start()
        error_event = await runner.get_event(timeout=0.1)
        assert error_event is not None
        controller._emit_event(error_event)
        with pytest.raises(SttSessionError, match="boom"):
            controller.session_runner.check_health()
        await controller.stop()

    asyncio.run(scenario())


def test_live_pipeline_controller_propagates_interrupt_cancellation_during_shutdown() -> (
    None
):
    class _CancelledTranscriptOutput(_FakeTranscriptOutput):
        async def shutdown(self, *, timeout_seconds: float = 1.0) -> None:
            self.shutdown_calls += 1
            raise asyncio.CancelledError

    async def scenario() -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=4,
            logger=logging.getLogger("test.pipeline.cancel.audio"),
            label="audio queue",
        )
        runner = _make_runner(FakeBackend(), audio_queue)
        capture = _FakeCapture(audio_queue)
        controller = LivePipelineController(
            capture=capture,
            session_runner=runner,
            transcript_output=_CancelledTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.cancel"),
        )

        await controller.start()
        with pytest.raises(asyncio.CancelledError):
            await controller.stop()
        assert capture.stopped is True

    asyncio.run(scenario())


def test_console_transcript_output_emits_partial_and_final_lines() -> None:
    emitted_lines: list[str] = []
    output = ConsoleTranscriptOutput(emitted_lines.append)

    asyncio.run(output.start())
    output.handle_revision(make_transcript_event(text="", is_final=False))
    output.handle_revision(make_transcript_event(text="partial text", is_final=False))
    output.handle_revision(make_transcript_event(text="final text", is_final=True))
    asyncio.run(output.shutdown(timeout_seconds=0.1))

    assert emitted_lines == ["[partial] partial text", "[final] final text"]


def test_live_pipeline_controller_start_is_idempotent() -> None:
    async def scenario() -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.start.audio"),
            label="audio queue",
        )
        controller = LivePipelineController(
            capture=_FakeCapture(audio_queue),
            session_runner=cast(AsyncSttSessionRunner, _StubSessionRunner()),
            transcript_output=_FakeTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.start"),
        )

        await controller.start()
        await controller.start()

        assert cast(_FakeCapture, controller.capture).start_calls == 1
        assert cast(_StubSessionRunner, controller.session_runner).start_calls == 1

    asyncio.run(scenario())


def test_live_pipeline_controller_run_forever_auto_starts_and_logs_heartbeat(
    caplog,
) -> None:
    async def scenario() -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=2,
            logger=logging.getLogger("test.pipeline.heartbeat.audio"),
            label="audio queue",
        )
        capture = _FakeCapture(audio_queue)
        capture.fail_on_check = "stop after heartbeat"
        runner = _StubSessionRunner(event_dropped_items=3)
        timestamps = iter([0.0, 1.0, 1.0, 1.0])
        controller = LivePipelineController(
            capture=capture,
            session_runner=cast(AsyncSttSessionRunner, runner),
            transcript_output=_FakeTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=1.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.heartbeat"),
            now=lambda: next(timestamps),
        )

        with pytest.raises(AudioRuntimeError, match="stop after heartbeat"):
            await controller.run_forever(event_timeout=0.5)

        assert capture.start_calls == 1
        assert runner.start_calls == 1

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())

    assert "Pipeline heartbeat: device=#7 Fake Mic [default]" in caplog.text


def test_live_pipeline_controller_emits_status_variants_and_logs_levels(
    caplog,
) -> None:
    audio_queue = DropOldestAsyncQueue(
        max_items=1,
        logger=logging.getLogger("test.pipeline.emit.audio"),
        label="audio queue",
    )
    emitted_lines: list[str] = []
    controller = LivePipelineController(
        capture=_FakeCapture(audio_queue),
        session_runner=cast(AsyncSttSessionRunner, _StubSessionRunner()),
        transcript_output=_FakeTranscriptOutput(),
        emit_line=emitted_lines.append,
        heartbeat_seconds=60.0,
        shutdown_timeout_seconds=1.0,
        logger=logging.getLogger("test.pipeline.emit"),
    )

    with caplog.at_level(logging.INFO):
        controller._emit_event(make_transcript_event(text="hello"))
        controller._emit_event(SttStatusEvent(status=SttStatus.READY, message="ready"))
        controller._emit_event(SttStatusEvent(status=SttStatus.RETRYING, attempt=2))
        controller._emit_event(SttStatusEvent(status=SttStatus.ERROR, message="boom"))
        controller._log_heartbeat()

    assert (
        cast(_FakeTranscriptOutput, controller.transcript_output).events[0].text
        == "hello"
    )
    assert emitted_lines == [
        "[status] ready: ready",
        "[status] retrying attempt=2",
        "[status] error: boom",
    ]
    assert "STT status=ready: ready" in caplog.text
    assert "STT status=retrying attempt=2" in caplog.text
    assert "STT status=error: boom" in caplog.text
    assert "Pipeline heartbeat: device=#7 Fake Mic [default]" in caplog.text


def test_live_pipeline_controller_log_heartbeat_uses_unresolved_label(caplog) -> None:
    audio_queue = DropOldestAsyncQueue(
        max_items=1,
        logger=logging.getLogger("test.pipeline.unresolved.audio"),
        label="audio queue",
    )
    capture = _FakeCapture(audio_queue)
    capture.resolved_device = None
    controller = LivePipelineController(
        capture=capture,
        session_runner=cast(
            AsyncSttSessionRunner,
            _StubSessionRunner(event_dropped_items=1),
        ),
        transcript_output=_FakeTranscriptOutput(),
        emit_line=lambda _line: None,
        heartbeat_seconds=60.0,
        shutdown_timeout_seconds=1.0,
        logger=logging.getLogger("test.pipeline.unresolved"),
    )

    with caplog.at_level(logging.INFO):
        controller._log_heartbeat()

    assert "Pipeline heartbeat: device=unresolved" in caplog.text


def test_live_pipeline_controller_stop_wraps_first_non_vrc_failure() -> None:
    async def scenario() -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.stop_wrap.audio"),
            label="audio queue",
        )
        capture = _FakeCapture(audio_queue)
        capture.stop_error = RuntimeError("capture stop failed")
        runner = _StubSessionRunner(close_error=RuntimeError("runner close failed"))
        output = _FakeTranscriptOutput()
        output.shutdown_error = RuntimeError("output flush failed")
        controller = LivePipelineController(
            capture=capture,
            session_runner=cast(AsyncSttSessionRunner, runner),
            transcript_output=output,
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.stop_wrap"),
        )
        controller._started = True

        with pytest.raises(PipelineError, match="capture stop failed"):
            await controller.stop()

        assert capture.stop_calls == 1
        assert runner.close_calls == 1
        assert output.shutdown_calls == 1

    asyncio.run(scenario())


def test_live_pipeline_controller_stop_preserves_vrc_errors() -> None:
    async def scenario() -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.stop_vrc.audio"),
            label="audio queue",
        )
        controller = LivePipelineController(
            capture=_FakeCapture(audio_queue),
            session_runner=cast(
                AsyncSttSessionRunner,
                _StubSessionRunner(
                    close_error=AudioRuntimeError("runner close failed")
                ),
            ),
            transcript_output=_FakeTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.stop_vrc"),
        )
        controller._started = True

        with pytest.raises(AudioRuntimeError, match="runner close failed"):
            await controller.stop()

    asyncio.run(scenario())


def test_live_pipeline_controller_drain_events_empties_queue() -> None:
    async def scenario() -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.drain.audio"),
            label="audio queue",
        )
        output = _FakeTranscriptOutput()
        emitted_lines: list[str] = []
        controller = LivePipelineController(
            capture=_FakeCapture(audio_queue),
            session_runner=cast(
                AsyncSttSessionRunner,
                _StubSessionRunner(
                    events=[
                        make_status_event(status=SttStatus.READY, message="ready"),
                        make_transcript_event(text="hello"),
                        None,
                    ]
                ),
            ),
            transcript_output=output,
            emit_line=emitted_lines.append,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.drain"),
        )

        await controller._drain_events(timeout=0.0)

        assert emitted_lines == ["[status] ready: ready"]
        assert output.events[0].text == "hello"

    from tests.support.stt_fakes import make_status_event

    asyncio.run(scenario())


def test_record_audio_sample_times_out_cleanly_and_closes_resources(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        _FakeSink.instances.clear()
        queue = _FakeQueue([asyncio.TimeoutError()])
        capture = _FakeCaptureForRecording(queue)
        timestamps = iter([0.0, 0.0, 0.05, 0.15])

        monkeypatch.setattr("vrc_live_caption.pipeline.WaveFileAudioSink", _FakeSink)
        await record_audio_sample(
            capture=cast(MicrophoneCapture, capture),
            output_path=Path("sample.wav"),
            duration_seconds=0.1,
            logger=logging.getLogger("test.pipeline.record.timeout"),
            now=lambda: next(timestamps),
        )

        sink = _FakeSink.instances[0]
        assert capture.start_calls == 1
        assert capture.stop_calls == 1
        assert queue.closed is True
        assert sink.closed is True
        assert sink.writes == []

    asyncio.run(scenario())


def test_record_audio_sample_still_closes_resources_when_health_check_fails(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        _FakeSink.instances.clear()
        queue = _FakeQueue([asyncio.TimeoutError()])
        capture = _FakeCaptureForRecording(
            queue,
            health_error=AudioRuntimeError("capture unhealthy"),
        )
        timestamps = iter([0.0, 0.0, 0.05])

        monkeypatch.setattr("vrc_live_caption.pipeline.WaveFileAudioSink", _FakeSink)
        with pytest.raises(AudioRuntimeError, match="capture unhealthy"):
            await record_audio_sample(
                capture=cast(MicrophoneCapture, capture),
                output_path=Path("sample.wav"),
                duration_seconds=0.1,
                logger=logging.getLogger("test.pipeline.record.health"),
                now=lambda: next(timestamps),
            )

        sink = _FakeSink.instances[0]
        assert capture.stop_calls == 1
        assert queue.closed is True
        assert sink.closed is True

    asyncio.run(scenario())
