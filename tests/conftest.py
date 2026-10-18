"""Изоляция файлов журнала и настроек логгера между тестами."""

import logging

import pytest


@pytest.fixture(autouse=True)
def isolate_logging(tmp_path, monkeypatch):
    monkeypatch.setenv("VALUTATRADE_LOGS_DIR", str(tmp_path / "logs"))
    logger = logging.getLogger("valutatrade")
    handlers = logger.handlers[:]
    level, propagate = logger.level, logger.propagate
    yield
    for handler in logger.handlers[:]:
        if handler not in handlers:
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(level)
    logger.propagate = propagate
