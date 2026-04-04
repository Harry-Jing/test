import asyncio
import builtins
import logging
import sys
from types import SimpleNamespace

import pytest

from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.config import FunasrLocalServiceConfig
from vrc_live_caption.local_stt.funasr.protocol import (
    build_error_message,
    decode_json_message,
)
from vrc_live_caption.local_stt.funasr.server import (
    AutoModelFunasrBundle,
    ResolvedFunasrDevice,
    _load_torch_module,
    _torch_cuda_is_available,
    _torch_cuda_version,
    resolve_funasr_runtime_device,
    run_funasr_local_server,
)


def _make_torch(*, cuda_available: bool, device_count: int = 1) -> SimpleNamespace:
    cuda = SimpleNamespace(
        is_available=lambda: cuda_available,
        device_count=lambda: device_count,
    )
    version = SimpleNamespace(cuda="12.8" if cuda_available else None)
    return SimpleNamespace(cuda=cuda, version=version)


class _GeneratedModel:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0) if self._responses else {}
        return [response]


class _FakeWebsocket:
    def __init__(self, *, fail_on_send: bool = False) -> None:
        self.fail_on_send = fail_on_send
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        if self.fail_on_send:
            raise RuntimeError("send failed")
        self.sent.append(message)


class _FakeServeContext:
    def __init__(self, websocket: _FakeWebsocket | None = None) -> None:
        self.websocket = websocket
        self.handler = None
        self.host = None
        self.port = None
        self.ping_interval = None

    def __call__(self, handler, host, port, ping_interval=None):
        self.handler = handler
        self.host = host
        self.port = port
        self.ping_interval = ping_interval
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def wait_closed(self) -> None:
        if self.websocket is not None and self.handler is not None:
            await self.handler(self.websocket)


def test_resolve_funasr_runtime_device_prefers_cuda_for_auto() -> None:
    resolved = resolve_funasr_runtime_device(
        device_policy="auto",
        torch_module=_make_torch(cuda_available=True),
    )

    assert resolved.device_policy == "auto"
    assert resolved.resolved_device == "cuda:0"
    assert resolved.cuda_available is True
    assert resolved.ngpu == 1


def test_resolve_funasr_runtime_device_falls_back_to_cpu_for_auto() -> None:
    resolved = resolve_funasr_runtime_device(
        device_policy="auto",
        torch_module=_make_torch(cuda_available=False),
    )

    assert resolved.resolved_device == "cpu"
    assert resolved.cuda_available is False
    assert resolved.ngpu == 0


def test_resolve_funasr_runtime_device_honors_cpu_policy() -> None:
    resolved = resolve_funasr_runtime_device(
        device_policy="cpu",
        torch_module=_make_torch(cuda_available=True),
    )

    assert resolved.resolved_device == "cpu"
    assert resolved.cuda_available is True
    assert resolved.ngpu == 0


def test_resolve_funasr_runtime_device_rejects_unavailable_cuda() -> None:
    with pytest.raises(SttSessionError, match="funasr-cu128 extra"):
        resolve_funasr_runtime_device(
            device_policy="cuda",
            torch_module=_make_torch(cuda_available=False),
        )


def test_resolve_funasr_runtime_device_rejects_unknown_policy() -> None:
    with pytest.raises(SttSessionError, match="Unsupported FunASR device policy"):
        resolve_funasr_runtime_device(
            device_policy="mps",
            torch_module=_make_torch(cuda_available=False),
        )


def test_load_torch_module_returns_none_when_import_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server.importlib.import_module",
        lambda name: (_ for _ in ()).throw(ImportError("torch missing")),
    )

    assert _load_torch_module() is None


def test_load_torch_module_returns_imported_module(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server.importlib.import_module",
        lambda name: sentinel,
    )

    assert _load_torch_module() is sentinel


def test_torch_cuda_is_available_handles_missing_and_zero_device_count() -> None:
    assert _torch_cuda_is_available(None) is False
    assert _torch_cuda_is_available(SimpleNamespace(cuda=SimpleNamespace())) is False
    assert (
        _torch_cuda_is_available(_make_torch(cuda_available=True, device_count=0))
        is False
    )


def test_torch_cuda_is_available_accepts_true_without_device_count() -> None:
    module = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))

    assert _torch_cuda_is_available(module) is True


def test_torch_cuda_version_returns_string_or_none() -> None:
    assert _torch_cuda_version(_make_torch(cuda_available=True)) == "12.8"
    assert (
        _torch_cuda_version(SimpleNamespace(version=SimpleNamespace(cuda="  "))) is None
    )
    assert (
        _torch_cuda_version(SimpleNamespace(version=SimpleNamespace(cuda=123))) is None
    )


def test_auto_model_bundle_load_raises_when_funasr_is_missing(monkeypatch) -> None:
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "funasr":
            raise ImportError("missing funasr")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(SttSessionError, match="FunASR dependencies are not installed"):
        AutoModelFunasrBundle.load(
            config=FunasrLocalServiceConfig(),
            runtime_device=ResolvedFunasrDevice(
                device_policy="cpu",
                resolved_device="cpu",
                cuda_available=False,
                ngpu=0,
            ),
            logger=logging.getLogger("test.local_stt.server.import_error"),
        )


def test_auto_model_bundle_load_passes_runtime_device_and_skips_blank_punctuation(
    monkeypatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_auto_model(*, model, **kwargs):
        calls.append((model, kwargs))
        return _GeneratedModel([{"text": model}])

    monkeypatch.setitem(
        sys.modules, "funasr", SimpleNamespace(AutoModel=fake_auto_model)
    )
    config = FunasrLocalServiceConfig().model_copy(update={"punc_model": " "})
    bundle = AutoModelFunasrBundle.load(
        config=config,
        runtime_device=ResolvedFunasrDevice(
            device_policy="auto",
            resolved_device="cuda:0",
            cuda_available=True,
            ngpu=1,
        ),
        logger=logging.getLogger("test.local_stt.server.load"),
    )

    assert len(calls) == 3
    assert {call[0] for call in calls} == {
        config.offline_asr_model,
        config.online_asr_model,
        config.vad_model,
    }
    assert all(call[1]["device"] == "cuda:0" for call in calls)
    assert all(call[1]["ngpu"] == 1 for call in calls)
    assert (
        bundle.transcribe_offline(audio=b"pcm", state={}, punc_state={})
        == config.offline_asr_model
    )


def test_auto_model_bundle_detect_speech_boundary_handles_invalid_shapes() -> None:
    bundle = AutoModelFunasrBundle(
        offline_model=_GeneratedModel([{"text": "unused"}]),
        online_model=_GeneratedModel([{"text": "unused"}]),
        vad_model=_GeneratedModel(
            [
                {"value": []},
                {"value": [[0, 1], [2, 3]]},
                {"value": ["bad"]},
                {"value": [[120, 360]]},
            ]
        ),
        punc_model=None,
    )

    assert bundle.detect_speech_boundary(audio=b"pcm", state={}) == (-1, -1)
    assert bundle.detect_speech_boundary(audio=b"pcm", state={}) == (-1, -1)
    assert bundle.detect_speech_boundary(audio=b"pcm", state={}) == (-1, -1)
    assert bundle.detect_speech_boundary(audio=b"pcm", state={}) == (120, 360)


def test_auto_model_bundle_transcribe_online_and_offline_coerce_results() -> None:
    online_model = _GeneratedModel([{"text": 123}])
    offline_model = _GeneratedModel([{"text": 456}, {"text": "hello"}])
    punc_model = _GeneratedModel([{"text": 789}])
    bundle = AutoModelFunasrBundle(
        offline_model=offline_model,
        online_model=online_model,
        vad_model=_GeneratedModel([{"value": []}]),
        punc_model=punc_model,
    )

    assert bundle.transcribe_online(audio=b"pcm", state={}) == "123"
    assert bundle.transcribe_offline(audio=b"pcm", state={}, punc_state={}) == "789"

    bundle_without_punc = AutoModelFunasrBundle(
        offline_model=_GeneratedModel([{"text": "   "}]),
        online_model=_GeneratedModel([{"text": ""}]),
        vad_model=_GeneratedModel([{"value": []}]),
        punc_model=None,
    )
    assert (
        bundle_without_punc.transcribe_offline(
            audio=b"pcm",
            state={},
            punc_state={},
        )
        == "   "
    )


def test_run_funasr_local_server_invokes_serve_and_session(monkeypatch) -> None:
    session_inits: list[dict] = []
    serve_context = _FakeServeContext(websocket=_FakeWebsocket())
    models = object()

    class FakeSession:
        def __init__(self, **kwargs) -> None:
            session_inits.append(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server._load_torch_module",
        lambda: _make_torch(cuda_available=True),
    )
    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server.AutoModelFunasrBundle.load",
        lambda **kwargs: models,
    )
    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server.FunasrWebsocketSession",
        FakeSession,
    )
    monkeypatch.setattr("vrc_live_caption.local_stt.funasr.server.serve", serve_context)

    asyncio.run(
        run_funasr_local_server(
            config=FunasrLocalServiceConfig(),
            host="127.0.0.1",
            port=10095,
            logger=logging.getLogger("test.local_stt.server.run"),
        )
    )

    assert serve_context.host == "127.0.0.1"
    assert serve_context.port == 10095
    assert serve_context.ping_interval is None
    assert session_inits[0]["models"] is models
    assert session_inits[0]["resolved_device"] == "cuda:0"
    assert session_inits[0]["device_policy"] == "auto"


def test_run_funasr_local_server_sends_fatal_error_when_session_fails(
    monkeypatch,
) -> None:
    websocket = _FakeWebsocket()
    serve_context = _FakeServeContext(websocket=websocket)

    class FakeSession:
        def __init__(self, **kwargs) -> None:
            return None

        async def run(self) -> None:
            raise SttSessionError("boom")

    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server._load_torch_module",
        lambda: _make_torch(cuda_available=False),
    )
    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server.AutoModelFunasrBundle.load",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server.FunasrWebsocketSession",
        FakeSession,
    )
    monkeypatch.setattr("vrc_live_caption.local_stt.funasr.server.serve", serve_context)

    asyncio.run(
        run_funasr_local_server(
            config=FunasrLocalServiceConfig(),
            host="127.0.0.1",
            port=10095,
            logger=logging.getLogger("test.local_stt.server.fatal"),
        )
    )

    assert decode_json_message(websocket.sent[0]) == build_error_message(
        "boom", fatal=True
    )


def test_run_funasr_local_server_ignores_secondary_send_failures(monkeypatch) -> None:
    serve_context = _FakeServeContext(websocket=_FakeWebsocket(fail_on_send=True))

    class FakeSession:
        def __init__(self, **kwargs) -> None:
            return None

        async def run(self) -> None:
            raise SttSessionError("boom")

    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server._load_torch_module",
        lambda: _make_torch(cuda_available=False),
    )
    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server.AutoModelFunasrBundle.load",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "vrc_live_caption.local_stt.funasr.server.FunasrWebsocketSession",
        FakeSession,
    )
    monkeypatch.setattr("vrc_live_caption.local_stt.funasr.server.serve", serve_context)

    asyncio.run(
        run_funasr_local_server(
            config=FunasrLocalServiceConfig(),
            host="127.0.0.1",
            port=10095,
            logger=logging.getLogger("test.local_stt.server.send_failure"),
        )
    )
