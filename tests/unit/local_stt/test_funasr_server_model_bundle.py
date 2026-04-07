import builtins
import logging
import sys

import pytest

from tests.unit.local_stt._server_support import GeneratedModel
from vrc_live_caption.errors import SttSessionError
from vrc_live_caption.local_stt.funasr.config import FunasrLocalServiceConfig
from vrc_live_caption.local_stt.funasr.server import (
    AutoModelFunasrBundle,
    ResolvedFunasrDevice,
)


class TestAutoModelFunasrBundleLoad:
    def test_when_funasr_is_missing__then_it_raises_dependency_error(
        self,
        monkeypatch,
    ) -> None:
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "funasr":
                raise ImportError("missing funasr")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(
            SttSessionError,
            match="FunASR dependencies are not installed",
        ):
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

    def test_when_bundle_loads__then_it_passes_runtime_device_and_skips_blank_punctuation(
        self,
        monkeypatch,
    ) -> None:
        calls: list[tuple[str, dict]] = []

        def fake_auto_model(*, model, **kwargs):
            calls.append((model, kwargs))
            return GeneratedModel([{"text": model}])

        monkeypatch.setitem(
            sys.modules,
            "funasr",
            type("FunasrModule", (), {"AutoModel": fake_auto_model}),
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


class TestAutoModelFunasrBundleInference:
    def test_when_vad_shapes_are_invalid__then_detect_speech_boundary_returns_negative_markers(
        self,
    ) -> None:
        bundle = AutoModelFunasrBundle(
            offline_model=GeneratedModel([{"text": "unused"}]),
            online_model=GeneratedModel([{"text": "unused"}]),
            vad_model=GeneratedModel(
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

    def test_when_online_and_offline_results_are_not_strings__then_they_are_coerced(
        self,
    ) -> None:
        online_model = GeneratedModel([{"text": 123}])
        offline_model = GeneratedModel([{"text": 456}, {"text": "hello"}])
        punc_model = GeneratedModel([{"text": 789}])
        bundle = AutoModelFunasrBundle(
            offline_model=offline_model,
            online_model=online_model,
            vad_model=GeneratedModel([{"value": []}]),
            punc_model=punc_model,
        )

        assert bundle.transcribe_online(audio=b"pcm", state={}) == "123"
        assert bundle.transcribe_offline(audio=b"pcm", state={}, punc_state={}) == "789"

        bundle_without_punc = AutoModelFunasrBundle(
            offline_model=GeneratedModel([{"text": "   "}]),
            online_model=GeneratedModel([{"text": ""}]),
            vad_model=GeneratedModel([{"value": []}]),
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
