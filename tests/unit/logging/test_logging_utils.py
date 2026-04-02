import logging
from logging.handlers import RotatingFileHandler

from rich.logging import RichHandler

from vrc_live_caption.config import LoggingConfig, LogLevel
from vrc_live_caption.logging_utils import configure_logging


def test_configure_logging_creates_expected_handlers(tmp_path) -> None:
    logger = configure_logging(
        LoggingConfig(
            console_level=LogLevel.WARNING,
            file_level=LogLevel.DEBUG,
            file_path=tmp_path / "logs" / "app.log",
            max_bytes=256,
            backup_count=2,
        )
    )

    assert logger.level == logging.DEBUG
    assert logger.propagate is False
    assert len(logger.handlers) == 2
    console_handler = next(
        handler for handler in logger.handlers if isinstance(handler, RichHandler)
    )
    file_handler = next(
        handler
        for handler in logger.handlers
        if isinstance(handler, RotatingFileHandler)
    )

    assert console_handler.level == logging.WARNING
    assert file_handler.level == logging.DEBUG
    assert file_handler.formatter is not None
    assert file_handler.formatter._fmt is not None
    assert "%(threadName)s" in file_handler.formatter._fmt
    assert (tmp_path / "logs").exists()


def test_configure_logging_replaces_existing_handlers(tmp_path) -> None:
    first_logger = configure_logging(
        LoggingConfig(file_path=tmp_path / "logs" / "first.log")
    )
    first_handlers = list(first_logger.handlers)

    second_logger = configure_logging(
        LoggingConfig(file_path=tmp_path / "logs" / "second.log")
    )

    assert second_logger is first_logger
    assert len(second_logger.handlers) == 2
    assert all(handler not in second_logger.handlers for handler in first_handlers)


def test_configure_logging_uses_most_verbose_handler_level_for_parent(tmp_path) -> None:
    logger = configure_logging(
        LoggingConfig(
            console_level=LogLevel.ERROR,
            file_level=LogLevel.INFO,
            file_path=tmp_path / "logs" / "app.log",
        )
    )

    assert logger.level == logging.INFO
