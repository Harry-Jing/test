from tests.support.fakes.osc import FakeOscTransport
from vrc_live_caption.cli import app
from vrc_live_caption.errors import OscError


class TestOscTestCommand:
    def test_when_text_and_typing_are_sent__then_it_emits_both(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        osc_transport = FakeOscTransport()
        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_osc_chatbox_transport",
            lambda *, app_config, logger: osc_transport,
        )

        result = cli_runner.invoke(
            app,
            [
                "osc-test",
                "hello world",
                "--config",
                str(config_path),
                "--typing",
                "true",
            ],
        )

        assert result.exit_code == 0
        assert osc_transport.typing_messages == [True]
        assert osc_transport.text_messages == ["hello world"]
        assert "Sent chatbox text to 127.0.0.1:9000" in result.output
        assert "[chatbox] hello world" in result.output

    def test_when_typing_value_is_invalid__then_it_exits_non_zero(
        self,
        config_file_factory,
        cli_runner,
    ) -> None:
        config_path = config_file_factory()

        result = cli_runner.invoke(
            app,
            [
                "osc-test",
                "hello world",
                "--config",
                str(config_path),
                "--typing",
                "maybe",
            ],
        )

        assert result.exit_code == 1
        assert "--typing must be true or false" in result.output

    def test_when_osc_transport_raises_vrc_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        class BrokenOscTransport(FakeOscTransport):
            def send_text(self, text: str) -> None:
                raise OscError("osc send failed")

        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_osc_chatbox_transport",
            lambda *, app_config, logger: BrokenOscTransport(),
        )

        result = cli_runner.invoke(app, ["osc-test", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "osc send failed" in result.output

    def test_when_osc_transport_raises_unexpected_error__then_it_exits_non_zero(
        self,
        monkeypatch,
        config_file_factory,
        cli_runner,
    ) -> None:
        class BrokenOscTransport(FakeOscTransport):
            def send_text(self, text: str) -> None:
                raise RuntimeError("unexpected osc failure")

        config_path = config_file_factory()
        monkeypatch.setattr(
            "vrc_live_caption.cli.create_osc_chatbox_transport",
            lambda *, app_config, logger: BrokenOscTransport(),
        )

        result = cli_runner.invoke(app, ["osc-test", "--config", str(config_path)])

        assert result.exit_code == 1
        assert "unexpected osc failure" in result.output
