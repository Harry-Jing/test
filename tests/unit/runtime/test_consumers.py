import wave
from datetime import datetime
from pathlib import Path

from vrc_live_caption.runtime import (
    AudioChunk,
    WaveFileAudioSink,
    default_recording_path,
)


def test_wave_file_audio_sink_writes_valid_wav(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "sample.wav"
    sink = WaveFileAudioSink(path, sample_rate=16_000)
    sink.write(
        AudioChunk(
            sequence=1,
            pcm16=b"\x01\x00\x02\x00",
            frame_count=2,
            captured_at_monotonic=1.0,
        )
    )
    sink.write(
        AudioChunk(
            sequence=2,
            pcm16=b"\x03\x00\x04\x00",
            frame_count=2,
            captured_at_monotonic=2.0,
        )
    )
    sink.close()

    assert path.exists()
    with wave.open(str(path), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 16_000
        assert (
            reader.readframes(reader.getnframes())
            == b"\x01\x00\x02\x00\x03\x00\x04\x00"
        )


def test_default_recording_path_uses_timestamped_filename(monkeypatch) -> None:
    class _FakeDateTime:
        @classmethod
        def now(cls) -> datetime:
            return datetime(2026, 3, 30, 17, 35, 7)

    monkeypatch.setattr("vrc_live_caption.runtime.consumers.datetime", _FakeDateTime)

    path = default_recording_path(Path("recordings"))

    assert path == Path("recordings/20260330-173507.wav")
