"""Define shared runtime types for audio capture and buffering."""

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class AudioChunk:
    """Store one captured PCM16 chunk and its capture metadata."""

    sequence: int
    pcm16: bytes
    frame_count: int
    captured_at_monotonic: float


__all__ = ["AudioChunk"]
