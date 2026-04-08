import wave
from collections.abc import Iterator
from pathlib import Path


def iter_wav_chunks(path: Path, *, frames_per_chunk: int) -> Iterator[bytes]:
    with wave.open(str(path), "rb") as wav_file:
        while True:
            chunk = wav_file.readframes(frames_per_chunk)
            if not chunk:
                return
            yield chunk
