from vrc_live_caption.local_stt.funasr.protocol import encode_json_message


class FakeSessionWebsocket:
    def __init__(self, incoming: list[bytes | str]) -> None:
        self._incoming = list(incoming)
        self.sent: list[str] = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)

    async def send(self, message: str) -> None:
        self.sent.append(message)


class FakeBundle:
    def __init__(
        self,
        *,
        vad_results: list[tuple[int, int]],
        online_texts: list[str],
        offline_texts: list[str],
    ) -> None:
        self._vad_results = list(vad_results)
        self._online_texts = list(online_texts)
        self._offline_texts = list(offline_texts)

    def detect_speech_boundary(self, *, audio: bytes, state: dict) -> tuple[int, int]:
        return self._vad_results.pop(0) if self._vad_results else (-1, -1)

    def transcribe_online(self, *, audio: bytes, state: dict) -> str:
        return self._online_texts.pop(0) if self._online_texts else ""

    def transcribe_offline(
        self,
        *,
        audio: bytes,
        state: dict,
        punc_state: dict,
    ) -> str:
        return self._offline_texts.pop(0) if self._offline_texts else ""


def sixty_ms_packet() -> bytes:
    return b"\x01\x00" * 960


def encoded_start_message() -> str:
    from vrc_live_caption.local_stt.funasr.protocol import build_client_start_message

    return encode_json_message(
        build_client_start_message(sample_rate=16_000, channels=1)
    )
