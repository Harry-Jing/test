import asyncio
from types import SimpleNamespace

from vrc_live_caption.env import DeepLCredentials
from vrc_live_caption.translation import TranslationRequest, TranslationResult


class FakeDeepLSecrets:
    def __init__(self) -> None:
        self.deepl_calls = 0

    def require_deepl_credentials(self) -> DeepLCredentials:
        self.deepl_calls += 1
        return DeepLCredentials(auth_key="deepl-key")


class FakeDeepLClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def translate_text(
        self,
        text: str,
        *,
        source_lang: str | None,
        target_lang: str,
    ) -> object:
        self.calls.append(
            {
                "text": text,
                "source_lang": source_lang,
                "target_lang": target_lang,
            }
        )
        return SimpleNamespace(text="translated")


class SlowTranslationBackend:
    name = "slow"

    def __init__(self) -> None:
        self.requests: list[TranslationRequest] = []
        self.release = asyncio.Event()

    def describe(self) -> str:
        return "slow"

    def validate_environment(self) -> None:
        return None

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self.requests.append(request)
        await self.release.wait()
        return TranslationResult(
            utterance_id=request.utterance_id,
            revision=request.revision,
            source_text=request.text,
            translated_text=f"translated:{request.text}",
        )
