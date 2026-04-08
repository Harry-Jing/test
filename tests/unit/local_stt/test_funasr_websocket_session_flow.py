import logging

import pytest

from tests.unit.local_stt._session_support import (
    FakeBundle,
    FakeSessionWebsocket,
    encoded_start_message,
    sixty_ms_packet,
)
from vrc_live_caption.local_stt.funasr.config import FunasrLocalServiceConfig
from vrc_live_caption.local_stt.funasr.protocol import (
    build_client_stop_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.local_stt.funasr.session import FunasrWebsocketSession


@pytest.mark.asyncio
class TestFunasrWebsocketSessionFlow:
    async def test_when_session_runs_with_packets__then_it_emits_ready_online_and_offline_messages(
        self,
    ) -> None:
        websocket = FakeSessionWebsocket(
            [
                encoded_start_message(),
                sixty_ms_packet(),
                sixty_ms_packet(),
            ]
        )
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(chunk_size=(0, 1, 0), chunk_interval=1),
            models=FakeBundle(
                vad_results=[(10, -1), (-1, 120)],
                online_texts=["hello", "hello"],
                offline_texts=["hello world"],
            ),
            executor=None,
            resolved_device="cuda:0",
            device_policy="auto",
            logger=logging.getLogger("test.local_stt.session.ready"),
        )

        await session.run()

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

    async def test_when_stop_message_arrives_with_pending_audio__then_it_flushes_the_final_transcript(
        self,
    ) -> None:
        websocket = FakeSessionWebsocket(
            [
                encoded_start_message(),
                sixty_ms_packet(),
                encode_json_message(build_client_stop_message()),
            ]
        )
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(chunk_size=(0, 1, 0), chunk_interval=1),
            models=FakeBundle(
                vad_results=[(0, -1)],
                online_texts=["partial"],
                offline_texts=["final"],
            ),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.stop"),
        )

        await session.run()

        sent = [decode_json_message(message) for message in websocket.sent]

        assert [item["type"] for item in sent] == ["ready", "transcript", "transcript"]
        assert sent[-1]["phase"] == "offline"
        assert sent[-1]["is_final"] is True

    async def test_when_emit_online_partial_receives_empty_or_duplicate_text__then_it_skips_sending(
        self,
    ) -> None:
        empty_websocket = FakeSessionWebsocket([])
        empty_session = FunasrWebsocketSession(
            websocket=empty_websocket,
            config=FunasrLocalServiceConfig(),
            models=FakeBundle(vad_results=[], online_texts=[""], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.online_empty"),
        )
        empty_session._state.online_packets = [sixty_ms_packet()]

        await empty_session._emit_online_partial()

        assert empty_websocket.sent == []
        assert empty_session._state.online_packets == []

        duplicate_websocket = FakeSessionWebsocket([])
        duplicate_session = FunasrWebsocketSession(
            websocket=duplicate_websocket,
            config=FunasrLocalServiceConfig(),
            models=FakeBundle(vad_results=[], online_texts=["same"], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.online_duplicate"),
        )
        duplicate_session._state.online_packets = [sixty_ms_packet()]
        duplicate_session._state.last_online_text = "same"

        await duplicate_session._emit_online_partial()

        assert duplicate_websocket.sent == []
        assert duplicate_session._state.online_packets == []

    async def test_when_force_flush_runs_without_offline_packets__then_it_resets_state_without_sending(
        self,
    ) -> None:
        websocket = FakeSessionWebsocket([])
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(),
            models=FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.force_reset"),
        )
        session._state.current_segment_id = 3
        session._state.online_packets = [sixty_ms_packet()]
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
