"""Получение курсов криптовалют и обычных валют через HTTP."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from urllib.parse import quote

import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import validate_amount
from valutatrade_hub.parser_service.config import ParserConfig


class BaseApiClient(ABC):
    """Общие проверки HTTP-ответа без вывода ключей и адресов с секретами."""

    source = "API"

    def __init__(self, config=None, clock=None):
        self.config = config if config is not None else ParserConfig()
        self.clock = clock or (lambda: datetime.now(UTC))

    def _request(self, url, *, params=None, headers=None):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=self.config.timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            raise ApiRequestError(
                f"{self.source}: время ожидания истекло. Повторите позже"
            ) from None
        except requests.RequestException:
            # Текст исключения Requests может содержать ключ из URL.
            raise ApiRequestError(
                f"{self.source}: не удалось подключиться. Проверьте сеть"
            ) from None

        try:
            status = response.status_code
            if status in (401, 403):
                raise ApiRequestError(
                    f"{self.source}: HTTP {status}. Проверьте ключ и доступ к API"
                )
            if status == 429:
                raise ApiRequestError(
                    f"{self.source}: HTTP 429, превышен лимит запросов. Повторите позже"
                )
            if status != 200:
                raise ApiRequestError(f"{self.source}: HTTP {status}. Повторите позже")
            try:
                data = response.json()
            except ValueError:
                raise ApiRequestError(
                    f"{self.source}: ответ не является JSON"
                ) from None
        finally:
            response.close()
        if not isinstance(data, dict):
            raise ApiRequestError(f"{self.source}: неверная структура ответа")
        return data

    def _make_quote(self, rate, provider_time, received_at):
        rate = validate_amount(rate)
        timestamp = validate_amount(provider_time)
        updated = datetime.fromtimestamp(timestamp, UTC)
        if (updated - received_at).total_seconds() > 60:
            raise ValueError("У источника указано время из будущего.")
        # TTL считает время с получения ответа, а дату самого курса храним отдельно.
        return {
            "rate": rate,
            "updated_at": received_at.isoformat(),
            "source": self.source,
            "meta": {"provider_updated_at": updated.isoformat()},
        }

    @abstractmethod
    def fetch_rates(self):
        """Вернуть словарь пар CODE_USD с курсами и временем получения."""


class CoinGeckoClient(BaseApiClient):
    """Цена BTC, ETH и SOL в долларах по ID CoinGecko."""

    source = "CoinGecko"

    def fetch_rates(self):
        """Получить все настроенные криптовалюты одним запросом."""
        if not self.config.crypto_ids:
            return {}
        headers = {}
        if self.config.coingecko_api_key:
            headers["x-cg-demo-api-key"] = self.config.coingecko_api_key
        data = self._request(
            self.config.coingecko_url,
            params={
                "ids": ",".join(self.config.crypto_ids.values()),
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            },
            headers=headers,
        )
        received_at = self.clock()
        result = {}
        try:
            for code, coin_id in self.config.crypto_ids.items():
                entry = data[coin_id]
                result[f"{code}_USD"] = self._make_quote(
                    entry["usd"], entry["last_updated_at"], received_at
                )
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ApiRequestError(
                "CoinGecko: неполные или некорректные курсы"
            ) from None
        return result


class ExchangeRateApiClient(BaseApiClient):
    """Курсы обычных валют, приведённые к направлению CODE→USD."""

    source = "ExchangeRate-API"

    def fetch_rates(self):
        """Запросить latest/USD и обратить курсы валют к доллару."""
        if not self.config.fiat_currencies:
            return {}
        if not self.config.exchangerate_api_key:
            raise ApiRequestError("ExchangeRate-API: задайте EXCHANGERATE_API_KEY")
        key = quote(self.config.exchangerate_api_key, safe="")
        data = self._request(f"{self.config.exchangerate_url}/{key}/latest/USD")
        if data.get("result") != "success":
            error_type = data.get("error-type")
            if not isinstance(error_type, str):
                error_type = "unknown"
            reason = {
                "invalid-key": "неверный API-ключ",
                "inactive-account": "подтвердите аккаунт сервиса",
                "quota-reached": "исчерпан лимит запросов, повторите позже",
                "unsupported-code": "сервис не поддерживает базовую валюту",
            }.get(error_type, "сервис вернул ошибку")
            raise ApiRequestError(f"ExchangeRate-API: {reason}")

        received_at = self.clock()
        result = {}
        try:
            if data["base_code"] != "USD":
                raise ValueError("Ответ должен содержать курсы к USD.")
            rates = data["conversion_rates"]
            if validate_amount(rates["USD"]) != 1:
                raise ValueError("Курс USD к USD должен быть равен 1.")
            for code in self.config.fiat_currencies:
                # latest/USD возвращает USD→EUR, а нам нужен EUR→USD.
                rate = 1.0 / validate_amount(rates[code])
                result[f"{code}_USD"] = self._make_quote(
                    rate, data["time_last_update_unix"], received_at
                )
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ApiRequestError(
                "ExchangeRate-API: неполные или некорректные курсы"
            ) from None
        return result
