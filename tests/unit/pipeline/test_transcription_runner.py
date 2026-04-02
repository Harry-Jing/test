import asyncio
import logging

import pytest

from tests.support.stt_fakes import FakeAttempt, FakeBackend, make_transcript_event
from vrc_live_caption.audio import AudioDeviceInfo
from vrc_live_caption.config import SttRetryConfig
from vrc_live_caption.errors import AudioRuntimeError, SttSessionError
from vrc_live_caption.pipeline import LivePipelineController
from vrc_live_caption.runtime import AudioChunk, DropOldestAsyncQueue
from vrc_live_caption.stt import AsyncSttSessionRunner, TranscriptRevisionEvent


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

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def check_health(self) -> None:
        if self.fail_on_check:
            raise AudioRuntimeError(self.fail_on_check)


class _FakeTranscriptOutput:
    def __init__(self) -> None:
        self.started = False
        self.shutdown_calls = 0
        self.events: list[TranscriptRevisionEvent] = []

    async def start(self) -> None:
        self.started = True

    def handle_revision(self, event: TranscriptRevisionEvent) -> None:
        self.events.append(event)

    async def shutdown(self, *, timeout_seconds: float = 1.0) -> None:
        self.shutdown_calls += 1


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
