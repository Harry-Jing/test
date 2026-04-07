from pathlib import Path

import pytest

from vrc_live_caption.config import (
    AppConfig,
    ConfigError,
    FunasrLocalProviderConfig,
    IflytekRtasrProviderConfig,
    OscConfig,
    SttConfig,
    TranslationChatboxLayoutConfig,
    TranslationConfig,
)


class TestAppConfigTomlValidation:
    def test_when_capture_section_contains_unknown_keys__then_it_raises_config_error(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = tmp_path / "bad.toml"
        config_path.write_text("[capture]\nunknown = 1\n", encoding="utf-8")

        with pytest.raises(ConfigError, match="Unknown keys in capture"):
            AppConfig.from_toml_file(config_path)

    def test_when_root_config_contains_unknown_keys__then_it_raises_config_error(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = tmp_path / "bad-root.toml"
        config_path.write_text("unknown = 1\n", encoding="utf-8")

        with pytest.raises(ConfigError, match="Unknown keys in root config: unknown"):
            AppConfig.from_toml_file(config_path)

    def test_when_stt_backoff_range_is_invalid__then_it_raises_config_error(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = tmp_path / "bad-stt.toml"
        config_path.write_text(
            "\n".join(
                [
                    "[stt.retry]",
                    "initial_backoff_seconds = 6.0",
                    "max_backoff_seconds = 5.0",
                ]
            ),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="initial_backoff_seconds must be <="):
            AppConfig.from_toml_file(config_path)

    def test_when_legacy_logging_level_key_is_present__then_it_raises_config_error(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = tmp_path / "legacy-logging.toml"
        config_path.write_text('[logging]\nlevel = "INFO"\n', encoding="utf-8")

        with pytest.raises(ConfigError, match="Unknown keys in logging: level"):
            AppConfig.from_toml_file(config_path)

    def test_when_logging_level_value_is_invalid__then_it_raises_config_error(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = tmp_path / "invalid-logging.toml"
        config_path.write_text(
            '[logging]\nconsole_level = "verbose"\n',
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="logging.console_level must be one of"):
            AppConfig.from_toml_file(config_path)

    def test_when_legacy_translation_width_key_is_present__then_it_raises_config_error(
        self,
        tmp_path: Path,
    ) -> None:
        config_path = tmp_path / "legacy-width.toml"
        config_path.write_text(
            "\n".join(
                [
                    "[translation.chatbox_layout]",
                    'mode = "stacked_two_zone"',
                    "source_visible_lines = 4",
                    "separator_blank_lines = 1",
                    "target_visible_lines = 4",
                    "visual_line_width_units = 29.0",
                ]
            ),
            encoding="utf-8",
        )

        with pytest.raises(
            ConfigError,
            match="Unknown keys in translation.chatbox_layout: visual_line_width_units",
        ):
            AppConfig.from_toml_file(config_path)


class TestTranslationConfigValidation:
    def test_when_translation_is_enabled_without_target_language__then_it_raises_value_error(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="translation.target_language is required"):
            TranslationConfig(enabled=True)

    def test_when_google_translation_is_enabled_without_project_id__then_it_raises_value_error(
        self,
    ) -> None:
        with pytest.raises(
            ValueError,
            match="translation.providers.google_cloud.project_id is required",
        ):
            TranslationConfig(
                enabled=True, provider="google_cloud", target_language="en"
            )

    def test_when_output_mode_is_invalid__then_it_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="translation.output_mode must be one of"):
            TranslationConfig(output_mode="dual")

    def test_when_chatbox_layout_mode_is_invalid__then_it_raises_value_error(
        self,
    ) -> None:
        with pytest.raises(
            ValueError,
            match="translation.chatbox_layout.mode must be one of",
        ):
            TranslationConfig(
                chatbox_layout=TranslationChatboxLayoutConfig(mode="grid")
            )

    def test_when_chatbox_layout_exceeds_nine_lines__then_it_raises_value_error(
        self,
    ) -> None:
        with pytest.raises(
            ValueError,
            match="source_visible_lines \\+ separator_blank_lines \\+ target_visible_lines must be <= 9",
        ):
            TranslationConfig(
                chatbox_layout=TranslationChatboxLayoutConfig(
                    source_visible_lines=5,
                    separator_blank_lines=1,
                    target_visible_lines=4,
                )
            )


class TestProviderConfigValidation:
    def test_when_openai_provider_is_selected__then_stt_config_accepts_it(self) -> None:
        config = SttConfig(provider="openai_realtime")

        assert config.provider == "openai_realtime"

    def test_when_funasr_local_provider_is_selected__then_stt_config_accepts_it(
        self,
    ) -> None:
        config = SttConfig(provider="funasr_local")

        assert config.provider == "funasr_local"

    def test_when_funasr_local_port_is_out_of_range__then_it_raises_value_error(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="funasr_local.port must be <= 65535"):
            FunasrLocalProviderConfig(port=70_000)

    def test_when_iflytek_vad_mode_is_invalid__then_it_raises_value_error(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="stt.providers.iflytek_rtasr.vad_mode"):
            IflytekRtasrProviderConfig(vad_mode="unsupported")

    def test_when_osc_port_is_out_of_range__then_it_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="osc.port must be <= 65535"):
            OscConfig(port=70_000)
