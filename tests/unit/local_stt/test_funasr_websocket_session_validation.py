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
    build_client_start_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.local_stt.funasr.session import FunasrWebsocketSession


@pytest.mark.asyncio
class TestFunasrWebsocketSessionValidation:
    async def test_when_control_message_is_invalid_json__then_it_sends_fatal_error(
        self,
    ) -> None:
        websocket = FakeSessionWebsocket(["{not-json"])
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(),
            models=FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.invalid_json"),
        )

        await session.run()

        sent = [decode_json_message(message) for message in websocket.sent]

        assert sent == [
            {
                "type": "error",
                "message": "Invalid JSON control message: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
                "fatal": True,
            }
        ]

    async def test_when_control_message_type_is_unsupported__then_it_sends_fatal_error(
        self,
    ) -> None:
        websocket = FakeSessionWebsocket(
            [encode_json_message({"type": "ping", "message": "hello"})]
        )
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(),
            models=FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.unsupported"),
        )

        await session.run()

        sent = [decode_json_message(message) for message in websocket.sent]

        assert sent == [
            {
                "type": "error",
                "message": "Unsupported local STT control message type: ping",
                "fatal": True,
            }
        ]

    async def test_when_start_message_is_sent_twice__then_it_rejects_the_duplicate_start(
        self,
    ) -> None:
        websocket = FakeSessionWebsocket(
            [encoded_start_message(), encoded_start_message()]
        )
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(),
            models=FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.duplicate_start"),
        )

        await session.run()

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
            pytest.param(
                build_client_start_message(
                    sample_rate=16_000,
                    channels=1,
                    mode="1pass",
                ),
                "Unsupported local STT mode: 1pass",
                id="unsupported-mode",
            ),
            pytest.param(
                build_client_start_message(
                    sample_rate=16_000,
                    channels=1,
                    sample_format="float32",
                ),
                "Unsupported local STT sample format: float32",
                id="unsupported-sample-format",
            ),
            pytest.param(
                build_client_start_message(sample_rate=8_000, channels=1),
                "FunASR local sidecar currently requires sample_rate = 16000",
                id="unsupported-sample-rate",
            ),
            pytest.param(
                build_client_start_message(sample_rate=16_000, channels=2),
                "FunASR local sidecar currently requires channels = 1",
                id="unsupported-channels",
            ),
        ],
    )
    async def test_when_start_parameters_are_invalid__then_it_sends_fatal_error(
        self,
        message: dict[str, object],
        expected: str,
    ) -> None:
        websocket = FakeSessionWebsocket([encode_json_message(message)])
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(),
            models=FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.invalid_start"),
        )

        await session.run()

        assert [decode_json_message(item) for item in websocket.sent] == [
            {
                "type": "error",
                "message": expected,
                "fatal": True,
            }
        ]

    async def test_when_audio_arrives_before_start__then_it_sends_fatal_error(
        self,
    ) -> None:
        websocket = FakeSessionWebsocket([sixty_ms_packet()])
        session = FunasrWebsocketSession(
            websocket=websocket,
            config=FunasrLocalServiceConfig(),
            models=FakeBundle(vad_results=[], online_texts=[], offline_texts=[]),
            executor=None,
            logger=logging.getLogger("test.local_stt.session.audio_before_start"),
        )

        await session.run()

        sent = [decode_json_message(message) for message in websocket.sent]
        expected = {
            "type": "error",
            "message": "Audio received before the local STT session was started",
            "fatal": True,
        }

        assert sent == [expected, expected]
