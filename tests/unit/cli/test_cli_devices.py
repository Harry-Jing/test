from tests.support.fakes.audio import FakeAudioBackend
from vrc_live_caption.audio import AudioDeviceInfo
from vrc_live_caption.cli import app


class TestDevicesCommand:
    def test_when_input_devices_exist__then_it_lists_them(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        backend = FakeAudioBackend(
            devices=[
                AudioDeviceInfo(
                    index=4,
                    name="USB Mic",
                    max_input_channels=1,
                    default_sample_rate=48_000.0,
                    is_default=True,
                )
            ]
        )
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_audio_backend", lambda: backend
        )

        result = cli_runner.invoke(app, ["devices"])

        assert result.exit_code == 0
        assert "USB Mic" in result.output
        assert "*" in result.output

    def test_when_backend_listing_fails__then_it_exits_non_zero(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_audio_backend",
            lambda: FakeAudioBackend(list_error="backend down"),
        )

        result = cli_runner.invoke(app, ["devices"])

        assert result.exit_code == 1
        assert "backend down" in result.output

    def test_when_no_input_device_exists__then_it_exits_non_zero(
        self,
        monkeypatch,
        cli_runner,
    ) -> None:
        class EmptyAudioBackend:
            def list_input_devices(self):
                return []

        monkeypatch.setattr(
            "vrc_live_caption.cli.create_audio_backend",
            lambda: EmptyAudioBackend(),
        )

        result = cli_runner.invoke(app, ["devices"])

        assert result.exit_code == 1
        assert "No input audio devices found." in result.output
