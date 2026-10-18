"""Формат журнала и ограничение размера файлов."""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from urllib.parse import quote

from valutatrade_hub.core.exceptions import StorageError


class AppFormatter(logging.Formatter):
    """Одна строка на событие, время UTC и скрытые API-ключи."""

    def __init__(self, log_format="text"):
        super().__init__()
        self.log_format = log_format

    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat()
        event = getattr(record, "action_event", None)
        if event is None:
            event = {"message": record.getMessage()}
        if self.log_format == "json":
            data = {
                "timestamp": timestamp,
                "level": record.levelname,
                "logger": record.name,
                **event,
            }
            text = json.dumps(data, ensure_ascii=False, allow_nan=False)
        else:
            fields = " ".join(
                f"{key}={json.dumps(value, ensure_ascii=False, allow_nan=False)}"
                for key, value in event.items()
            )
            text = f"{timestamp} {record.levelname} {record.name} {fields}"
        for variable in ("EXCHANGERATE_API_KEY", "COINGECKO_DEMO_API_KEY"):
            secret = os.getenv(variable, "").strip()
            if secret:
                for value in (secret, quote(secret, safe="")):
                    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
                    text = text.replace(escaped, "[REDACTED]")
        return text


class AppFileHandler(RotatingFileHandler):
    """Не показывать содержимое записи и traceback при сбое самого журнала."""

    def handleError(self, record):
        print("Не удалось записать событие в журнал.", file=sys.stderr)


def configure_logging(settings):
    """Настроить журнал приложения, не меняя root-логгер и журналы библиотек."""
    path = settings.get("logs_dir") / "valutatrade.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = AppFileHandler(
            path,
            maxBytes=settings.get("log_max_bytes"),
            backupCount=settings.get("log_backup_count"),
            encoding="utf-8",
        )
    except OSError:
        raise StorageError("Не удалось открыть журнал приложения.") from None
    handler.setFormatter(AppFormatter(settings.get("log_format")))
    logger = logging.getLogger("valutatrade")
    for old_handler in logger.handlers[:]:
        if isinstance(old_handler, AppFileHandler):
            logger.removeHandler(old_handler)
            old_handler.close()
    logger.addHandler(handler)
    logger.setLevel(settings.get("log_level"))
    logger.propagate = False
    return logger
