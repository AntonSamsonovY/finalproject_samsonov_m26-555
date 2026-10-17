"""Покупка, продажа и оценка портфеля без обращения к реальным API."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    AuthenticationError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    StorageError,
)
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import AuthService, TradingService
from valutatrade_hub.infra.database import DatabaseManager, save_json


@pytest.fixture
def services(tmp_path):
    now = datetime.now(UTC)
    save_json(
        tmp_path / "rates.json",
        {
            "pairs": {
                code: {"rate": rate, "updated_at": now.isoformat(), "source": "Test"}
                for code, rate in {"BTC_USD": 60000, "EUR_USD": 1.2}.items()
            },
            "last_refresh": now.isoformat(),
        },
    )
    database = DatabaseManager(tmp_path)
    auth = AuthService(database)
    auth.register("anton", "test-password")
    auth.login("anton", "test-password")
    rates = RateService(tmp_path, 300, clock=lambda: now)
    return database, auth, TradingService(database, auth, rates)


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir()}


def test_buy_sell_and_reload(services, tmp_path):
    database, _, trading = services
    result = trading.buy("btc", 0.1)
    assert result["currency"] == "BTC"
    assert result["balance"] == 0.1
    assert result["rate"] == 60000
    assert result["estimated_cost"] == 6000
    assert result["base"] == "USD"
    trading.buy("BTC", 0.2)
    result = trading.sell("btc", 0.1)
    assert result["balance"] == pytest.approx(0.2)
    assert result["estimated_cost"] == 6000

    auth = AuthService(DatabaseManager(tmp_path))
    auth.login("anton", "test-password")
    restarted = TradingService(database, auth, RateService(tmp_path, 300))
    assert restarted.show_portfolio()["total"] == pytest.approx(12000)
    wallets = database.load()["portfolios"][0]["wallets"]
    assert set(wallets) == {"BTC"}  # USD не списывается и не начисляется отдельно.
    assert wallets["BTC"]["balance"] == pytest.approx(0.2)


def test_empty_portfolio_does_not_need_rates(services, tmp_path):
    _, _, trading = services
    (tmp_path / "rates.json").unlink()
    assert trading.show_portfolio("eur") == {
        "username": "anton",
        "base": "EUR",
        "wallets": [],
        "total": 0.0,
    }


def test_portfolio_conversion(services):
    _, _, trading = services
    trading.buy("BTC", 0.1)
    trading.buy("EUR", 100)
    trading.buy("USD", 30)
    info = trading.show_portfolio("eur")
    assert info["base"] == "EUR"
    assert info["total"] == pytest.approx(5125)
    assert [row["currency"] for row in info["wallets"]] == ["BTC", "EUR", "USD"]
    assert [row["value"] for row in info["wallets"]] == pytest.approx([5000, 100, 25])


@pytest.mark.parametrize("operation", ["buy", "sell", "show_portfolio"])
def test_trading_requires_login(services, operation, tmp_path):
    _, auth, trading = services
    auth.logout()
    before = snapshot(tmp_path)
    args = () if operation == "show_portfolio" else ("BTC", 1)
    with pytest.raises(AuthenticationError):
        getattr(trading, operation)(*args)
    assert snapshot(tmp_path) == before


def test_get_rate_does_not_require_login(services):
    _, auth, trading = services
    auth.logout()
    quote = trading.get_rate("btc", "eur")
    assert quote["from"] == "BTC"
    assert quote["to"] == "EUR"
    assert quote["rate"] == pytest.approx(50000)


@pytest.mark.parametrize("amount", [0, -1, True, "1", None, float("nan"), float("inf")])
@pytest.mark.parametrize("operation", ["buy", "sell"])
def test_invalid_amount_does_not_change_files(services, tmp_path, amount, operation):
    _, _, trading = services
    trading.buy("BTC", 1)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        getattr(trading, operation)("BTC", amount)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("operation", ["buy", "sell", "show_portfolio", "get_rate"])
def test_unknown_currency_does_not_change_files(services, tmp_path, operation):
    _, _, trading = services
    before = snapshot(tmp_path)
    args = {
        "buy": ("XYZ", 1),
        "sell": ("XYZ", 1),
        "show_portfolio": ("XYZ",),
        "get_rate": ("XYZ", "USD"),
    }
    with pytest.raises(CurrencyNotFoundError):
        getattr(trading, operation)(*args[operation])
    assert snapshot(tmp_path) == before


def test_sell_missing_wallet(services, tmp_path):
    _, _, trading = services
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="нет кошелька"):
        trading.sell("BTC", 1)
    assert snapshot(tmp_path) == before


def test_sell_insufficient_balance(services, tmp_path):
    _, _, trading = services
    trading.buy("BTC", 0.1)
    before = snapshot(tmp_path)
    with pytest.raises(InsufficientFundsError) as caught:
        trading.sell("BTC", 0.2)
    assert caught.value.available == 0.1
    assert caught.value.required == 0.2
    assert snapshot(tmp_path) == before


def test_sell_full_balance_and_show_zero_without_rate(services, tmp_path):
    _, _, trading = services
    trading.buy("BTC", 0.1)
    assert trading.sell("BTC", 0.1)["balance"] == 0
    (tmp_path / "rates.json").unlink()
    assert trading.show_portfolio()["wallets"] == [
        {"currency": "BTC", "balance": 0.0, "value": 0.0}
    ]


@pytest.mark.parametrize("operation", ["buy", "sell", "show_portfolio"])
@pytest.mark.parametrize("cache_state", ["missing", "stale", "broken"])
def test_rate_error_does_not_save_portfolio(services, tmp_path, operation, cache_state):
    _, _, trading = services
    trading.buy("BTC", 1)
    path = tmp_path / "rates.json"
    if cache_state == "missing":
        path.unlink()
    elif cache_state == "broken":
        path.write_text("{broken", encoding="utf-8")
    else:
        save_json(
            path,
            {
                "pairs": {
                    "BTC_USD": {
                        "rate": 60000,
                        "source": "Test",
                        "updated_at": (
                            datetime.now(UTC) - timedelta(hours=1)
                        ).isoformat(),
                    }
                }
            },
        )
    before = snapshot(tmp_path)
    error = StorageError if cache_state == "broken" else ApiRequestError
    args = () if operation == "show_portfolio" else ("BTC", 0.1)
    with pytest.raises(error):
        getattr(trading, operation)(*args)
    assert snapshot(tmp_path) == before


def test_user_portfolios_are_separate(services):
    _, auth, trading = services
    trading.buy("BTC", 1)
    auth.register("ivan", "other-password")
    auth.login("ivan", "other-password")
    assert trading.show_portfolio()["wallets"] == []
    trading.buy("EUR", 10)
    auth.login("anton", "test-password")
    assert trading.show_portfolio()["wallets"] == [
        {"currency": "BTC", "balance": 1.0, "value": 60000.0}
    ]


def test_save_failure_does_not_report_success_or_change_balance(services, tmp_path):
    database, _, trading = services
    trading.buy("BTC", 1)
    before = snapshot(tmp_path)
    with patch.object(database, "save", side_effect=StorageError("test failure")):
        with pytest.raises(StorageError):
            trading.sell("BTC", 0.5)
    assert snapshot(tmp_path) == before
    assert trading.show_portfolio()["wallets"][0]["balance"] == 1


def test_overflow_does_not_save_trade(services, tmp_path):
    _, _, trading = services
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        trading.buy("BTC", 1e308)
    assert snapshot(tmp_path) == before


def test_portfolio_total_overflow(services):
    database, _, trading = services
    data = database.load()
    data["portfolios"][0]["wallets"] = {
        "USD": {"balance": 1e308},
        "EUR": {"balance": 1e308},
    }
    database.save(data)
    with pytest.raises(ValueError):
        trading.show_portfolio()
