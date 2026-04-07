from vrc_live_caption.config import (
    AppConfig,
    GoogleCloudTranslationProviderConfig,
    SttConfig,
    TranslationChatboxLayoutConfig,
    TranslationConfig,
)


class TestAppConfigModelDefaults:
    def test_when_app_config_is_instantiated_twice__then_nested_models_are_not_shared(
        self,
    ) -> None:
        first = AppConfig()
        second = AppConfig()

        assert first.capture is not second.capture
        assert first.pipeline is not second.pipeline
        assert first.logging is not second.logging
        assert first.debug is not second.debug
        assert first.osc is not second.osc
        assert first.stt is not second.stt

    def test_when_stt_config_is_instantiated_twice__then_nested_models_are_not_shared(
        self,
    ) -> None:
        first = SttConfig()
        second = SttConfig()

        assert first.retry is not second.retry
        assert first.providers is not second.providers
        assert first.providers.funasr_local is not second.providers.funasr_local
        assert first.providers.iflytek_rtasr is not second.providers.iflytek_rtasr
        assert first.providers.openai_realtime is not second.providers.openai_realtime


class TestTranslationConfigModels:
    def test_when_translation_is_disabled__then_deepl_defaults_remain_valid(
        self,
    ) -> None:
        config = TranslationConfig()

        assert config.enabled is False
        assert config.provider == "deepl"
        assert config.chatbox_layout == TranslationChatboxLayoutConfig()

    def test_when_google_provider_config_is_created__then_location_defaults_to_global(
        self,
    ) -> None:
        config = GoogleCloudTranslationProviderConfig(project_id="test-project")

        assert config.location == "global"
