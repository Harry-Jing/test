import asyncio
import logging
from typing import cast

import pytest
from websockets.asyncio.server import serve

from tests.unit.translation._support import FakeDeepLSecrets
from vrc_live_caption.config import (
    TranslateGemmaLocalTranslationProviderConfig,
    TranslationConfig,
    TranslationProvidersConfig,
)
from vrc_live_caption.env import AppSecrets
from vrc_live_caption.local_translation.translategemma.protocol import (
    build_ready_message,
    build_result_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.translation import create_translation_backend
from vrc_live_caption.translation.types import TranslationRequest


@pytest.mark.integration
def test_local_translation_sidecar_replays_request_through_websocket_backend() -> None:
    async def scenario() -> None:
        received_requests: list[dict[str, object]] = []

        async def handler(websocket) -> None:
            await websocket.send(
                encode_json_message(
                    build_ready_message(
                        model="google/translategemma-4b-it",
                        device_policy="auto",
                        resolved_device="cuda:0",
                        resolved_dtype="bfloat16",
                    )
                )
            )
            raw_message = await websocket.recv()
            assert isinstance(raw_message, str)
            received_requests.append(decode_json_message(raw_message))
            await websocket.send(
                encode_json_message(build_result_message("hello world"))
            )

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            backend = await asyncio.to_thread(
                create_translation_backend,
                translation_config=TranslationConfig(
                    enabled=True,
                    provider="translategemma_local",
                    source_language="zh",
                    target_language="en",
                    providers=TranslationProvidersConfig(
                        translategemma_local=TranslateGemmaLocalTranslationProviderConfig(
                            port=port
                        )
                    ),
                ),
                secrets=cast(AppSecrets, FakeDeepLSecrets()),
                logger=logging.getLogger("test.integration.local_translation"),
            )
            assert backend is not None

            result = await backend.translate(
                TranslationRequest(
                    utterance_id="utt-local-translation",
                    revision=1,
                    text="你好世界",
                    source_language="zh",
                    target_language="en",
                )
            )

        assert received_requests == [
            {
                "type": "translate",
                "text": "你好世界",
                "source_language": "zh",
                "target_language": "en",
            }
        ]
        assert result.translated_text == "hello world"

    asyncio.run(scenario())
