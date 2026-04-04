from types import SimpleNamespace

import pytest

from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.server import resolve_funasr_runtime_device


def _make_torch(*, cuda_available: bool, device_count: int = 1) -> SimpleNamespace:
    cuda = SimpleNamespace(
        is_available=lambda: cuda_available,
        device_count=lambda: device_count,
    )
    version = SimpleNamespace(cuda="12.8" if cuda_available else None)
    return SimpleNamespace(cuda=cuda, version=version)


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
