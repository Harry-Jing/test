import logging

import pytest

from tests.support.fakes.stt import make_status_event, make_transcript_event
from tests.unit.pipeline._support import (
    FakeCapture,
    FakeTranscriptOutput,
    StubSessionRunner,
    cast_stub_runner,
)
from vrc_live_caption.pipeline import ConsoleTranscriptOutput, LivePipelineController
from vrc_live_caption.runtime import DropOldestAsyncQueue
from vrc_live_caption.stt import SttStatus


@pytest.mark.asyncio
class TestConsoleTranscriptOutput:
    async def test_when_partial_and_final_revisions_arrive__then_it_emits_console_lines(
        self,
    ) -> None:
        emitted_lines: list[str] = []
        output = ConsoleTranscriptOutput(emitted_lines.append)

        await output.start()
        output.handle_revision(make_transcript_event(text="", is_final=False))
        output.handle_revision(
            make_transcript_event(text="partial text", is_final=False)
        )
        output.handle_revision(make_transcript_event(text="final text", is_final=True))
        await output.shutdown(timeout_seconds=0.1)

        assert emitted_lines == ["[partial] partial text", "[final] final text"]


class TestLivePipelineControllerEvents:
    def test_when_status_and_transcript_events_are_emitted__then_it_logs_and_dispatches_them(
        self,
        caplog,
    ) -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.emit.audio"),
            label="audio queue",
        )
        emitted_lines: list[str] = []
        output = FakeTranscriptOutput()
        controller = LivePipelineController(
            capture=FakeCapture(audio_queue),
            session_runner=cast_stub_runner(StubSessionRunner()),
            transcript_output=output,
            emit_line=emitted_lines.append,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.emit"),
        )

        with caplog.at_level(logging.INFO):
            controller._emit_event(make_transcript_event(text="hello"))
            controller._emit_event(
                make_status_event(status=SttStatus.READY, message="ready")
            )
            controller._emit_event(
                make_status_event(status=SttStatus.RETRYING, attempt=2)
            )
            controller._emit_event(
                make_status_event(status=SttStatus.ERROR, message="boom")
            )
            controller._log_heartbeat()

        assert output.events[0].text == "hello"
        assert emitted_lines == [
            "[status] ready: ready",
            "[status] retrying attempt=2",
            "[status] error: boom",
        ]
        assert "STT status=ready: ready" in caplog.text
        assert "STT status=retrying attempt=2" in caplog.text
        assert "STT status=error: boom" in caplog.text
        assert "Pipeline heartbeat: device=#7 Fake Mic [default]" in caplog.text

    def test_when_resolved_device_is_missing__then_heartbeat_uses_unresolved_label(
        self,
        caplog,
    ) -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.unresolved.audio"),
            label="audio queue",
        )
        capture = FakeCapture(audio_queue)
        capture.resolved_device = None
        controller = LivePipelineController(
            capture=capture,
            session_runner=cast_stub_runner(StubSessionRunner(event_dropped_items=1)),
            transcript_output=FakeTranscriptOutput(),
            emit_line=lambda _line: None,
            heartbeat_seconds=60.0,
            shutdown_timeout_seconds=1.0,
            logger=logging.getLogger("test.pipeline.unresolved"),
        )

        with caplog.at_level(logging.INFO):
            controller._log_heartbeat()

        assert "Pipeline heartbeat: device=unresolved" in caplog.text


@pytest.mark.asyncio
class TestLivePipelineControllerDrainEvents:
    async def test_when_drain_events_is_called__then_it_empties_the_runner_queue(
        self,
    ) -> None:
        audio_queue = DropOldestAsyncQueue(
            max_items=1,
            logger=logging.getLogger("test.pipeline.drain.audio"),
            label="audio queue",
        )
        output = FakeTranscriptOutput()
        emitted_lines: list[str] = []
        controller = LivePipelineController(
            capture=FakeCapture(audio_queue),
            session_runner=cast_stub_runner(
                StubSessionRunner(
                    events=[
                        make_status_event(status=SttStatus.READY, message="ready"),
                        make_transcript_event(text="hello"),
                        None,
                    ]
                )
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
