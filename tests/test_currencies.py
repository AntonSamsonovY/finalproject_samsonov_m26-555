"""Проверки классов валют и поиска по коду."""

import pytest

from valutatrade_hub.core.currencies import (
    CryptoCurrency,
    Currency,
    FiatCurrency,
    get_currency,
)
from valutatrade_hub.core.exceptions import CurrencyNotFoundError


def test_base_currency_is_abstract():
    with pytest.raises(TypeError):
        Currency("Test", "TT")


def test_fiat_currency():
    currency = FiatCurrency("US Dollar", "usd", "United States")
    assert currency.code == "USD"
    assert currency.get_display_info() == (
        "[FIAT] USD — US Dollar (Issuing: United States)"
    )


def test_crypto_currency():
    currency = CryptoCurrency("Bitcoin", "BTC", "SHA-256", 1.12e12)
    assert currency.market_cap == 1.12e12
    assert currency.get_display_info() == (
        "[CRYPTO] BTC — Bitcoin (Algo: SHA-256, MCAP: 1.12e+12)"
    )


@pytest.mark.parametrize(
    "code", ["", "A", "ABCDEF", "US D", " USD", "123", "РУБ", None]
)
def test_invalid_currency_code(code):
    with pytest.raises(ValueError):
        FiatCurrency("Test", code, "Country")


@pytest.mark.parametrize("name", ["", "  ", None])
def test_invalid_currency_name(name):
    with pytest.raises(ValueError):
        FiatCurrency(name, "TT", "Country")


@pytest.mark.parametrize("country", ["", "  ", None])
def test_invalid_country(country):
    with pytest.raises(ValueError):
        FiatCurrency("Test", "TT", country)


@pytest.mark.parametrize("algorithm", ["", "  ", None])
def test_invalid_algorithm(algorithm):
    with pytest.raises(ValueError):
        CryptoCurrency("Test", "TT", algorithm)


@pytest.mark.parametrize("value", [-1, True, "100", None, float("nan"), float("inf")])
def test_invalid_market_cap(value):
    with pytest.raises(ValueError):
        CryptoCurrency("Test", "TT", "Test", value)


def test_market_cap_overflow():
    with pytest.raises(ValueError):
        CryptoCurrency("Test", "TT", "Test", 10**1000)


def test_default_market_cap():
    assert CryptoCurrency("Test", "TT", "Test").market_cap == 0


def test_find_currency():
    assert isinstance(get_currency("usd"), FiatCurrency)
    assert isinstance(get_currency("BTC"), CryptoCurrency)
    for code in ("USD", "EUR", "GBP", "RUB", "BTC", "ETH", "SOL"):
        assert get_currency(code).code == code


@pytest.mark.parametrize("code", ["ABC", "", "US D"])
def test_unknown_currency(code):
    with pytest.raises(CurrencyNotFoundError, match="Неизвестная валюта"):
        get_currency(code)


def test_lookup_requires_string():
    with pytest.raises(ValueError):
        get_currency(123)
