"""Проверки кошельков пользователя и общей стоимости портфеля."""

import json

import pytest

from valutatrade_hub.core.exceptions import CurrencyNotFoundError
from valutatrade_hub.core.models import Portfolio, User, Wallet


@pytest.fixture
def user():
    return User.create(1, "anton", "test-password")


def test_empty_portfolio(user):
    portfolio = Portfolio(user)
    assert portfolio.user is user
    assert portfolio.user_id == user.user_id
    assert portfolio.wallets == {}
    assert portfolio.get_total_value() == 0


def test_wallet_is_not_created_twice(user):
    portfolio = Portfolio(user)
    wallet = portfolio.add_currency("usd")
    wallet.deposit(100)
    assert portfolio.add_currency("USD") is wallet
    assert portfolio.get_wallet("usd").balance == 100
    assert len(portfolio.wallets) == 1


def test_wallet_dictionary_is_a_copy(user):
    wallet = Wallet("USD", 100)
    original = {"USD": wallet}
    portfolio = Portfolio(user, original)
    original.clear()
    portfolio.wallets.clear()
    assert portfolio.get_wallet("USD") is wallet


def test_owner_is_read_only(user):
    portfolio = Portfolio(user)
    with pytest.raises(AttributeError):
        portfolio.user = User.create(2, "ivan", "test-password")
    with pytest.raises(AttributeError):
        portfolio.user_id = 2


def test_unknown_and_missing_wallets(user):
    portfolio = Portfolio(user)
    with pytest.raises(CurrencyNotFoundError):
        portfolio.add_currency("ABC")
    with pytest.raises(ValueError, match="нет кошелька"):
        portfolio.get_wallet("USD")
    assert portfolio.wallets == {}


def test_total_in_different_currencies(user):
    portfolio = Portfolio(user)
    portfolio.add_currency("USD").deposit(100)
    portfolio.add_currency("EUR").deposit(50)
    portfolio.add_currency("BTC").deposit(0.01)
    rates = {"EUR_USD": 2, "BTC_USD": 10000}
    assert portfolio.get_total_value(exchange_rates=rates) == 300
    assert portfolio.get_total_value("EUR", rates) == 150
    assert portfolio.get_total_value("BTC", rates) == pytest.approx(0.03)


def test_same_currency_does_not_need_a_rate(user):
    portfolio = Portfolio(user)
    portfolio.add_currency("EUR").deposit(100)
    assert portfolio.get_total_value("EUR") == 100


def test_empty_wallet_does_not_need_a_rate(user):
    portfolio = Portfolio(user)
    portfolio.add_currency("BTC")
    portfolio.add_currency("USD").deposit(100)
    assert portfolio.get_total_value() == 100


def test_missing_rate_is_reported(user):
    portfolio = Portfolio(user)
    portfolio.add_currency("EUR").deposit(10)
    with pytest.raises(ValueError, match="Нет курса EUR"):
        portfolio.get_total_value()


@pytest.mark.parametrize("rate", [0, -1, True, "2", float("nan"), float("inf")])
def test_invalid_rate(user, rate):
    portfolio = Portfolio(user)
    portfolio.add_currency("EUR").deposit(10)
    with pytest.raises(ValueError):
        portfolio.get_total_value(exchange_rates={"EUR_USD": rate})
    assert portfolio.get_wallet("EUR").balance == 10


def test_overflow_in_total_is_rejected(user):
    portfolio = Portfolio(user)
    portfolio.add_currency("EUR").deposit(1e308)
    with pytest.raises(ValueError):
        portfolio.get_total_value(exchange_rates={"EUR_USD": 2})


def test_json_roundtrip(user):
    portfolio = Portfolio(user)
    portfolio.add_currency("BTC").deposit(0.05)
    restored = Portfolio.from_dict(json.loads(json.dumps(portfolio.to_dict())), user)
    assert restored.to_dict() == portfolio.to_dict()
    assert restored.user is user
    assert restored.get_wallet("BTC").balance == 0.05


@pytest.mark.parametrize(
    "record",
    [
        {},
        [],
        {"user_id": 2, "wallets": {}},
        {"user_id": True, "wallets": {}},
        {"user_id": 1, "wallets": []},
        {"user_id": 1, "wallets": {"USD": {}}},
        {"user_id": 1, "wallets": {"USD": {"balance": -1}}},
        {"user_id": 1, "wallets": {"USD": {"balance": 1, "currency_code": "EUR"}}},
        {"user_id": 1, "wallets": {"usd": {"balance": 1}}},
    ],
)
def test_invalid_saved_portfolio(user, record):
    with pytest.raises(ValueError):
        Portfolio.from_dict(record, user)


def test_invalid_constructor_data(user):
    with pytest.raises(ValueError):
        Portfolio(1)
    with pytest.raises(ValueError):
        Portfolio(user, [])
    with pytest.raises(ValueError):
        Portfolio(user, {"USD": Wallet("EUR")})


def test_unknown_base_currency(user):
    with pytest.raises(CurrencyNotFoundError):
        Portfolio(user).get_total_value("ABC")
