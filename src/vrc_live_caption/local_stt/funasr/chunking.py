"""Helpers for PCM16 streaming chunk splitting in the local FunASR sidecar."""


class StreamingPacketChunker:
    """Split PCM16 audio into fixed-duration packets for streaming inference."""

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        packet_duration_ms: int,
        sample_width_bytes: int = 2,
    ) -> None:
        if sample_rate < 1:
            raise ValueError("sample_rate must be >= 1")
        if channels < 1:
            raise ValueError("channels must be >= 1")
        if packet_duration_ms < 1:
            raise ValueError("packet_duration_ms must be >= 1")
        if sample_width_bytes < 1:
            raise ValueError("sample_width_bytes must be >= 1")
        self._sample_width_bytes = sample_width_bytes
        self._packet_bytes = (
            sample_rate * channels * packet_duration_ms * sample_width_bytes
        ) // 1000
        if self._packet_bytes < sample_width_bytes:
            raise ValueError("packet size is too small for the configured format")
        self._buffer = bytearray()

    @property
    def packet_bytes(self) -> int:
        """Return the packet size in bytes."""
        return self._packet_bytes

    def append(self, audio: bytes) -> list[bytes]:
        """Append PCM audio and return complete packets."""
        self._buffer.extend(audio)
        return self._drain(final=False)

    def flush(self) -> list[bytes]:
        """Return the final packets, including trailing aligned audio."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[bytes]:
        packets: list[bytes] = []
        while len(self._buffer) >= self._packet_bytes:
            packets.append(bytes(self._buffer[: self._packet_bytes]))
            del self._buffer[: self._packet_bytes]
        if final and self._buffer:
            aligned_length = len(self._buffer) - (
                len(self._buffer) % self._sample_width_bytes
            )
            if aligned_length > 0:
                packets.append(bytes(self._buffer[:aligned_length]))
            self._buffer.clear()
        return packets


def pcm_duration_ms(
    audio: bytes,
    *,
    sample_rate: int,
    channels: int = 1,
    sample_width_bytes: int = 2,
) -> int:
    """Return the approximate duration of one PCM buffer in milliseconds."""
    if not audio:
        return 0
    bytes_per_ms = (sample_rate * channels * sample_width_bytes) / 1000.0
    if bytes_per_ms <= 0:
        return 0
    return int(round(len(audio) / bytes_per_ms))
