"""Адреса сервисов, список валют и настройки запросов."""

import os
from pathlib import Path

from valutatrade_hub.core.currencies import CryptoCurrency, FiatCurrency, get_currency
from valutatrade_hub.core.utils import validate_amount
from valutatrade_hub.infra.settings import SettingsLoader


class ParserConfig:
    """Настройки парсера; ключи берутся только из окружения."""

    def __init__(
        self, data_dir=None, timeout=10, fiat_currencies=None, crypto_ids=None
    ):
        if data_dir is None:
            data_dir = SettingsLoader().get("data_dir")
        self.rates_file = Path(data_dir) / "rates.json"
        self.history_file = Path(data_dir) / "exchange_rates.json"
        self.timeout = validate_amount(timeout)
        self.base_currency = "USD"
        self.coingecko_url = "https://api.coingecko.com/api/v3/simple/price"
        self.exchangerate_url = "https://v6.exchangerate-api.com/v6"
        self.exchangerate_api_key = os.getenv("EXCHANGERATE_API_KEY", "").strip()
        self.coingecko_api_key = os.getenv("COINGECKO_DEMO_API_KEY", "").strip()

        if fiat_currencies is None:
            fiat_currencies = ["EUR", "GBP", "RUB"]
        if crypto_ids is None:
            crypto_ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
        if not isinstance(fiat_currencies, (list, tuple)):
            raise ValueError("Список обычных валют должен быть списком или кортежем.")
        if not isinstance(crypto_ids, dict):
            raise ValueError("Для криптовалют нужен словарь кодов и ID CoinGecko.")

        self.fiat_currencies = []
        for code in fiat_currencies:
            currency = get_currency(code)
            if not isinstance(currency, FiatCurrency):
                raise ValueError("В списке обычных валют указана криптовалюта.")
            if currency.code not in self.fiat_currencies:
                self.fiat_currencies.append(currency.code)

        self.crypto_ids = {}
        for code, coin_id in crypto_ids.items():
            currency = get_currency(code)
            if not isinstance(currency, CryptoCurrency):
                raise ValueError("В списке криптовалют указана обычная валюта.")
            if not isinstance(coin_id, str) or not coin_id.strip():
                raise ValueError("ID CoinGecko не может быть пустым.")
            coin_id = coin_id.strip()
            if currency.code in self.crypto_ids or coin_id in self.crypto_ids.values():
                raise ValueError("Коды и ID криптовалют не должны повторяться.")
            self.crypto_ids[currency.code] = coin_id
