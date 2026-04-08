from types import SimpleNamespace

import pytest

from tests.unit.local_stt._server_support import make_torch
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.server import (
    _load_torch_module,
    _torch_cuda_is_available,
    _torch_cuda_version,
    resolve_funasr_runtime_device,
)


class TestResolveFunasrRuntimeDevice:
    def test_when_device_policy_is_auto_and_cuda_is_available__then_it_prefers_cuda(
        self,
    ) -> None:
        resolved = resolve_funasr_runtime_device(
            device_policy="auto",
            torch_module=make_torch(cuda_available=True),
        )

        assert resolved.device_policy == "auto"
        assert resolved.resolved_device == "cuda:0"
        assert resolved.cuda_available is True
        assert resolved.ngpu == 1

    def test_when_device_policy_is_auto_and_cuda_is_unavailable__then_it_falls_back_to_cpu(
        self,
    ) -> None:
        resolved = resolve_funasr_runtime_device(
            device_policy="auto",
            torch_module=make_torch(cuda_available=False),
        )

        assert resolved.resolved_device == "cpu"
        assert resolved.cuda_available is False
        assert resolved.ngpu == 0

    def test_when_device_policy_is_cpu__then_it_honors_cpu_even_if_cuda_exists(
        self,
    ) -> None:
        resolved = resolve_funasr_runtime_device(
            device_policy="cpu",
            torch_module=make_torch(cuda_available=True),
        )

        assert resolved.resolved_device == "cpu"
        assert resolved.cuda_available is True
        assert resolved.ngpu == 0

    def test_when_cuda_policy_is_requested_without_cuda__then_it_raises_stt_session_error(
        self,
    ) -> None:
        with pytest.raises(SttSessionError, match="local-cu130 extra"):
            resolve_funasr_runtime_device(
                device_policy="cuda",
                torch_module=make_torch(cuda_available=False),
            )

    def test_when_device_policy_is_unknown__then_it_raises_stt_session_error(
        self,
    ) -> None:
        with pytest.raises(SttSessionError, match="Unsupported FunASR device policy"):
            resolve_funasr_runtime_device(
                device_policy="mps",
                torch_module=make_torch(cuda_available=False),
            )


class TestTorchRuntimeHelpers:
    def test_when_torch_import_fails__then_load_torch_module_returns_none(
        self,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.importlib.import_module",
            lambda name: (_ for _ in ()).throw(ImportError("torch missing")),
        )

        assert _load_torch_module() is None

    def test_when_torch_import_succeeds__then_load_torch_module_returns_the_module(
        self,
        monkeypatch,
    ) -> None:
        sentinel = object()
        monkeypatch.setattr(
            "vrc_live_caption.local_stt.funasr.server.importlib.import_module",
            lambda name: sentinel,
        )

        assert _load_torch_module() is sentinel

    def test_when_cuda_helpers_receive_missing_or_empty_cuda_state__then_they_report_unavailable(
        self,
    ) -> None:
        assert _torch_cuda_is_available(None) is False
        assert (
            _torch_cuda_is_available(SimpleNamespace(cuda=SimpleNamespace())) is False
        )
        assert (
            _torch_cuda_is_available(make_torch(cuda_available=True, device_count=0))
            is False
        )

    def test_when_cuda_reports_available_without_device_count__then_it_is_treated_as_available(
        self,
    ) -> None:
        module = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))

        assert _torch_cuda_is_available(module) is True

    def test_when_cuda_version_is_blank_or_non_string__then_it_returns_none(
        self,
    ) -> None:
        assert _torch_cuda_version(make_torch(cuda_available=True)) == "12.8"
        assert (
            _torch_cuda_version(SimpleNamespace(version=SimpleNamespace(cuda="  ")))
            is None
        )
        assert (
            _torch_cuda_version(SimpleNamespace(version=SimpleNamespace(cuda=123)))
            is None
        )
