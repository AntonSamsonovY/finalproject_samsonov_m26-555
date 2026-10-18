"""Настройки из pyproject.toml и пути к рабочим файлам."""

import os
import tomllib
from pathlib import Path

from valutatrade_hub.core.currencies import get_currency


class SettingsLoader:
    """Один набор настроек на приложение."""

    _instance = None

    def __new__(cls, config_path=None):
        # Для одного экземпляра достаточно __new__, метакласс здесь не нужен.
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._load(Path(config_path or "pyproject.toml").resolve())
            cls._instance = instance
        return cls._instance

    def _load(self, path):
        config = {}
        if path.exists():
            try:
                with path.open("rb") as file:
                    config = tomllib.load(file).get("tool", {}).get("valutatrade", {})
            except (OSError, ValueError, AttributeError):
                raise ValueError("Не удалось прочитать настройки проекта.") from None
        if not isinstance(config, dict):
            raise ValueError("Настройки tool.valutatrade должны быть таблицей TOML.")

        ttl = config.get("rates_ttl_seconds", 300)
        if type(ttl) is not int or ttl <= 0:
            raise ValueError("TTL курсов должен быть положительным целым числом.")
        log_format = config.get("log_format", "text")
        if log_format not in ("text", "json"):
            raise ValueError("Формат логов должен быть text или json.")
        log_level = config.get("log_level", "INFO")
        if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError(
                "Уровень логов должен быть DEBUG, INFO, WARNING или ERROR."
            )
        log_max_bytes = config.get("log_max_bytes", 1048576)
        log_backup_count = config.get("log_backup_count", 3)
        for value in (log_max_bytes, log_backup_count):
            if type(value) is not int or value <= 0:
                raise ValueError(
                    "Размер журнала и число архивов должны быть целыми больше нуля."
                )
        base = get_currency(config.get("default_base_currency", "USD")).code

        self._settings = {
            "rates_ttl_seconds": ttl,
            "default_base_currency": base,
            "log_format": log_format,
            "log_level": log_level,
            "log_max_bytes": log_max_bytes,
            "log_backup_count": log_backup_count,
        }
        for key, env_name, default in (
            ("data_dir", "VALUTATRADE_DATA_DIR", "data"),
            ("logs_dir", "VALUTATRADE_LOGS_DIR", "logs"),
        ):
            value = os.getenv(env_name) or config.get(key, default)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Необходимо указать путь {key}.")
            folder = Path(value)
            self._settings[key] = (
                folder if folder.is_absolute() else path.parent / folder
            )

    def get(self, key, default=None):
        """Вернуть настройку или значение по умолчанию."""
        return self._settings.get(key, default)
