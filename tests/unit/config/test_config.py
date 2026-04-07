from pathlib import Path

import pytest

from vrc_live_caption.config import (
    AppConfig,
    ConfigError,
    FunasrLocalProviderConfig,
    GoogleCloudTranslationProviderConfig,
    IflytekRtasrProviderConfig,
    LogLevel,
    OscConfig,
    SttConfig,
    TranslationChatboxLayoutConfig,
    TranslationConfig,
    parse_device_value,
)


def test_load_app_config_returns_defaults_when_optional_file_is_missing(
    tmp_path: Path,
) -> None:
    config = AppConfig.from_toml_file(tmp_path / "missing.toml", required=False)

    assert isinstance(config, AppConfig)
    assert config.capture.sample_rate == 16_000
    assert config.pipeline.audio_buffer_max_chunks == 50
    assert config.logging.file_path == Path(".runtime/logs/vrc-live-caption.log")
    assert config.osc.host == "127.0.0.1"
    assert config.osc.port == 9000
    assert config.osc.notification_sfx is False
    assert config.stt.provider == "openai_realtime"
    assert config.stt.providers.funasr_local.host == "127.0.0.1"
    assert config.stt.providers.funasr_local.port == 10095
    assert config.stt.providers.iflytek_rtasr.language == "autodialect"
    assert config.stt.providers.iflytek_rtasr.vad_mode == "near_field"
    assert config.stt.providers.openai_realtime.model == "gpt-4o-transcribe"
    assert config.translation.enabled is False
    assert config.translation.provider == "deepl"
    assert config.translation.output_mode == "source_target"
    assert config.translation.chatbox_layout.mode == "stacked_two_zone"
    assert config.translation.chatbox_layout.source_visible_lines == 4
    assert config.translation.chatbox_layout.separator_blank_lines == 1
    assert config.translation.chatbox_layout.target_visible_lines == 4


def test_load_app_config_rejects_unknown_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text("[capture]\nunknown = 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Unknown keys in capture"):
        AppConfig.from_toml_file(config_path)


def test_load_app_config_rejects_unknown_root_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "bad-root.toml"
    config_path.write_text("unknown = 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Unknown keys in root config: unknown"):
        AppConfig.from_toml_file(config_path)


def test_load_app_config_parses_custom_values(tmp_path: Path) -> None:
    config_path = tmp_path / "custom.toml"
    config_path.write_text(
        "\n".join(
            [
                "[capture]",
                'device = "USB Mic"',
                "sample_rate = 22050",
                "channels = 1",
                'dtype = "int16"',
                "block_duration_ms = 40",
                "",
                "[pipeline]",
                "audio_buffer_max_chunks = 8",
                "event_buffer_max_items = 32",
                "shutdown_timeout_seconds = 6.5",
                "heartbeat_seconds = 2",
                "",
                "[logging]",
                'console_level = "warning"',
                'file_level = "debug"',
                'file_path = ".runtime/logs/custom.log"',
                "",
                "[debug]",
                'runtime_dir = ".runtime"',
                'recordings_dir = ".runtime/recordings"',
                "probe_seconds = 0.5",
                "",
                "[osc]",
                'host = "192.168.1.9"',
                "port = 9011",
                "notification_sfx = true",
                "",
                "[stt]",
                'provider = "iflytek_rtasr"',
                "",
                "[stt.retry]",
                "connect_timeout_seconds = 12.5",
                "max_attempts = 4",
                "initial_backoff_seconds = 1.5",
                "max_backoff_seconds = 6.0",
                "",
                "[stt.providers.funasr_local]",
                'host = "127.0.0.1"',
                "port = 10096",
                "use_ssl = false",
                "",
                "[stt.providers.iflytek_rtasr]",
                'language = "autominor"',
                'vad_mode = "far_field"',
                'domain = "tech"',
                "",
                "[stt.providers.openai_realtime]",
                'model = "gpt-4o-transcribe"',
                'language = "zh"',
                'prompt = "technology words expected"',
                'noise_reduction = "far_field"',
                'turn_detection = "server_vad"',
                "vad_prefix_padding_ms = 450",
                "vad_silence_duration_ms = 700",
                "vad_threshold = 0.4",
                "",
                "[translation]",
                "enabled = true",
                'provider = "google_cloud"',
                'target_language = "en"',
                'source_language = "zh"',
                'output_mode = "source_target"',
                'strategy = "final_only"',
                "request_timeout_seconds = 4.5",
                "max_pending_finals = 3",
                "",
                "[translation.chatbox_layout]",
                'mode = "stacked_two_zone"',
                "source_visible_lines = 3",
                "separator_blank_lines = 1",
                "target_visible_lines = 4",
                "",
                "[translation.providers.google_cloud]",
                'project_id = "test-project"',
                'location = "global"',
            ]
        ),
        encoding="utf-8",
    )

    config = AppConfig.from_toml_file(config_path)

    assert config.capture.device == "USB Mic"
    assert config.capture.frames_per_chunk == 882
    assert config.pipeline.audio_buffer_max_chunks == 8
    assert config.pipeline.event_buffer_max_items == 32
    assert config.logging.console_level == LogLevel.WARNING
    assert config.logging.file_level == LogLevel.DEBUG
    assert config.debug.probe_seconds == 0.5
    assert config.osc.host == "192.168.1.9"
    assert config.osc.port == 9011
    assert config.osc.notification_sfx is True
    assert config.stt.retry.connect_timeout_seconds == 12.5
    assert config.stt.retry.max_attempts == 4
    assert config.stt.providers.funasr_local.port == 10096
    assert config.stt.providers.iflytek_rtasr.language == "autominor"
    assert config.stt.providers.iflytek_rtasr.vad_mode == "far_field"
    assert config.stt.providers.iflytek_rtasr.domain == "tech"
    assert config.stt.providers.openai_realtime.model == "gpt-4o-transcribe"
    assert config.stt.providers.openai_realtime.language == "zh"
    assert config.stt.providers.openai_realtime.noise_reduction == "far_field"
    assert config.translation.enabled is True
    assert config.translation.provider == "google_cloud"
    assert config.translation.target_language == "en"
    assert config.translation.chatbox_layout.source_visible_lines == 3
    assert config.translation.providers.google_cloud.project_id == "test-project"


def test_load_app_config_rejects_invalid_stt_backoff_range(tmp_path: Path) -> None:
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


def test_load_app_config_rejects_legacy_logging_level_key(tmp_path: Path) -> None:
    config_path = tmp_path / "legacy-logging.toml"
    config_path.write_text(
        "\n".join(
            [
                "[logging]",
                'level = "INFO"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Unknown keys in logging: level"):
        AppConfig.from_toml_file(config_path)


def test_load_app_config_rejects_invalid_logging_level_value(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid-logging.toml"
    config_path.write_text(
        "\n".join(
            [
                "[logging]",
                'console_level = "verbose"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="logging.console_level must be one of"):
        AppConfig.from_toml_file(config_path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("default", None),
        (" Default ", None),
        (3, 3),
        ("USB Microphone", "USB Microphone"),
    ],
)
def test_parse_device_value_normalizes_supported_values(raw, expected) -> None:
    assert parse_device_value(raw) == expected


def test_app_config_default_models_do_not_share_nested_instances() -> None:
    first = AppConfig()
    second = AppConfig()

    assert first.capture is not second.capture
    assert first.pipeline is not second.pipeline
    assert first.logging is not second.logging
    assert first.debug is not second.debug
    assert first.osc is not second.osc
    assert first.stt is not second.stt


def test_stt_config_default_models_do_not_share_nested_instances() -> None:
    first = SttConfig()
    second = SttConfig()

    assert first.retry is not second.retry
    assert first.providers is not second.providers
    assert first.providers.funasr_local is not second.providers.funasr_local
    assert first.providers.iflytek_rtasr is not second.providers.iflytek_rtasr
    assert first.providers.openai_realtime is not second.providers.openai_realtime


def test_translation_config_requires_target_language_when_enabled() -> None:
    with pytest.raises(ValueError, match="translation.target_language is required"):
        TranslationConfig(enabled=True)


def test_translation_config_requires_google_project_id_when_enabled() -> None:
    with pytest.raises(
        ValueError,
        match="translation.providers.google_cloud.project_id is required",
    ):
        TranslationConfig(enabled=True, provider="google_cloud", target_language="en")


def test_translation_config_accepts_deepl_defaults_when_disabled() -> None:
    config = TranslationConfig()

    assert config.enabled is False
    assert config.provider == "deepl"
    assert config.chatbox_layout == TranslationChatboxLayoutConfig()


def test_translation_config_rejects_invalid_output_mode() -> None:
    with pytest.raises(ValueError, match="translation.output_mode must be one of"):
        TranslationConfig(output_mode="dual")


def test_translation_config_rejects_invalid_chatbox_layout_mode() -> None:
    with pytest.raises(
        ValueError,
        match="translation.chatbox_layout.mode must be one of",
    ):
        TranslationConfig(chatbox_layout=TranslationChatboxLayoutConfig(mode="grid"))


def test_translation_config_rejects_chatbox_layout_that_exceeds_nine_lines() -> None:
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


def test_translation_config_rejects_legacy_visual_width_keys(tmp_path: Path) -> None:
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


def test_google_cloud_translation_provider_config_defaults_location() -> None:
    config = GoogleCloudTranslationProviderConfig(project_id="test-project")

    assert config.location == "global"


def test_stt_config_accepts_openai_provider_selection() -> None:
    config = SttConfig(provider="openai_realtime")

    assert config.provider == "openai_realtime"


def test_stt_config_accepts_funasr_local_provider_selection() -> None:
    config = SttConfig(provider="funasr_local")

    assert config.provider == "funasr_local"


def test_funasr_local_provider_config_rejects_out_of_range_port() -> None:
    with pytest.raises(ValueError, match="funasr_local.port must be <= 65535"):
        FunasrLocalProviderConfig(port=70_000)


def test_iflytek_rtasr_config_rejects_invalid_vad_mode() -> None:
    with pytest.raises(ValueError, match="stt.providers.iflytek_rtasr.vad_mode"):
        IflytekRtasrProviderConfig(vad_mode="unsupported")


def test_osc_config_rejects_out_of_range_port() -> None:
    with pytest.raises(ValueError, match="osc.port must be <= 65535"):
        OscConfig(port=70_000)
