"""Проверки баланса и операций с кошельком."""

import pytest

from valutatrade_hub.core.exceptions import (
    CurrencyNotFoundError,
    InsufficientFundsError,
)
from valutatrade_hub.core.models import Wallet


def test_new_wallet():
    wallet = Wallet("usd")
    assert wallet.currency_code == "USD"
    assert wallet.balance == 0
    assert wallet.to_dict() == {"currency_code": "USD", "balance": 0.0}


def test_deposit_and_withdraw():
    wallet = Wallet("USD", 100)
    wallet.deposit(50)
    wallet.withdraw(25)
    assert wallet.balance == 125
    assert wallet.get_balance_info() == "USD: 125.00000000"


def test_withdraw_all():
    wallet = Wallet("BTC", 0.05)
    wallet.withdraw(0.05)
    assert wallet.balance == 0


def test_insufficient_funds():
    wallet = Wallet("EUR", 10)
    with pytest.raises(InsufficientFundsError) as caught:
        wallet.withdraw(11)
    assert wallet.balance == 10
    assert caught.value.available == 10
    assert caught.value.required == 11
    assert caught.value.code == "EUR"
    assert "Недостаточно средств" in str(caught.value)


@pytest.mark.parametrize("balance", [-1, "10", True, None, float("nan"), float("inf")])
def test_invalid_starting_balance(balance):
    with pytest.raises(ValueError):
        Wallet("USD", balance)


@pytest.mark.parametrize("balance", [-1, "10", True, None, float("nan"), float("inf")])
def test_invalid_balance_does_not_change_wallet(balance):
    wallet = Wallet("USD", 100)
    with pytest.raises(ValueError):
        wallet.balance = balance
    assert wallet.balance == 100


@pytest.mark.parametrize("method", ["deposit", "withdraw"])
@pytest.mark.parametrize(
    "amount", [0, -1, "10", True, None, float("nan"), float("inf")]
)
def test_invalid_operation_does_not_change_wallet(method, amount):
    wallet = Wallet("USD", 100)
    with pytest.raises(ValueError):
        getattr(wallet, method)(amount)
    assert wallet.balance == 100


def test_overflow_does_not_change_balance():
    wallet = Wallet("USD", 1e308)
    with pytest.raises(ValueError):
        wallet.deposit(1e308)
    assert wallet.balance == 1e308
    with pytest.raises(ValueError):
        wallet.deposit(10**1000)
    assert wallet.balance == 1e308


def test_unknown_currency():
    with pytest.raises(CurrencyNotFoundError):
        Wallet("ABC")


def test_currency_is_read_only():
    wallet = Wallet("USD")
    with pytest.raises(AttributeError):
        wallet.currency_code = "EUR"


def test_small_crypto_amount():
    wallet = Wallet("BTC")
    wallet.deposit(0.00000001)
    assert wallet.balance == 0.00000001
    assert wallet.get_balance_info() == "BTC: 0.00000001"
