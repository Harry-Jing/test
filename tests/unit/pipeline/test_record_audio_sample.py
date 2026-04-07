import asyncio
import logging
from pathlib import Path

import pytest

from tests.unit.pipeline._support import (
    FakeCaptureForRecording,
    FakeQueue,
    FakeSink,
    cast_recording_capture,
)
from vrc_live_caption.errors import AudioRuntimeError
from vrc_live_caption.pipeline import record_audio_sample


@pytest.mark.asyncio
class TestRecordAudioSample:
    async def test_when_recording_times_out__then_it_closes_resources_cleanly(
        self,
        monkeypatch,
    ) -> None:
        FakeSink.instances.clear()
        queue = FakeQueue([asyncio.TimeoutError()])
        capture = FakeCaptureForRecording(queue)
        timestamps = iter([0.0, 0.0, 0.05, 0.15])

        monkeypatch.setattr("vrc_live_caption.pipeline.WaveFileAudioSink", FakeSink)
        await record_audio_sample(
            capture=cast_recording_capture(capture),
            output_path=Path("sample.wav"),
            duration_seconds=0.1,
            logger=logging.getLogger("test.pipeline.record.timeout"),
            now=lambda: next(timestamps),
        )

        sink = FakeSink.instances[0]
        assert capture.start_calls == 1
        assert capture.stop_calls == 1
        assert queue.closed is True
        assert sink.closed is True
        assert sink.writes == []

    async def test_when_health_check_fails__then_it_still_closes_resources(
        self,
        monkeypatch,
    ) -> None:
        FakeSink.instances.clear()
        queue = FakeQueue([asyncio.TimeoutError()])
        capture = FakeCaptureForRecording(
            queue,
            health_error=AudioRuntimeError("capture unhealthy"),
        )
        timestamps = iter([0.0, 0.0, 0.05])

        monkeypatch.setattr("vrc_live_caption.pipeline.WaveFileAudioSink", FakeSink)
        with pytest.raises(AudioRuntimeError, match="capture unhealthy"):
            await record_audio_sample(
                capture=cast_recording_capture(capture),
                output_path=Path("sample.wav"),
                duration_seconds=0.1,
                logger=logging.getLogger("test.pipeline.record.health"),
                now=lambda: next(timestamps),
            )

        sink = FakeSink.instances[0]
        assert capture.stop_calls == 1
        assert queue.closed is True
        assert sink.closed is True
