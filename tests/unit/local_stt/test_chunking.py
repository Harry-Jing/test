import pytest

from vrc_live_caption.local_stt.funasr.chunking import (
    StreamingPacketChunker,
    pcm_duration_ms,
)


def test_streaming_packet_chunker_splits_complete_packets_and_flushes_tail() -> None:
    chunker = StreamingPacketChunker(
        sample_rate=16_000,
        channels=1,
        packet_duration_ms=60,
    )
    packet = b"\x01\x00" * 960
    first = chunker.append(packet + packet + b"\x02\x00" * 100)
    tail = chunker.flush()

    assert first == [packet, packet]
    assert tail == [b"\x02\x00" * 100]


def test_pcm_duration_ms_estimates_audio_length_for_pcm16() -> None:
    sixty_ms = b"\x01\x00" * 960

    assert pcm_duration_ms(sixty_ms, sample_rate=16_000) == 60


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sample_rate": 0}, "sample_rate must be >= 1"),
        ({"channels": 0}, "channels must be >= 1"),
        ({"packet_duration_ms": 0}, "packet_duration_ms must be >= 1"),
        ({"sample_width_bytes": 0}, "sample_width_bytes must be >= 1"),
    ],
)
def test_streaming_packet_chunker_rejects_invalid_config(
    kwargs: dict[str, int],
    message: str,
) -> None:
    base_kwargs = {
        "sample_rate": 16_000,
        "channels": 1,
        "packet_duration_ms": 60,
        "sample_width_bytes": 2,
    }
    base_kwargs.update(kwargs)

    with pytest.raises(ValueError, match=message):
        StreamingPacketChunker(**base_kwargs)


def test_streaming_packet_chunker_rejects_too_small_packet_size() -> None:
    with pytest.raises(
        ValueError,
        match="packet size is too small for the configured format",
    ):
        StreamingPacketChunker(
            sample_rate=1,
            channels=1,
            packet_duration_ms=1,
            sample_width_bytes=2,
        )


def test_streaming_packet_chunker_buffers_partial_packet_until_flush() -> None:
    chunker = StreamingPacketChunker(
        sample_rate=16_000,
        channels=1,
        packet_duration_ms=60,
    )
    partial = b"\x01\x00" * 100

    assert chunker.append(partial) == []
    assert chunker.flush() == [partial]


def test_streaming_packet_chunker_flush_discards_unaligned_tail_bytes() -> None:
    chunker = StreamingPacketChunker(
        sample_rate=16_000,
        channels=1,
        packet_duration_ms=60,
    )

    assert chunker.append(b"\x01\x00\x02") == []
    assert chunker.flush() == [b"\x01\x00"]
    assert chunker.flush() == []


def test_pcm_duration_ms_handles_empty_audio_and_invalid_bytes_per_ms() -> None:
    assert pcm_duration_ms(b"", sample_rate=16_000) == 0
    assert pcm_duration_ms(b"\x01\x02", sample_rate=16_000, sample_width_bytes=0) == 0


def test_pcm_duration_ms_supports_custom_channels_and_sample_width() -> None:
    twenty_ms = b"\x01\x00" * 320

    assert pcm_duration_ms(twenty_ms, sample_rate=8_000, channels=2) == 20
