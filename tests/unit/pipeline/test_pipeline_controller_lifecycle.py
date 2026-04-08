import logging

import pytest

from tests.support.fakes.stt import FakeAttempt, FakeSttBackend, make_transcript_event
from tests.unit.pipeline._support import (
    FakeCapture,
    FakeTranscriptOutput,
    StubSessionRunner,
    cast_stub_runner,
    make_runner,
)
from vrc_live_caption.errors import AudioRuntimeError
from vrc_live_caption.pipeline import LivePipelineController
from vrc_live_caption.runtime import DropOldestAsyncQueue


@pytest.mark.asyncio
class TestLivePipelineControllerLifecycle:
    async def test_when_controller_starts_drains_events_and_stops__then_it_coordinates_capture_runner_and_output(
        self,
    ) -> None:
        emitted_lines: list[str] = []
        audio_queue = DropOldestAsyncQueue(
            max_items=4,
            logger=logging.getLogger("test.pipeline.audio"),
            label="audio queue",
        )
        runner = make_runner(
            FakeSttBackend(
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
        capture = FakeCapture(audio_queue)
        output = FakeTranscriptOutput()
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

    async def test_when_start_is_called_twice__then_it_only_starts_dependencies_once(
        self,
    ) -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.start.audio"),
            label="audio queue",
        )
        capture = FakeCapture(audio_queue)
        runner = StubSessionRunner()
        controller = LivePipelineController(
            capture=capture,
            session_runner=cast_stub_runner(runner),
            transcript_output=FakeTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.start"),
        )

        await controller.start()
        await controller.start()

        assert capture.start_calls == 1
        assert runner.start_calls == 1

    async def test_when_run_forever_logs_heartbeat_and_capture_fails_health_check__then_it_surfaces_audio_runtime_error(
        self,
        caplog,
    ) -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=2,
            logger=logging.getLogger("test.pipeline.heartbeat.audio"),
            label="audio queue",
        )
        capture = FakeCapture(audio_queue)
        capture.fail_on_check = "stop after heartbeat"
        runner = StubSessionRunner(event_dropped_items=3)
        timestamps = iter([0.0, 1.0, 1.0, 1.0])
        controller = LivePipelineController(
            capture=capture,
            session_runner=cast_stub_runner(runner),
            transcript_output=FakeTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=1.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.heartbeat"),
            now=lambda: next(timestamps),
        )

        with caplog.at_level(logging.INFO):
            with pytest.raises(AudioRuntimeError, match="stop after heartbeat"):
                await controller.run_forever(event_timeout=0.5)

        assert capture.start_calls == 1
        assert runner.start_calls == 1
        assert "Pipeline heartbeat: device=#7 Fake Mic [default]" in caplog.text
