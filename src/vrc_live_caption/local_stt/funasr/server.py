"""Run the repository-local FunASR websocket sidecar."""

from __future__ import annotations

import importlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.server import serve

from ...errors import SttSessionError
from .config import FunasrLocalServiceConfig
from .protocol import build_error_message, encode_json_message
from .session import FunasrModelBundle, FunasrWebsocketSession


@dataclass(frozen=True, slots=True)
class ResolvedFunasrDevice:
    """Store the resolved runtime device used for FunASR inference."""

    device_policy: str
    resolved_device: str
    cuda_available: bool
    ngpu: int


class AutoModelFunasrBundle(FunasrModelBundle):
    """Wrap the FunASR AutoModel APIs used by the local sidecar."""

    def __init__(
        self,
        *,
        offline_model: Any,
        online_model: Any,
        vad_model: Any,
        punc_model: Any | None,
    ) -> None:
        self._offline_model = offline_model
        self._online_model = online_model
        self._vad_model = vad_model
        self._punc_model = punc_model

    @classmethod
    def load(
        cls,
        *,
        config: FunasrLocalServiceConfig,
        runtime_device: ResolvedFunasrDevice,
        logger: logging.Logger,
    ) -> AutoModelFunasrBundle:
        """Load the configured FunASR models lazily."""
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise SttSessionError(
                "FunASR dependencies are not installed. Install the funasr-cpu or funasr-cu128 extra."
            ) from exc

        model_kwargs = {
            "device": runtime_device.resolved_device,
            "ngpu": runtime_device.ngpu,
            "ncpu": config.ncpu,
            "disable_pbar": True,
            "disable_log": True,
        }
        logger.info(
            "Loading FunASR models with device policy=%s resolved_device=%s ngpu=%s",
            runtime_device.device_policy,
            runtime_device.resolved_device,
            runtime_device.ngpu,
        )
        logger.info("Loading FunASR offline model: %s", config.offline_asr_model)
        offline_model = AutoModel(model=config.offline_asr_model, **model_kwargs)
        logger.info("Loading FunASR online model: %s", config.online_asr_model)
        online_model = AutoModel(model=config.online_asr_model, **model_kwargs)
        logger.info("Loading FunASR VAD model: %s", config.vad_model)
        vad_model = AutoModel(model=config.vad_model, **model_kwargs)
        punc_model = None
        if config.punc_model.strip():
            logger.info("Loading FunASR punctuation model: %s", config.punc_model)
            punc_model = AutoModel(model=config.punc_model, **model_kwargs)

        return cls(
            offline_model=offline_model,
            online_model=online_model,
            vad_model=vad_model,
            punc_model=punc_model,
        )

    def detect_speech_boundary(
        self,
        *,
        audio: bytes,
        state: dict[str, Any],
    ) -> tuple[int, int]:
        """Run streaming VAD and return one `(start_ms, end_ms)` pair."""
        result = self._vad_model.generate(input=audio, **state)[0]
        segments = result.get("value", [])
        if len(segments) != 1:
            return -1, -1
        segment = segments[0]
        if not isinstance(segment, (list, tuple)) or len(segment) != 2:
            return -1, -1
        start_ms = int(segment[0]) if int(segment[0]) != -1 else -1
        end_ms = int(segment[1]) if int(segment[1]) != -1 else -1
        return start_ms, end_ms

    def transcribe_online(
        self,
        *,
        audio: bytes,
        state: dict[str, Any],
    ) -> str:
        """Run streaming ASR for one online packet window."""
        result = self._online_model.generate(input=audio, **state)[0]
        text = result.get("text", "")
        return text if isinstance(text, str) else str(text)

    def transcribe_offline(
        self,
        *,
        audio: bytes,
        state: dict[str, Any],
        punc_state: dict[str, Any],
    ) -> str:
        """Run offline ASR and optional punctuation for one finalized segment."""
        result = self._offline_model.generate(input=audio, **state)[0]
        text = result.get("text", "")
        if not isinstance(text, str):
            text = str(text)
        if self._punc_model is None or not text.strip():
            return text

        punc_result = self._punc_model.generate(input=text, **punc_state)[0]
        punc_text = punc_result.get("text", text)
        return punc_text if isinstance(punc_text, str) else str(punc_text)


def resolve_funasr_runtime_device(
    *,
    device_policy: str,
    torch_module: Any | None,
) -> ResolvedFunasrDevice:
    """Resolve the actual device string and GPU count used by FunASR."""
    cuda_available = _torch_cuda_is_available(torch_module)

    if device_policy == "auto":
        if cuda_available:
            return ResolvedFunasrDevice(
                device_policy=device_policy,
                resolved_device="cuda:0",
                cuda_available=True,
                ngpu=1,
            )
        return ResolvedFunasrDevice(
            device_policy=device_policy,
            resolved_device="cpu",
            cuda_available=False,
            ngpu=0,
        )

    if device_policy == "cpu":
        return ResolvedFunasrDevice(
            device_policy=device_policy,
            resolved_device="cpu",
            cuda_available=cuda_available,
            ngpu=0,
        )

    if device_policy == "cuda":
        if not cuda_available:
            raise SttSessionError(
                '[stt.providers.funasr_local.sidecar] requests device = "cuda", but torch.cuda.is_available() is false. '
                "Install the funasr-cu128 extra and verify the Windows NVIDIA CUDA runtime."
            )
        return ResolvedFunasrDevice(
            device_policy=device_policy,
            resolved_device="cuda:0",
            cuda_available=True,
            ngpu=1,
        )

    raise SttSessionError(f"Unsupported FunASR device policy: {device_policy}")


def _load_torch_module() -> Any | None:
    try:
        return importlib.import_module("torch")
    except ImportError:
        return None


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


def _torch_cuda_version(torch_module: Any | None) -> str | None:
    version_module = getattr(torch_module, "version", None)
    value = getattr(version_module, "cuda", None)
    if isinstance(value, str) and value.strip():
        return value
    return None


async def run_funasr_local_server(
    *,
    config: FunasrLocalServiceConfig,
    host: str,
    port: int,
    logger: logging.Logger,
) -> None:
    """Start the local websocket sidecar and wait until it stops."""
    executor = ThreadPoolExecutor(max_workers=max(config.ncpu, 2))
    try:
        torch_module = _load_torch_module()
        runtime_device = resolve_funasr_runtime_device(
            device_policy=config.device,
            torch_module=torch_module,
        )
        logger.info(
            "Resolved local FunASR device policy=%s resolved_device=%s cuda_available=%s torch_cuda_version=%s",
            runtime_device.device_policy,
            runtime_device.resolved_device,
            runtime_device.cuda_available,
            _torch_cuda_version(torch_module) or "none",
        )
        models = AutoModelFunasrBundle.load(
            config=config,
            runtime_device=runtime_device,
            logger=logger.getChild("models"),
        )

        async def handle_connection(websocket) -> None:
            session = FunasrWebsocketSession(
                websocket=websocket,
                config=config,
                models=models,
                executor=executor,
                resolved_device=runtime_device.resolved_device,
                device_policy=runtime_device.device_policy,
                logger=logger.getChild("session"),
            )
            try:
                await session.run()
            except SttSessionError as exc:
                logger.error("Local STT session failed: %s", exc)
                try:
                    await websocket.send(
                        encode_json_message(build_error_message(str(exc), fatal=True))
                    )
                except Exception:
                    return

        async with serve(handle_connection, host, port, ping_interval=None) as server:
            logger.info("Local FunASR sidecar listening on ws://%s:%s", host, port)
            await server.wait_closed()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "AutoModelFunasrBundle",
    "ResolvedFunasrDevice",
    "resolve_funasr_runtime_device",
    "run_funasr_local_server",
]
