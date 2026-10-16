"""Классы валют и поиск валюты по коду."""

import math
from abc import ABC, abstractmethod

from valutatrade_hub.core.exceptions import CurrencyNotFoundError


class Currency(ABC):
    """Общие данные валюты: название и код."""

    def __init__(self, name, code):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Название валюты не может быть пустым.")
        if not isinstance(code, str):
            raise ValueError("Код валюты должен быть строкой.")

        code = code.upper()
        if not (2 <= len(code) <= 5 and code.isascii() and code.isalpha()):
            raise ValueError("Код валюты должен содержать от 2 до 5 латинских букв.")

        self.name = name.strip()
        self.code = code

    @abstractmethod
    def get_display_info(self):
        """Вернуть описание валюты для вывода в консоль."""


class FiatCurrency(Currency):
    """Обычная валюта со страной или зоной выпуска."""

    def __init__(self, name, code, issuing_country):
        super().__init__(name, code)
        if not isinstance(issuing_country, str) or not issuing_country.strip():
            raise ValueError("Нужно указать страну или зону выпуска валюты.")
        self.issuing_country = issuing_country.strip()

    def get_display_info(self):
        """Показать название, код и страну выпуска."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """Криптовалюта с названием алгоритма и капитализацией."""

    def __init__(self, name, code, algorithm, market_cap=0.0):
        super().__init__(name, code)
        if not isinstance(algorithm, str) or not algorithm.strip():
            raise ValueError("Нужно указать алгоритм криптовалюты.")
        if type(market_cap) not in (int, float):
            raise ValueError("Капитализация должна быть числом.")

        try:
            market_cap = float(market_cap)
        except OverflowError:
            raise ValueError("Слишком большое значение капитализации.") from None

        if not math.isfinite(market_cap) or market_cap < 0:
            raise ValueError(
                "Капитализация должна быть конечным числом не меньше нуля."
            )

        self.algorithm = algorithm.strip()
        self.market_cap = market_cap

    def get_display_info(self):
        """Показать код, алгоритм и капитализацию в краткой записи."""
        return (
            f"[CRYPTO] {self.code} — {self.name} "
            f"(Algo: {self.algorithm}, MCAP: {self.market_cap:.2e})"
        )


# Пока храним только описания валют. Курсы будем получать отдельно.
_CURRENCIES = {
    "USD": FiatCurrency("US Dollar", "USD", "United States"),
    "EUR": FiatCurrency("Euro", "EUR", "Eurozone"),
    "GBP": FiatCurrency("Pound Sterling", "GBP", "United Kingdom"),
    "RUB": FiatCurrency("Russian Ruble", "RUB", "Russia"),
    "BTC": CryptoCurrency("Bitcoin", "BTC", "SHA-256"),
    "ETH": CryptoCurrency("Ethereum", "ETH", "Proof of Stake"),
    "SOL": CryptoCurrency("Solana", "SOL", "Proof of History / Proof of Stake"),
}


def get_currency(code):
    """Найти поддерживаемую валюту; регистр введённого кода не важен."""
    if not isinstance(code, str):
        raise ValueError("Код валюты должен быть строкой.")
    code = code.upper()
    if code not in _CURRENCIES:
        raise CurrencyNotFoundError(code)
    return _CURRENCIES[code]
