import asyncio
import logging

import pytest

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
        resolved_device="cuda:0",
        device_policy="auto",
        logger=logging.getLogger("test.local_stt.session.ready"),
    )

    asyncio.run(session.run())

    sent = [decode_json_message(message) for message in websocket.sent]

    assert sent[0] == {
        "type": "ready",
        "message": "FunASR local 2-pass sidecar ready",
        "resolved_device": "cuda:0",
        "device_policy": "auto",
    }
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
        logger=logging.getLogger("test.local_stt.session.stop"),
    )

    asyncio.run(session.run())

    sent = [decode_json_message(message) for message in websocket.sent]

    assert [item["type"] for item in sent] == ["ready", "transcript", "transcript"]
    assert sent[-1]["phase"] == "offline"
    assert sent[-1]["is_final"] is True


def test_websocket_session_rejects_invalid_json_control_messages() -> None:
    websocket = _FakeWebsocket(["{not-json"])
    session = FunasrWebsocketSession(
        websocket=websocket,
        config=FunasrLocalServiceConfig(),
        models=_FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
        executor=None,
        logger=logging.getLogger("test.local_stt.session.invalid_json"),
    )

    asyncio.run(session.run())

    sent = [decode_json_message(message) for message in websocket.sent]

    assert sent == [
        {
            "type": "error",
            "message": "Invalid JSON control message: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
            "fatal": True,
        }
    ]


def test_websocket_session_rejects_unsupported_control_messages() -> None:
    websocket = _FakeWebsocket(
        [encode_json_message({"type": "ping", "message": "hello"})]
    )
    session = FunasrWebsocketSession(
        websocket=websocket,
        config=FunasrLocalServiceConfig(),
        models=_FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
        executor=None,
        logger=logging.getLogger("test.local_stt.session.unsupported"),
    )

    asyncio.run(session.run())

    sent = [decode_json_message(message) for message in websocket.sent]

    assert sent == [
        {
            "type": "error",
            "message": "Unsupported local STT control message type: ping",
            "fatal": True,
        }
    ]


def test_websocket_session_rejects_duplicate_start_messages() -> None:
    websocket = _FakeWebsocket(
        [
            encode_json_message(
                build_client_start_message(sample_rate=16_000, channels=1)
            ),
            encode_json_message(
                build_client_start_message(sample_rate=16_000, channels=1)
            ),
        ]
    )
    session = FunasrWebsocketSession(
        websocket=websocket,
        config=FunasrLocalServiceConfig(),
        models=_FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
        executor=None,
        logger=logging.getLogger("test.local_stt.session.duplicate_start"),
    )

    asyncio.run(session.run())

    sent = [decode_json_message(message) for message in websocket.sent]

    assert sent[0]["type"] == "ready"
    assert sent[1] == {
        "type": "error",
        "message": "FunASR local session already started",
        "fatal": True,
    }


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            build_client_start_message(
                sample_rate=16_000,
                channels=1,
                mode="1pass",
            ),
            "Unsupported local STT mode: 1pass",
        ),
        (
            build_client_start_message(
                sample_rate=16_000,
                channels=1,
                sample_format="float32",
            ),
            "Unsupported local STT sample format: float32",
        ),
        (
            build_client_start_message(sample_rate=8_000, channels=1),
            "FunASR local sidecar currently requires sample_rate = 16000",
        ),
        (
            build_client_start_message(sample_rate=16_000, channels=2),
            "FunASR local sidecar currently requires channels = 1",
        ),
    ],
)
def test_websocket_session_rejects_invalid_start_parameters(
    message: dict[str, object],
    expected: str,
) -> None:
    websocket = _FakeWebsocket([encode_json_message(message)])
    session = FunasrWebsocketSession(
        websocket=websocket,
        config=FunasrLocalServiceConfig(),
        models=_FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
        executor=None,
        logger=logging.getLogger("test.local_stt.session.invalid_start"),
    )

    asyncio.run(session.run())

    assert [decode_json_message(item) for item in websocket.sent] == [
        {
            "type": "error",
            "message": expected,
            "fatal": True,
        }
    ]


def test_websocket_session_rejects_audio_before_start() -> None:
    websocket = _FakeWebsocket([_sixty_ms_packet()])
    session = FunasrWebsocketSession(
        websocket=websocket,
        config=FunasrLocalServiceConfig(),
        models=_FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
        executor=None,
        logger=logging.getLogger("test.local_stt.session.audio_before_start"),
    )

    asyncio.run(session.run())

    sent = [decode_json_message(message) for message in websocket.sent]
    expected = {
        "type": "error",
        "message": "Audio received before the local STT session was started",
        "fatal": True,
    }

    assert sent == [expected, expected]


def test_websocket_session_emit_online_partial_skips_empty_and_duplicate_text() -> None:
    async def scenario() -> None:
        empty_websocket = _FakeWebsocket([])
        empty_session = FunasrWebsocketSession(
            websocket=empty_websocket,
            config=FunasrLocalServiceConfig(),
            models=_FakeBundle(vad_results=[], online_texts=[""], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.online_empty"),
        )
        empty_session._state.online_packets = [_sixty_ms_packet()]

        await empty_session._emit_online_partial()

        assert empty_websocket.sent == []
        assert empty_session._state.online_packets == []

        duplicate_websocket = _FakeWebsocket([])
        duplicate_session = FunasrWebsocketSession(
            websocket=duplicate_websocket,
            config=FunasrLocalServiceConfig(),
            models=_FakeBundle(vad_results=[], online_texts=["same"], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.online_duplicate"),
        )
        duplicate_session._state.online_packets = [_sixty_ms_packet()]
        duplicate_session._state.last_online_text = "same"

        await duplicate_session._emit_online_partial()

        assert duplicate_websocket.sent == []
        assert duplicate_session._state.online_packets == []

    asyncio.run(scenario())


def test_websocket_session_emit_offline_final_force_without_packets_resets_state() -> None:
    async def scenario() -> None:
        websocket = _FakeWebsocket([])
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(),
            models=_FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.force_reset"),
        )
        session._state.current_segment_id = 3
        session._state.online_packets = [_sixty_ms_packet()]
        session._state.offline_packets = []
        session._state.last_online_text = "partial"
        session._state.speech_active = True
        session._state.online_state = {"cache": {"existing": True}, "is_final": True}

        await session._emit_offline_final(force=True)

        assert websocket.sent == []
        assert session._state.current_segment_id is None
        assert session._state.online_packets == []
        assert session._state.offline_packets == []
        assert session._state.last_online_text == ""
        assert session._state.speech_active is False
        assert session._state.online_state["cache"] == {}
        assert session._state.online_state["is_final"] is False

    asyncio.run(scenario())
