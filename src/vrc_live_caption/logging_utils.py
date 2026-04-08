"""Configures the application logger and its console and file handlers.

Rebuilds handlers from config so per-command logging overrides apply cleanly.
"""

import logging
from logging.handlers import RotatingFileHandler

from rich.logging import RichHandler

from .config import LoggingConfig, LogLevel

APP_LOGGER_NAME = "vrc_live_caption"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the app logger or one of its named child loggers."""
    if not name:
        return logging.getLogger(APP_LOGGER_NAME)
    return logging.getLogger(f"{APP_LOGGER_NAME}.{name}")


def _python_log_level(level: LogLevel) -> int:
    return getattr(logging, level.value)


def configure_logging(config: LoggingConfig) -> logging.Logger:
    """Configure the application logger from the validated logging settings.

    Replace existing handlers so per-command overrides take effect immediately.
    """
    console_level = _python_log_level(config.console_level)
    file_level = _python_log_level(config.file_level)

    logger = get_logger()
    logger.setLevel(min(console_level, file_level))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    config.file_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(threadName)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = RichHandler(rich_tracebacks=True, show_path=False)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    file_handler = RotatingFileHandler(
        config.file_path,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger
