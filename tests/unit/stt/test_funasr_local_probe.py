import ssl
from typing import cast

import pytest
from websockets.asyncio.server import serve

from tests.unit.stt._funasr_local_support import FakeConnection
from vrc_live_caption.config import CaptureConfig, FunasrLocalProviderConfig
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.protocol import (
    build_error_message,
    build_ready_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.stt.funasr_local import (
    FatalFunasrLocalServerError,
    probe_funasr_local_service,
)


@pytest.mark.asyncio
class TestProbeFunasrLocalService:
    async def test_when_sidecar_returns_ready_event__then_probe_returns_device_metadata(
        self,
    ) -> None:
        async def handler(websocket) -> None:
            await websocket.recv()
            await websocket.send(
                encode_json_message(
                    build_ready_message(
                        "ready",
                        resolved_device="cuda:0",
                        device_policy="auto",
                    )
                )
            )
            await websocket.recv()

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            result = await probe_funasr_local_service(
                capture_config=CaptureConfig(),
                provider_config=FunasrLocalProviderConfig(port=port),
                timeout_seconds=1.0,
            )

        assert result.message == "ready"
        assert result.resolved_device == "cuda:0"
        assert result.device_policy == "auto"

    async def test_when_ssl_is_enabled_and_binary_frames_arrive__then_probe_ignores_them_and_closes_cleanly(
        self,
        monkeypatch,
    ) -> None:
        captured: dict[str, object] = {}
        connection = FakeConnection(
            [
                b"binary-frame",
                encode_json_message(
                    build_ready_message(
                        "ready",
                        resolved_device="cuda:0",
                        device_policy="auto",
                    )
                ),
            ]
        )

        async def fake_connect(url, ssl=None):
            captured["url"] = url
            captured["ssl"] = ssl
            return connection

        monkeypatch.setattr("vrc_live_caption.stt.funasr_local.connect", fake_connect)
        result = await probe_funasr_local_service(
            capture_config=CaptureConfig(),
            provider_config=FunasrLocalProviderConfig(port=10096, use_ssl=True),
            timeout_seconds=0.1,
        )

        assert result.resolved_device == "cuda:0"
        assert captured["url"] == "wss://127.0.0.1:10096"
        assert isinstance(captured["ssl"], ssl.SSLContext)
        assert decode_json_message(cast(str, connection.sent[0]))["type"] == "start"
        assert decode_json_message(cast(str, connection.sent[1]))["type"] == "stop"
        assert connection.closed is True

    async def test_when_server_error_arrives__then_probe_raises_matching_exception(
        self,
        monkeypatch,
    ) -> None:
        async def run_case(
            *,
            fatal: bool,
            expected_exception: type[BaseException],
        ) -> None:
            connection = FakeConnection(
                [encode_json_message(build_error_message("server boom", fatal=fatal))]
            )

            async def fake_connect(url, ssl=None):
                return connection

            monkeypatch.setattr(
                "vrc_live_caption.stt.funasr_local.connect",
                fake_connect,
            )
            with pytest.raises(expected_exception, match="server boom"):
                await probe_funasr_local_service(
                    capture_config=CaptureConfig(),
                    provider_config=FunasrLocalProviderConfig(),
                    timeout_seconds=0.1,
                )
            assert connection.closed is True

        await run_case(fatal=True, expected_exception=FatalFunasrLocalServerError)
        await run_case(fatal=False, expected_exception=SttSessionError)
