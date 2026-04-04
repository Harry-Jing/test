import asyncio

from vrc_live_caption.local_stt.funasr.config import FunasrLocalServiceConfig
from vrc_live_caption.local_stt.funasr.protocol import (
    build_client_start_message,
    build_client_stop_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.local_stt.funasr.session import FunasrWebsocketSession


class _FakeWebsocket:
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


class _FakeBundle:
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


def _sixty_ms_packet() -> bytes:
    return b"\x01\x00" * 960


def test_websocket_session_emits_ready_online_and_offline_messages() -> None:
    websocket = _FakeWebsocket(
        [
            encode_json_message(
                build_client_start_message(sample_rate=16_000, channels=1)
            ),
            _sixty_ms_packet(),
            _sixty_ms_packet(),
        ]
    )
    session = FunasrWebsocketSession(
        websocket=websocket,
        config=FunasrLocalServiceConfig(chunk_size=(0, 1, 0), chunk_interval=1),
        models=_FakeBundle(
            vad_results=[(10, -1), (-1, 120)],
            online_texts=["hello", "hello"],
            offline_texts=["hello world"],
        ),
        executor=None,
        logger=None,
    )

    asyncio.run(session.run())

    sent = [decode_json_message(message) for message in websocket.sent]

    assert sent[0]["type"] == "ready"
    assert sent[1] == {
        "type": "transcript",
        "phase": "online",
        "segment_id": 1,
        "text": "hello",
        "is_final": False,
    }
    assert sent[2] == {
        "type": "transcript",
        "phase": "offline",
        "segment_id": 1,
        "text": "hello world",
        "is_final": True,
    }


def test_websocket_session_flushes_pending_final_on_stop() -> None:
    websocket = _FakeWebsocket(
        [
            encode_json_message(
                build_client_start_message(sample_rate=16_000, channels=1)
            ),
            _sixty_ms_packet(),
            encode_json_message(build_client_stop_message()),
        ]
    )
    session = FunasrWebsocketSession(
        websocket=websocket,
        config=FunasrLocalServiceConfig(chunk_size=(0, 1, 0), chunk_interval=1),
        models=_FakeBundle(
            vad_results=[(0, -1)],
            online_texts=["partial"],
            offline_texts=["final"],
        ),
        executor=None,
        logger=None,
    )

    asyncio.run(session.run())

    sent = [decode_json_message(message) for message in websocket.sent]

    assert [item["type"] for item in sent] == ["ready", "transcript", "transcript"]
    assert sent[-1]["phase"] == "offline"
    assert sent[-1]["is_final"] is True
