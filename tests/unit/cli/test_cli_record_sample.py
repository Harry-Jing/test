from pathlib import Path

from tests.support.fakes.audio import FakeAudioBackend
from vrc_live_caption.cli import app
from vrc_live_caption.errors import AudioRuntimeError


class TestRecordSampleCommandHelp:
    def test_when_help_is_rendered__then_it_shows_the_recording_panel(
        self,
        cli_runner,
    ) -> None:
        result = cli_runner.invoke(app, ["record-sample", "--help"])

        assert result.exit_code == 0
        assert "Recording" in result.output


class TestRecordSampleCommand:
    def test_when_capture_succeeds__then_it_writes_the_output_file(
        self,
        monkeypatch,
        tmp_cwd: Path,
        config_file_factory,
        cli_runner,
    ) -> None:
        backend = FakeAudioBackend()
        config_path = config_file_factory()
        output_path = tmp_cwd / "sample.wav"
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_audio_backend", lambda: backend
        )

        result = cli_runner.invoke(
            app,
            [
                "record-sample",
                "--config",
                str(config_path),
                "--seconds",
                "0.1",
                "--output",
                str(output_path),
            ],
        )

        assert result.exit_code == 0
        assert output_path.exists()
        assert f"Config: {config_path}" in result.output
        assert f"Output WAV: {output_path}" in result.output
        assert "[info] Recording 0.10s sample..." in result.output
        assert f"Recorded sample: {output_path}" in result.output

    def test_when_audio_capture_fails_to_start__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_audio_backend",
            lambda: FakeAudioBackend(fail_on_start=True),
        )

        result = cli_runner.invoke(app, ["record-sample", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "Failed to start audio capture" in result.output

    def test_when_runtime_raises_vrc_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        async def fake_record_sample_command(**kwargs) -> None:
            raise AudioRuntimeError("recording failed")

        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli._run_record_sample_command",
            fake_record_sample_command,
        )

        result = cli_runner.invoke(app, ["record-sample", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "recording failed" in result.output

    def test_when_runtime_raises_unexpected_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        async def fake_record_sample_command(**kwargs) -> None:
            raise RuntimeError("unexpected recording failure")

        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli._run_record_sample_command",
            fake_record_sample_command,
        )

        result = cli_runner.invoke(app, ["record-sample", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "unexpected recording failure" in result.output
