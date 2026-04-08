import importlib
import logging
from contextlib import nullcontext

import pytest

from tests.unit.local_stt._server_support import FakeServeContext
from vrc_live_caption.errors import TranslationError
from vrc_live_caption.local_translation.translategemma.config import (
    TranslateGemmaLocalServiceConfig,
)
from vrc_live_caption.local_translation.translategemma.server import (
    ResolvedTranslateGemmaRuntime,
    TranslateGemmaLocalServerReadyInfo,
    TranslateGemmaModelBundle,
    resolve_translategemma_runtime,
    run_translategemma_local_server,
)


class _FakeBatchInputs(dict):
    def to(self, device: str):
        self["device"] = device
        return self


class _FakeProcessor:
    def __init__(self) -> None:
        self.messages = None
        self.decoded = None

    def apply_chat_template(self, messages, **kwargs):
        self.messages = messages
        return _FakeBatchInputs(input_ids=[[10, 20]])

    def decode(self, tokens, *, skip_special_tokens: bool) -> str:
        self.decoded = {
            "tokens": list(tokens),
            "skip_special_tokens": skip_special_tokens,
        }
        return "translated text"


class _FakeModel:
    def __init__(self) -> None:
        self.generate_calls: list[dict[str, object]] = []
        self.device = None
        self.eval_called = False

    def to(self, device: str) -> None:
        self.device = device

    def eval(self) -> None:
        self.eval_called = True

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return [[10, 20, 30, 40]]


class _FakeWebsocket:
    def __init__(self, messages: list[object] | None = None) -> None:
        self._messages = list(messages or [])
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        if self._messages:
            return self._messages.pop(0)
        raise RuntimeError("no message queued")


class TestTranslateGemmaModelBundleLoad:
    def test_when_dependencies_are_missing__then_it_raises_dependency_error(
        self,
        monkeypatch,
    ) -> None:
        original_import_module = importlib.import_module

        def fake_import_module(name: str):
            if name in {"torch", "transformers"}:
                raise ImportError(f"missing {name}")
            return original_import_module(name)

        monkeypatch.setattr(
            "vrc_live_caption.local_translation.translategemma.server.importlib.import_module",
            fake_import_module,
        )

        with pytest.raises(
            TranslationError,
            match="Install the local-cpu or local-cu130 extra",
        ):
            TranslateGemmaModelBundle.load(
                config=TranslateGemmaLocalServiceConfig(),
                runtime=ResolvedTranslateGemmaRuntime(
                    device_policy="auto",
                    resolved_device="cuda:0",
                    resolved_dtype="bfloat16",
                    torch_dtype="bfloat16",
                    cuda_available=True,
                ),
                logger=logging.getLogger("test.local_translation.import_error"),
            )

    def test_when_bundle_loads__then_it_passes_runtime_device_and_dtype(
        self,
        monkeypatch,
    ) -> None:
        captured: dict[str, object] = {}
        fake_model = _FakeModel()
        original_import_module = importlib.import_module

        class FakeProcessorClass:
            @classmethod
            def from_pretrained(cls, model_name: str):
                captured["processor_model_name"] = model_name
                return _FakeProcessor()

        class FakeModelClass:
            @classmethod
            def from_pretrained(cls, model_name: str, *, torch_dtype):
                captured["model_name"] = model_name
                captured["torch_dtype"] = torch_dtype
                return fake_model

        fake_torch = type(
            "FakeTorch", (), {"bfloat16": "bfloat16", "float32": "float32"}
        )
        fake_transformers = type(
            "FakeTransformers",
            (),
            {
                "AutoProcessor": FakeProcessorClass,
                "AutoModelForImageTextToText": FakeModelClass,
            },
        )

        def fake_import_module(name: str):
            if name == "torch":
                return fake_torch
            if name == "transformers":
                return fake_transformers
            return original_import_module(name)

        monkeypatch.setattr(
            "vrc_live_caption.local_translation.translategemma.server.importlib.import_module",
            fake_import_module,
        )

        bundle = TranslateGemmaModelBundle.load(
            config=TranslateGemmaLocalServiceConfig(),
            runtime=ResolvedTranslateGemmaRuntime(
                device_policy="auto",
                resolved_device="cuda:0",
                resolved_dtype="bfloat16",
                torch_dtype="bfloat16",
                cuda_available=True,
            ),
            logger=logging.getLogger("test.local_translation.load"),
        )

        assert captured["processor_model_name"] == "google/translategemma-4b-it"
        assert captured["model_name"] == "google/translategemma-4b-it"
        assert captured["torch_dtype"] == "bfloat16"
        assert fake_model.device == "cuda:0"
        assert fake_model.eval_called is True
        assert isinstance(bundle, TranslateGemmaModelBundle)


class TestTranslateGemmaModelBundleTranslate:
    def test_when_text_is_translated__then_it_uses_the_official_chat_template(
        self,
    ) -> None:
        fake_processor = _FakeProcessor()
        fake_model = _FakeModel()
        fake_torch = type(
            "FakeTorch",
            (),
            {"inference_mode": staticmethod(nullcontext)},
        )
        bundle = TranslateGemmaModelBundle(
            processor=fake_processor,
            model=fake_model,
            torch_module=fake_torch,
            runtime=ResolvedTranslateGemmaRuntime(
                device_policy="auto",
                resolved_device="cuda:0",
                resolved_dtype="bfloat16",
                torch_dtype="bfloat16",
                cuda_available=True,
            ),
            max_new_tokens=128,
        )

        translated = bundle.translate("你好世界", "zh", "en")

        assert translated == "translated text"
        assert fake_processor.messages == [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "source_lang_code": "zh",
                        "target_lang_code": "en",
                        "text": "你好世界",
                    }
                ],
            }
        ]
        assert fake_model.generate_calls[0]["max_new_tokens"] == 128
        assert fake_processor.decoded == {
            "tokens": [30, 40],
            "skip_special_tokens": True,
        }


class TestResolveTranslateGemmaRuntime:
    def test_when_auto_uses_cuda__then_it_resolves_bfloat16(self) -> None:
        fake_torch = type(
            "FakeTorch",
            (),
            {
                "bfloat16": "bfloat16",
                "float32": "float32",
                "cuda": type(
                    "FakeCuda",
                    (),
                    {
                        "is_available": staticmethod(lambda: True),
                        "device_count": staticmethod(lambda: 1),
                    },
                )(),
            },
        )

        runtime = resolve_translategemma_runtime(
            device_policy="auto",
            dtype_policy="auto",
            torch_module=fake_torch,
        )

        assert runtime.resolved_device == "cuda:0"
        assert runtime.resolved_dtype == "bfloat16"
        assert runtime.torch_dtype == "bfloat16"

    def test_when_auto_uses_cpu__then_it_resolves_float32(self) -> None:
        fake_torch = type(
            "FakeTorch",
            (),
            {
                "bfloat16": "bfloat16",
                "float32": "float32",
                "cuda": type(
                    "FakeCuda",
                    (),
                    {
                        "is_available": staticmethod(lambda: False),
                    },
                )(),
            },
        )

        runtime = resolve_translategemma_runtime(
            device_policy="auto",
            dtype_policy="auto",
            torch_module=fake_torch,
        )

        assert runtime.resolved_device == "cpu"
        assert runtime.resolved_dtype == "float32"
        assert runtime.torch_dtype == "float32"

    def test_when_cuda_is_requested_without_gpu__then_it_raises(self) -> None:
        fake_torch = type(
            "FakeTorch",
            (),
            {
                "bfloat16": "bfloat16",
                "float32": "float32",
                "cuda": type(
                    "FakeCuda",
                    (),
                    {
                        "is_available": staticmethod(lambda: False),
                    },
                )(),
            },
        )

        with pytest.raises(TranslationError, match="local-cu130 extra"):
            resolve_translategemma_runtime(
                device_policy="cuda",
                dtype_policy="auto",
                torch_module=fake_torch,
            )


@pytest.mark.asyncio
class TestRunTranslateGemmaLocalServer:
    async def test_when_server_starts__then_it_reports_ready_details(
        self,
        monkeypatch,
    ) -> None:
        ready_events: list[TranslateGemmaLocalServerReadyInfo] = []
        serve_context = FakeServeContext()

        monkeypatch.setattr(
            "vrc_live_caption.local_translation.translategemma.server._load_torch_module",
            lambda: type(
                "FakeTorch",
                (),
                {
                    "bfloat16": "bfloat16",
                    "float32": "float32",
                    "cuda": type(
                        "FakeCuda",
                        (),
                        {
                            "is_available": staticmethod(lambda: True),
                            "device_count": staticmethod(lambda: 1),
                        },
                    )(),
                },
            )(),
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_translation.translategemma.server.TranslateGemmaModelBundle.load",
            lambda **kwargs: object(),
        )
        monkeypatch.setattr(
            "vrc_live_caption.local_translation.translategemma.server.serve",
            serve_context,
        )

        await run_translategemma_local_server(
            config=TranslateGemmaLocalServiceConfig(),
            host="127.0.0.1",
            port=10096,
            logger=logging.getLogger("test.local_translation.server.run"),
            ready_callback=ready_events.append,
        )

        assert serve_context.host == "127.0.0.1"
        assert serve_context.port == 10096
        assert serve_context.ping_interval is None
        assert ready_events[0] == TranslateGemmaLocalServerReadyInfo(
            endpoint="ws://127.0.0.1:10096",
            model="google/translategemma-4b-it",
            resolved_device="cuda:0",
            device_policy="auto",
            resolved_dtype="bfloat16",
        )
