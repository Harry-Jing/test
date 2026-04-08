"""Provide audio sinks used by capture-driven workflows."""

import wave
from datetime import datetime
from pathlib import Path

from .types import AudioChunk


class WaveFileAudioSink:
    """Write captured PCM16 audio chunks to a WAV file."""

    def __init__(
        self, path: Path, *, sample_rate: int, channels: int = 1, sample_width: int = 2
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._writer = wave.open(str(path), "wb")
        self._writer.setnchannels(channels)
        self._writer.setsampwidth(sample_width)
        self._writer.setframerate(sample_rate)

    def write(self, chunk: AudioChunk) -> None:
        """Append one audio chunk to the WAV file."""
        self._writer.writeframes(chunk.pcm16)

    def close(self) -> None:
        """Close the WAV writer and release the file handle."""
        self._writer.close()


def default_recording_path(recordings_dir: Path) -> Path:
    """Return a timestamped WAV path inside the recordings directory."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return recordings_dir / f"{timestamp}.wav"


__all__ = ["WaveFileAudioSink", "default_recording_path"]
