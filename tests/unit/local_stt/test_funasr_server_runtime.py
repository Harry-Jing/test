import logging

import pytest

from tests.unit.local_stt._server_support import (
    FakeServeContext,
    FakeWebsocket,
    make_torch,
)
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.config import FunasrLocalServiceConfig
from vrc_live_caption.local_stt.funasr.protocol import (
    build_error_message,
    decode_json_message,
)
from vrc_live_caption.local_stt.funasr.server import run_funasr_local_server


@pytest.mark.asyncio
class TestRunFunasrLocalServer:
    async def test_when_server_starts__then_it_invokes_serve_and_builds_the_session(
        self,
        monkeypatch,
    ) -> None:
        session_inits: list[dict] = []
        serve_context = FakeServeContext(websocket=FakeWebsocket())
        models = object()

        class FakeSession:
            def __init__(self, **kwargs) -> None:
                session_inits.append(kwargs)

            async def run(self) -> None:
                return None

        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server._load_torch_module",
            lambda: make_torch(cuda_available=True),
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.AutoModelFunasrBundle.load",
            lambda **kwargs: models,
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.FunasrWebsocketSession",
            FakeSession,
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.serve", serve_context
        )

        await run_funasr_local_server(
            config=FunasrLocalServiceConfig(),
            host="127.0.0.1",
            port=10095,
            logger=logging.getLogger("test.local_stt.server.run"),
        )

        assert serve_context.host == "127.0.0.1"
        assert serve_context.port == 10095
        assert serve_context.ping_interval is None
        assert session_inits[0]["models"] is models
        assert session_inits[0]["resolved_device"] == "cuda:0"
        assert session_inits[0]["device_policy"] == "auto"

    async def test_when_session_raises_stt_session_error__then_it_sends_a_fatal_error_message(
        self,
        monkeypatch,
    ) -> None:
        websocket = FakeWebsocket()
        serve_context = FakeServeContext(websocket=websocket)

        class FakeSession:
            def __init__(self, **kwargs) -> None:
                return None

            async def run(self) -> None:
                raise SttSessionError("boom")

        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server._load_torch_module",
            lambda: make_torch(cuda_available=False),
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.AutoModelFunasrBundle.load",
            lambda **kwargs: object(),
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.FunasrWebsocketSession",
            FakeSession,
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.serve", serve_context
        )

        await run_funasr_local_server(
            config=FunasrLocalServiceConfig(),
            host="127.0.0.1",
            port=10095,
            logger=logging.getLogger("test.local_stt.server.fatal"),
        )

        assert decode_json_message(websocket.sent[0]) == build_error_message(
            "boom",
            fatal=True,
        )

    async def test_when_secondary_send_of_fatal_error_fails__then_it_is_ignored(
        self,
        monkeypatch,
    ) -> None:
        serve_context = FakeServeContext(websocket=FakeWebsocket(fail_on_send=True))

        class FakeSession:
            def __init__(self, **kwargs) -> None:
                return None

            async def run(self) -> None:
                raise SttSessionError("boom")

        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server._load_torch_module",
            lambda: make_torch(cuda_available=False),
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.AutoModelFunasrBundle.load",
            lambda **kwargs: object(),
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.FunasrWebsocketSession",
            FakeSession,
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.serve", serve_context
        )

        await run_funasr_local_server(
            config=FunasrLocalServiceConfig(),
            host="127.0.0.1",
            port=10095,
            logger=logging.getLogger("test.local_stt.server.send_failure"),
        )
