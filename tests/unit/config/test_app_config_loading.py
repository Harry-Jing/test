from pathlib import Path

import pytest

from vrc_live_caption.config import AppConfig, LogLevel, parse_device_value


class TestAppConfigLoading:
    def test_when_optional_config_file_is_missing__then_it_returns_defaults(
        self,
        tmp_path: Path,
    ) -> None:
        config = AppConfig.from_toml_file(tmp_path / "missing.toml", required=False)

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

    def test_when_config_file_contains_custom_values__then_it_parses_them(
        self,
        tmp_path: Path,
    ) -> None:
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


class TestParseDeviceValue:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param(None, None, id="none"),
            pytest.param("default", None, id="default-lower"),
            pytest.param(" Default ", None, id="default-trimmed"),
            pytest.param(3, 3, id="int-index"),
            pytest.param("USB Microphone", "USB Microphone", id="device-name"),
        ],
    )
    def test_when_value_is_supported__then_it_normalizes_it(
        self,
        raw,
        expected,
    ) -> None:
        assert parse_device_value(raw) == expected
