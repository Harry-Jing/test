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

