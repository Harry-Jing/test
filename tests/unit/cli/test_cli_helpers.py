import pytest

from vrc_live_caption import cli as cli_module
from vrc_live_caption.config import LoggingConfig


class TestApplyLoggingOverrides:
    def test_when_no_override_is_provided__then_it_returns_the_existing_config(
        self,
    ) -> None:
        config = LoggingConfig()

        assert (
            cli_module._apply_logging_overrides(
                config,
                console_log_level=None,
                file_log_level=None,
            )
            is config
        )


class TestParseCliBool:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("true", True, id="true"),
            pytest.param("YES", True, id="yes"),
            pytest.param("0", False, id="zero"),
            pytest.param("off", False, id="off"),
        ],
    )
    def test_when_value_is_supported__then_it_returns_the_boolean(
        self,
        raw: str,
        expected: bool,
    ) -> None:
        assert cli_module._parse_cli_bool(raw, context="--typing") is expected

    def test_when_value_is_invalid__then_it_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="--typing must be true or false"):
            cli_module._parse_cli_bool("maybe", context="--typing")


class TestConsumeCurrentTaskCancellation:
    def test_when_current_task_is_missing__then_it_returns_false(
        self,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr("vrc_live_caption.cli.asyncio.current_task", lambda: None)

        assert cli_module._consume_current_task_cancellation() is False

    def test_when_current_task_cannot_uncancel__then_it_returns_false(
        self,
        monkeypatch,
    ) -> None:
        class FakeTask:
            def cancelling(self) -> int:
                return 1

        monkeypatch.setattr(
            "vrc_live_caption.cli.asyncio.current_task",
            lambda: FakeTask(),
        )

        assert cli_module._consume_current_task_cancellation() is False

    def test_when_current_task_has_pending_cancellation__then_it_uncancels_it(
        self,
        monkeypatch,
    ) -> None:
        class FakeTask:
            def __init__(self) -> None:
                self.remaining = 2

            def cancelling(self) -> int:
                return self.remaining

            def uncancel(self) -> int:
                self.remaining -= 1
                return self.remaining

        task = FakeTask()
        monkeypatch.setattr("vrc_live_caption.cli.asyncio.current_task", lambda: task)

        assert cli_module._consume_current_task_cancellation() is True
        assert task.remaining == 0
