import asyncio
import logging

import pytest

from tests.support.fakes.stt import FakeAttempt, FakeSttBackend
from tests.unit.pipeline._support import (
    FakeCapture,
    FakeTranscriptOutput,
    StubSessionRunner,
    cast_stub_runner,
    make_runner,
)
from vrc_live_caption.errors import AudioRuntimeError, PipelineError, SttSessionError
from vrc_live_caption.pipeline import LivePipelineController
from vrc_live_caption.runtime import DropOldestAsyncQueue


@pytest.mark.asyncio
class TestLivePipelineControllerShutdown:
    async def test_when_runner_failure_is_detected__then_it_surfaces_the_stt_error(
        self,
    ) -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.error.audio"),
            label="audio queue",
        )
        backend = FakeSttBackend(
            attempt_factories=[
                lambda context: FakeAttempt(
                    context=context,
                    run_error=SttSessionError("boom"),
                )
            ],
            retriable_errors=(),
        )
        runner = make_runner(backend, audio_queue)
        controller = LivePipelineController(
            capture=FakeCapture(audio_queue),
            session_runner=runner,
            transcript_output=FakeTranscriptOutput(),
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

    async def test_when_transcript_output_cancellation_happens_during_shutdown__then_stop_propagates_cancelled_error(
        self,
    ) -> None:
        class CancelledTranscriptOutput(FakeTranscriptOutput):
            async def shutdown(self, *, timeout_seconds: float = 1.0) -> None:
                self.shutdown_calls += 1
                raise asyncio.CancelledError

        audio_queue = DropOldestAsyncQueue(
            max_items=4,
            logger=logging.getLogger("test.pipeline.cancel.audio"),
            label="audio queue",
        )
        runner = make_runner(FakeSttBackend(), audio_queue)
        capture = FakeCapture(audio_queue)
        controller = LivePipelineController(
            capture=capture,
            session_runner=runner,
            transcript_output=CancelledTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.cancel"),
        )

        await controller.start()
        with pytest.raises(asyncio.CancelledError):
            await controller.stop()
        assert capture.stopped is True

    async def test_when_multiple_non_vrc_shutdown_failures_happen__then_stop_wraps_the_first_one(
        self,
    ) -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.stop_wrap.audio"),
            label="audio queue",
        )
        capture = FakeCapture(audio_queue)
        capture.stop_error = RuntimeError("capture stop failed")
        runner = StubSessionRunner(close_error=RuntimeError("runner close failed"))
        output = FakeTranscriptOutput()
        output.shutdown_error = RuntimeError("output flush failed")
        controller = LivePipelineController(
            capture=capture,
            session_runner=cast_stub_runner(runner),
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

    async def test_when_shutdown_failure_is_already_a_vrc_error__then_stop_preserves_it(
        self,
    ) -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.stop_vrc.audio"),
            label="audio queue",
        )
        controller = LivePipelineController(
            capture=FakeCapture(audio_queue),
            session_runner=cast_stub_runner(
                StubSessionRunner(close_error=AudioRuntimeError("runner close failed"))
            ),
            transcript_output=FakeTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.stop_vrc"),
        )
        controller._started = True

        with pytest.raises(AudioRuntimeError, match="runner close failed"):
            await controller.stop()
