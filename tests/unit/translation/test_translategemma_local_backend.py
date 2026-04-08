import asyncio
import logging

import pytest
from websockets.asyncio.server import serve

from vrc_live_caption.config import TranslateGemmaLocalTranslationProviderConfig
from vrc_live_caption.errors import TranslationError
from vrc_live_caption.local_translation.translategemma.protocol import (
    build_error_message,
    build_ready_message,
    build_result_message,
    decode_json_message,
    encode_json_message,
)
from vrc_live_caption.translation.translategemma_local import (
    TranslateGemmaLocalTranslationBackend,
    probe_translategemma_local_service,
)
from vrc_live_caption.translation.types import TranslationRequest


@pytest.mark.asyncio
class TestTranslateGemmaLocalTranslationBackend:
    async def test_when_sidecar_returns_result__then_probe_and_translate_succeed(
        self,
    ) -> None:
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
                encode_json_message(build_result_message("translated output"))
            )

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            provider_config = TranslateGemmaLocalTranslationProviderConfig(port=port)
            ready = await asyncio.to_thread(
                probe_translategemma_local_service,
                provider_config=provider_config,
                timeout_seconds=1.0,
            )
            backend = TranslateGemmaLocalTranslationBackend(
                provider_config=provider_config,
                timeout_seconds=1.0,
                logger=logging.getLogger("test.translation.translategemma_local"),
            )

            result = await backend.translate(
                TranslationRequest(
                    utterance_id="utt-1",
                    revision=2,
                    text="你好",
                    source_language="zh",
                    target_language="en",
                )
            )

        assert ready.model == "google/translategemma-4b-it"
        assert ready.resolved_device == "cuda:0"
        assert received_requests == [
            {
                "type": "translate",
                "text": "你好",
                "source_language": "zh",
                "target_language": "en",
            }
        ]
        assert result.translated_text == "translated output"
        assert result.source_text == "你好"

    async def test_when_sidecar_returns_error__then_it_raises_translation_error(
        self,
    ) -> None:
        async def handler(websocket) -> None:
            await websocket.send(
                encode_json_message(
                    build_ready_message(
                        model="google/translategemma-4b-it",
                        device_policy="cpu",
                        resolved_device="cpu",
                        resolved_dtype="float32",
                    )
                )
            )
            await websocket.recv()
            await websocket.send(
                encode_json_message(build_error_message("translation boom"))
            )

        async with serve(handler, "127.0.0.1", 0, ping_interval=None) as server:
            port = server.sockets[0].getsockname()[1]
            backend = TranslateGemmaLocalTranslationBackend(
                provider_config=TranslateGemmaLocalTranslationProviderConfig(port=port),
                timeout_seconds=1.0,
                logger=logging.getLogger("test.translation.translategemma_error"),
            )

            with pytest.raises(TranslationError, match="translation boom"):
                await backend.translate(
                    TranslationRequest(
                        utterance_id="utt-err",
                        revision=1,
                        text="你好",
                        source_language="zh",
                        target_language="en",
                    )
                )
