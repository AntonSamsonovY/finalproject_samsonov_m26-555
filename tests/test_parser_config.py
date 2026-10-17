"""Настройки источников и чтение ключей из окружения."""

import pytest

from valutatrade_hub.core.exceptions import CurrencyNotFoundError
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.config import ParserConfig


def test_defaults_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EXCHANGERATE_API_KEY", " example-key ")
    monkeypatch.setenv("COINGECKO_DEMO_API_KEY", " demo-key ")
    config = ParserConfig(tmp_path)
    assert config.exchangerate_api_key == "example-key"
    assert config.coingecko_api_key == "demo-key"
    assert config.rates_file == tmp_path / "rates.json"
    assert config.history_file == tmp_path / "exchange_rates.json"
    assert config.timeout == 10
    assert config.base_currency == "USD"
    assert config.crypto_ids == {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
    assert config.fiat_currencies == ["EUR", "GBP", "RUB"]
    assert "example-key" not in repr(config)
    assert "demo-key" not in repr(config)


def test_missing_keys_are_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("EXCHANGERATE_API_KEY", raising=False)
    monkeypatch.delenv("COINGECKO_DEMO_API_KEY", raising=False)
    config = ParserConfig(tmp_path)
    assert config.exchangerate_api_key == config.coingecko_api_key == ""


def test_data_path_from_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setenv("VALUTATRADE_DATA_DIR", str(tmp_path))
    assert ParserConfig().rates_file == tmp_path / "rates.json"


def test_custom_lists_are_copied_and_normalized(tmp_path):
    fiat = ["eur", "EUR", "usd"]
    crypto = {"btc": " bitcoin "}
    config = ParserConfig(
        tmp_path, fiat_currencies=fiat, crypto_ids=crypto, timeout=2.5
    )
    assert config.fiat_currencies == ["EUR", "USD"]
    assert config.crypto_ids == {"BTC": "bitcoin"}
    assert config.timeout == 2.5
    fiat.clear()
    crypto.clear()
    assert config.fiat_currencies == ["EUR", "USD"]
    assert config.crypto_ids == {"BTC": "bitcoin"}
    assert ParserConfig(tmp_path).fiat_currencies == ["EUR", "GBP", "RUB"]


@pytest.mark.parametrize("timeout", [0, -1, True, "10", float("inf"), float("nan")])
def test_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError):
        ParserConfig(tmp_path, timeout=timeout)


@pytest.mark.parametrize(
    "options",
    [
        {"fiat_currencies": "EUR"},
        {"fiat_currencies": ["BTC"]},
        {"crypto_ids": ["BTC"]},
        {"crypto_ids": {"EUR": "euro"}},
        {"crypto_ids": {"BTC": " "}},
        {"crypto_ids": {"BTC": None}},
        {"crypto_ids": {"btc": "bitcoin", "BTC": "bitcoin"}},
        {"crypto_ids": {"BTC": "bitcoin", "ETH": "bitcoin"}},
    ],
)
def test_invalid_currency_config(tmp_path, options):
    with pytest.raises(ValueError):
        ParserConfig(tmp_path, **options)


def test_unknown_currency_config(tmp_path):
    with pytest.raises(CurrencyNotFoundError):
        ParserConfig(tmp_path, fiat_currencies=["XYZ"])
