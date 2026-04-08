"""Run the repository-local TranslateGemma websocket sidecar."""

from __future__ import annotations

import asyncio
import importlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedOK

from ...errors import TranslationError
from .config import TranslateGemmaLocalServiceConfig
from .protocol import (
    build_error_message,
    build_ready_message,
    build_result_message,
    decode_json_message,
    encode_json_message,
)


@dataclass(frozen=True, slots=True)
class ResolvedTranslateGemmaRuntime:
    """Store the resolved runtime device and dtype used for inference."""

    device_policy: str
    resolved_device: str
    resolved_dtype: str
    torch_dtype: Any
    cuda_available: bool


class TranslateGemmaModelBundle:
    """Wrap the TranslateGemma processor and model used by the sidecar."""

    def __init__(
        self,
        *,
        processor: Any,
        model: Any,
        torch_module: Any,
        runtime: ResolvedTranslateGemmaRuntime,
        max_new_tokens: int,
    ) -> None:
        self._processor = processor
        self._model = model
        self._torch = torch_module
        self._runtime = runtime
        self._max_new_tokens = max_new_tokens

    @classmethod
    def load(
        cls,
        *,
        config: TranslateGemmaLocalServiceConfig,
        runtime: ResolvedTranslateGemmaRuntime,
        logger: logging.Logger,
    ) -> TranslateGemmaModelBundle:
        """Load the configured TranslateGemma model and processor eagerly."""
        try:
            torch_module = importlib.import_module("torch")
            transformers = importlib.import_module("transformers")
        except ImportError as exc:
            raise TranslationError(
                "TranslateGemma dependencies are not installed. "
                "Install the translategemma-cpu or translategemma-cu128 extra."
            ) from exc

        model_cls = getattr(transformers, "AutoModelForImageTextToText", None)
        processor_cls = getattr(transformers, "AutoProcessor", None)
        if model_cls is None or processor_cls is None:
            raise TranslationError(
                "Installed transformers build does not expose TranslateGemma loading APIs. "
                "Install a recent transformers release from the translategemma extras."
            )

        logger.info(
            "Loading TranslateGemma model=%s device_policy=%s resolved_device=%s resolved_dtype=%s",
            config.model,
            runtime.device_policy,
            runtime.resolved_device,
            runtime.resolved_dtype,
        )
        try:
            processor = processor_cls.from_pretrained(config.model)
            model = model_cls.from_pretrained(
                config.model,
                torch_dtype=runtime.torch_dtype,
            )
            model.to(runtime.resolved_device)
            model.eval()
        except Exception as exc:
            raise TranslationError(
                "TranslateGemma model load failed for "
                f"{config.model}: {exc}. Accept the Gemma license on Hugging Face and authenticate with "
                "`hf auth login` or set HF_TOKEN when using a gated repo."
            ) from exc

        return cls(
            processor=processor,
            model=model,
            torch_module=torch_module,
            runtime=runtime,
            max_new_tokens=config.max_new_tokens,
        )

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        """Translate one text input with the official TranslateGemma chat template."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "source_lang_code": source_language,
                        "target_lang_code": target_language,
                        "text": text,
                    }
                ],
            }
        ]

        try:
            inputs = self._processor.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
                return_tensors="pt",
            )
            inputs = inputs.to(self._runtime.resolved_device)
            input_len = len(inputs["input_ids"][0])
            with self._torch.inference_mode():
                generation = self._model.generate(
                    **inputs,
                    do_sample=False,
                    max_new_tokens=self._max_new_tokens,
                )
            decoded = self._processor.decode(
                generation[0][input_len:],
                skip_special_tokens=True,
            ).strip()
        except Exception as exc:
            raise TranslationError(f"TranslateGemma translation failed: {exc}") from exc

        if not decoded:
            raise TranslationError(
                "TranslateGemma translation returned an empty result"
            )
        return decoded


def resolve_translategemma_runtime(
    *,
    device_policy: str,
    dtype_policy: str,
    torch_module: Any | None,
) -> ResolvedTranslateGemmaRuntime:
    """Resolve the actual device string and dtype used by TranslateGemma."""
    cuda_available = _torch_cuda_is_available(torch_module)

    if device_policy == "auto":
        resolved_device = "cuda:0" if cuda_available else "cpu"
    elif device_policy == "cpu":
        resolved_device = "cpu"
    elif device_policy == "cuda":
        if not cuda_available:
            raise TranslationError(
                '[translation.providers.translategemma_local.sidecar] requests device = "cuda", '
                "but torch.cuda.is_available() is false. Install the translategemma-cu128 extra "
                "and verify the Windows NVIDIA CUDA runtime."
            )
        resolved_device = "cuda:0"
    else:
        raise TranslationError(
            f"Unsupported TranslateGemma device policy: {device_policy}"
        )

    resolved_dtype = dtype_policy
    if dtype_policy == "auto":
        resolved_dtype = "bfloat16" if resolved_device.startswith("cuda") else "float32"

    return ResolvedTranslateGemmaRuntime(
        device_policy=device_policy,
        resolved_device=resolved_device,
        resolved_dtype=resolved_dtype,
        torch_dtype=_resolve_torch_dtype(torch_module, resolved_dtype),
        cuda_available=cuda_available,
    )


def _load_torch_module() -> Any | None:
    try:
        return importlib.import_module("torch")
    except ImportError:
        return None


def _resolve_torch_dtype(torch_module: Any | None, dtype_name: str) -> Any:
    if torch_module is None:
        raise TranslationError(
            "TranslateGemma dependencies are not installed. "
            "Install the translategemma-cpu or translategemma-cu128 extra."
        )
    dtype = getattr(torch_module, dtype_name, None)
    if dtype is None:
        raise TranslationError(f"Unsupported torch dtype: {dtype_name}")
    return dtype


def _torch_cuda_is_available(torch_module: Any | None) -> bool:
    if torch_module is None:
        return False

    cuda_module = getattr(torch_module, "cuda", None)
    is_available = getattr(cuda_module, "is_available", None)
    if not callable(is_available) or not bool(is_available()):
        return False

    device_count = getattr(cuda_module, "device_count", None)
    if callable(device_count):
        return int(device_count()) > 0
    return True


def _parse_translate_request(event: dict[str, Any]) -> tuple[str, str, str]:
    if event.get("type") != "translate":
        raise TranslationError("expected a translate request")

    text = event.get("text")
    source_language = event.get("source_language")
    target_language = event.get("target_language")
    if not isinstance(text, str) or not text.strip():
        raise TranslationError("translate request text must be a non-empty string")
    if not isinstance(source_language, str) or not source_language.strip():
        raise TranslationError(
            "translate request source_language must be a non-empty string"
        )
    if not isinstance(target_language, str) or not target_language.strip():
        raise TranslationError(
            "translate request target_language must be a non-empty string"
        )
    return text.strip(), source_language.strip(), target_language.strip()


async def run_translategemma_local_server(
    *,
    config: TranslateGemmaLocalServiceConfig,
    host: str,
    port: int,
    logger: logging.Logger,
) -> None:
    """Start the local TranslateGemma websocket sidecar and wait until it stops."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        runtime = resolve_translategemma_runtime(
            device_policy=config.device,
            dtype_policy=config.dtype,
            torch_module=_load_torch_module(),
        )
        bundle = TranslateGemmaModelBundle.load(
            config=config,
            runtime=runtime,
            logger=logger.getChild("models"),
        )
        ready_message = encode_json_message(
            build_ready_message(
                model=config.model,
                device_policy=runtime.device_policy,
                resolved_device=runtime.resolved_device,
                resolved_dtype=runtime.resolved_dtype,
            )
        )

        async def handle_connection(websocket) -> None:
            await websocket.send(ready_message)
            try:
                raw_message = await websocket.recv()
            except ConnectionClosedOK:
                return
            if isinstance(raw_message, bytes):
                await websocket.send(
                    encode_json_message(
                        build_error_message("protocol message must be text JSON")
                    )
                )
                return

            try:
                text, source_language, target_language = _parse_translate_request(
                    decode_json_message(raw_message)
                )
                translated_text = await asyncio.get_running_loop().run_in_executor(
                    executor,
                    bundle.translate,
                    text,
                    source_language,
                    target_language,
                )
            except TranslationError as exc:
                await websocket.send(
                    encode_json_message(build_error_message(str(exc), fatal=True))
                )
                return
            except Exception as exc:
                await websocket.send(
                    encode_json_message(
                        build_error_message(f"TranslateGemma sidecar failure: {exc}")
                    )
                )
                return

            await websocket.send(
                encode_json_message(build_result_message(translated_text))
            )

        async with serve(handle_connection, host, port, ping_interval=None) as server:
            logger.info(
                "Local TranslateGemma sidecar listening on ws://%s:%s", host, port
            )
            await server.wait_closed()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "ResolvedTranslateGemmaRuntime",
    "TranslateGemmaModelBundle",
    "resolve_translategemma_runtime",
    "run_translategemma_local_server",
]
