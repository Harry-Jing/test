"""Provide PCM16 resampling helpers for provider-specific STT input formats."""

from array import array
from math import gcd


class LinearPcm16Resampler:
    """Stateful mono PCM16 resampler with exact rational phase tracking."""

    def __init__(self, *, source_rate: int, target_rate: int) -> None:
        """Initialize a resampler between two mono PCM16 sample rates.

        Args:
            source_rate: Input sample rate of incoming PCM16 chunks.
            target_rate: Output sample rate required by the provider.

        Raises:
            ValueError: Raised when either sample rate is less than 1.
        """
        if source_rate < 1 or target_rate < 1:
            raise ValueError("sample rates must be >= 1")
        self._passthrough = source_rate == target_rate

        factor = gcd(source_rate, target_rate)
        self._step_num = source_rate // factor
        self._step_den = target_rate // factor
        self._position_num = 0
        self._tail_sample: int | None = None

    def convert(self, pcm16: bytes) -> bytes:
        """Convert one PCM16 chunk while preserving internal interpolation state."""
        if self._passthrough:
            return pcm16
        return self._resample(_decode_pcm16(pcm16))

    def flush(self) -> bytes:
        """Emit buffered tail audio and reset internal resampling state."""
        if self._passthrough:
            return b""
        if self._tail_sample is None:
            return b""
        flushed = self._resample([self._tail_sample])
        self._tail_sample = None
        self._position_num = 0
        return flushed

    def _resample(self, new_samples: list[int]) -> bytes:
        if self._tail_sample is None:
            buffer = list(new_samples)
        else:
            buffer = [self._tail_sample, *new_samples]

        if not buffer:
            return b""

        output = array("h")
        while self._position_num + self._step_den < len(buffer) * self._step_den:
            index = self._position_num // self._step_den
            next_index = index + 1
            if next_index >= len(buffer):
                break

            fraction_num = self._position_num % self._step_den
            left = buffer[index]
            right = buffer[next_index]
            interpolated = (
                ((self._step_den - fraction_num) * left) + (fraction_num * right)
            ) / self._step_den
            output.append(int(round(interpolated)))
            self._position_num += self._step_num

        self._tail_sample = buffer[-1]
        self._position_num -= (len(buffer) - 1) * self._step_den
        return output.tobytes()


def _decode_pcm16(pcm16: bytes) -> list[int]:
    if not pcm16:
        return []
    samples = array("h")
    samples.frombytes(pcm16)
    return samples.tolist()
