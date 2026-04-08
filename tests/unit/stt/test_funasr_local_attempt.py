import asyncio
import logging
from typing import cast

import pytest
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from tests.unit.stt._funasr_local_support import FakeConnection, make_attempt_context
from vrc_live_caption.config import CaptureConfig, FunasrLocalProviderConfig
from vrc_live_caption.local_stt.funasr.protocol import (
    build_client_start_message,
    build_client_stop_message,
    build_ready_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.stt.funasr_local import (
    FunasrLocalAttempt,
    FunasrLocalConnectionState,
)


@pytest.mark.asyncio
class TestFunasrLocalAttempt:
    async def test_when_stop_is_requested__then_attempt_flushes_and_closes_connection(
        self,
        monkeypatch,
    ) -> None:
        context, _, ready_messages = make_attempt_context()
        context.stop_requested.set()

        class FlushConnection(FakeConnection):
            async def recv(self) -> bytes | str:
                if self._responses:
                    return await super().recv()
                while len(self.sent) < 2:
                    await asyncio.sleep(0)
                raise ConnectionClosedOK(
                    Close(1000, "closed"),
                    Close(1000, "closed"),
                    True,
                )

        connection = FlushConnection(
            [
                encode_json_message(
                    build_ready_message(
                        "ready",
                        resolved_device="cuda:0",
                        device_policy="auto",
                    )
                )
            ]
        )

        async def fake_connect(url, ssl=None):
            return connection

        monkeypatch.setattr("vrc_live_caption.stt.funasr_local.connect", fake_connect)
        attempt = FunasrLocalAttempt(
            state=FunasrLocalConnectionState(),
            context=context,
            provider_config=FunasrLocalProviderConfig(),
            capture_config=CaptureConfig(),
            logger=logging.getLogger("test.funasr_local.flush"),
        )

        await attempt.run()

        assert decode_json_message(
            cast(str, connection.sent[0])
        ) == build_client_start_message(
            sample_rate=16_000,
            channels=1,
        )
        assert (
            decode_json_message(cast(str, connection.sent[1]))
            == build_client_stop_message()
        )
        assert ready_messages == [
            "FunASR local sidecar ready (127.0.0.1:10095, device=cuda:0, policy=auto)"
        ]
        assert connection.closed is True

    async def test_when_receiver_disconnects_unexpectedly__then_attempt_raises_os_error(
        self,
        monkeypatch,
    ) -> None:
        context, _, _ = make_attempt_context()
        connection = FakeConnection(
            [
                encode_json_message(build_ready_message("ready")),
                ConnectionClosedOK(
                    Close(1000, "closed"),
                    Close(1000, "closed"),
                    True,
                ),
            ]
        )

        async def fake_connect(url, ssl=None):
            return connection

        monkeypatch.setattr("vrc_live_caption.stt.funasr_local.connect", fake_connect)
        attempt = FunasrLocalAttempt(
            state=FunasrLocalConnectionState(),
            context=context,
            provider_config=FunasrLocalProviderConfig(),
            capture_config=CaptureConfig(),
            logger=logging.getLogger("test.funasr_local.receiver_disconnect"),
        )

        with pytest.raises(OSError, match="connection closed unexpectedly"):
            await attempt.run()

        assert connection.closed is True
