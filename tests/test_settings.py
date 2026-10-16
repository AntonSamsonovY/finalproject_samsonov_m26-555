"""Проверки загрузки настроек и единственного экземпляра."""

import pytest

from valutatrade_hub.infra.settings import SettingsLoader


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.delenv("VALUTATRADE_DATA_DIR", raising=False)
    monkeypatch.delenv("VALUTATRADE_LOGS_DIR", raising=False)


def test_default_settings(tmp_path):
    settings = SettingsLoader(tmp_path / "pyproject.toml")
    assert settings.get("data_dir") == tmp_path / "data"
    assert settings.get("logs_dir") == tmp_path / "logs"
    assert settings.get("rates_ttl_seconds") == 300
    assert settings.get("default_base_currency") == "USD"
    assert settings.get("unknown", "default") == "default"
    assert SettingsLoader() is settings


def test_settings_from_toml(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[tool.valutatrade]\ndata_dir = "custom"\nrates_ttl_seconds = 60\n'
        'default_base_currency = "EUR"\nlog_format = "json"\n',
        encoding="utf-8",
    )
    settings = SettingsLoader(path)
    assert settings.get("data_dir") == tmp_path / "custom"
    assert settings.get("rates_ttl_seconds") == 60
    assert settings.get("default_base_currency") == "EUR"
    assert settings.get("log_format") == "json"


def test_paths_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("VALUTATRADE_DATA_DIR", str(tmp_path / "other_data"))
    monkeypatch.setenv("VALUTATRADE_LOGS_DIR", str(tmp_path / "other_logs"))
    settings = SettingsLoader(tmp_path / "pyproject.toml")
    assert settings.get("data_dir") == tmp_path / "other_data"
    assert settings.get("logs_dir") == tmp_path / "other_logs"


@pytest.mark.parametrize(
    "content",
    [
        "[broken",
        "[tool.valutatrade]\nrates_ttl_seconds = 0",
        "[tool.valutatrade]\nrates_ttl_seconds = true",
        '[tool.valutatrade]\nlog_format = "unknown"',
        "[tool.valutatrade]\ndata_dir = 123",
    ],
)
def test_bad_settings(tmp_path, content):
    path = tmp_path / "pyproject.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        SettingsLoader(path)
    assert SettingsLoader._instance is None
