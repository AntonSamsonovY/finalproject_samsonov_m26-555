"""Проверки кэша, срока актуальности и направления курсов."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    StorageError,
)
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.infra.database import save_json
from valutatrade_hub.infra.settings import SettingsLoader

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def save_rates(folder, pairs, timestamp=None):
    timestamp = timestamp or NOW
    save_json(
        folder / "rates.json",
        {
            "pairs": {
                pair: {
                    "rate": rate,
                    "updated_at": timestamp.isoformat(),
                    "source": "Test",
                }
                for pair, rate in pairs.items()
            },
            "last_refresh": timestamp.isoformat(),
        },
    )


def service(folder, **kwargs):
    return RateService(folder, ttl=300, clock=lambda: NOW, **kwargs)


def test_direct_inverse_and_cross_rates(tmp_path):
    save_rates(tmp_path, {"BTC_USD": 10000, "EUR_USD": 2})
    rates = service(tmp_path)
    assert rates.get_rate("btc", "usd")["rate"] == 10000
    assert rates.get_rate("USD", "BTC")["rate"] == 0.0001
    assert rates.get_rate("BTC", "EUR")["rate"] == 5000
    assert rates.get_rate("EUR", "BTC")["rate"] == 0.0002


def test_identity_needs_no_cache(tmp_path):
    quote = service(tmp_path).get_rate("EUR", "EUR")
    assert quote["rate"] == 1
    assert not (tmp_path / "rates.json").exists()


def test_cross_rate_with_reverse_usd_pair(tmp_path):
    save_rates(tmp_path, {"USD_EUR": 0.5, "BTC_USD": 10000})
    assert service(tmp_path).get_rate("BTC", "EUR")["rate"] == 5000


def test_cross_rate_uses_older_timestamp(tmp_path):
    older = NOW - timedelta(seconds=100)
    save_rates(tmp_path, {"BTC_USD": 10000}, older)
    data = {
        "pairs": {
            "BTC_USD": {
                "rate": 10000,
                "updated_at": older.isoformat(),
                "source": "Crypto",
            },
            "EUR_USD": {"rate": 2, "updated_at": NOW.isoformat(), "source": "Fiat"},
        }
    }
    save_json(tmp_path / "rates.json", data)
    quote = service(tmp_path).get_rate("BTC", "EUR")
    assert quote["updated_at"] == older.isoformat()
    assert quote["source"] == "Crypto, Fiat"


@pytest.mark.parametrize("seconds", [300, 301, 3600])
def test_expired_cache(tmp_path, seconds):
    save_rates(tmp_path, {"BTC_USD": 10000}, NOW - timedelta(seconds=seconds))
    with pytest.raises(ApiRequestError, match="устарел"):
        service(tmp_path).get_rate("BTC", "USD")


def test_cache_just_before_ttl(tmp_path):
    save_rates(tmp_path, {"BTC_USD": 10000}, NOW - timedelta(seconds=299))
    assert service(tmp_path).get_rate("BTC", "USD")["rate"] == 10000


def test_future_timestamp_is_rejected(tmp_path):
    save_rates(tmp_path, {"BTC_USD": 10000}, NOW + timedelta(minutes=2))
    with pytest.raises(ApiRequestError, match="будущего"):
        service(tmp_path).get_rate("BTC", "USD")


def test_missing_cache(tmp_path):
    with pytest.raises(ApiRequestError, match="отсутствует"):
        service(tmp_path).get_rate("BTC", "USD")


@pytest.mark.parametrize("value", [0, -1, True, "100"])
def test_bad_rate_is_not_used(tmp_path, value):
    save_rates(tmp_path, {"BTC_USD": value})
    with pytest.raises(StorageError):
        service(tmp_path).get_rate("BTC", "USD")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_number_in_external_file(tmp_path, value):
    # Файл, испорченный извне: обычное сохранение такие числа уже запрещает.
    text = (
        '{"pairs":{"BTC_USD":{"rate":'
        + value
        + ',"updated_at":"2026-10-17T12:00:00Z","source":"Test"}}}'
    )
    (tmp_path / "rates.json").write_text(text, encoding="utf-8")
    with pytest.raises(StorageError):
        service(tmp_path).get_rate("BTC", "USD")


@pytest.mark.parametrize("timestamp", ["bad", "2026-10-17T12:00:00", None])
def test_bad_timestamp(tmp_path, timestamp):
    save_json(
        tmp_path / "rates.json",
        {
            "pairs": {
                "BTC_USD": {"rate": 10000, "updated_at": timestamp, "source": "Test"},
            }
        },
    )
    with pytest.raises(StorageError):
        service(tmp_path).get_rate("BTC", "USD")


@pytest.mark.parametrize("data", [[], {}, {"pairs": []}, {"pairs": {"BTC_USD": None}}])
def test_bad_cache_structure(tmp_path, data):
    save_json(tmp_path / "rates.json", data)
    with pytest.raises(StorageError):
        service(tmp_path).get_rate("BTC", "USD")


def test_broken_json_is_preserved(tmp_path):
    path = tmp_path / "rates.json"
    path.write_text("{broken", encoding="utf-8")
    refresh = Mock()
    with pytest.raises(StorageError):
        service(tmp_path, refresh=refresh).get_rate("BTC", "USD")
    refresh.assert_not_called()
    assert path.read_text() == "{broken"


def test_refresh_happens_once(tmp_path):
    refresh = Mock(side_effect=lambda: save_rates(tmp_path, {"BTC_USD": 10000}))
    rates = service(tmp_path, refresh=refresh)
    assert rates.get_rate("BTC", "USD")["rate"] == 10000
    assert rates.get_rate("BTC", "USD")["rate"] == 10000
    refresh.assert_called_once()


def test_refresh_without_result_is_not_repeated(tmp_path):
    refresh = Mock()
    with pytest.raises(ApiRequestError):
        service(tmp_path, refresh=refresh).get_rate("BTC", "USD")
    refresh.assert_called_once()


def test_cache_only_mode_does_not_refresh(tmp_path):
    refresh = Mock()
    with pytest.raises(ApiRequestError):
        service(tmp_path, refresh=refresh).get_rate("BTC", "USD", allow_refresh=False)
    refresh.assert_not_called()


def test_unknown_currency_does_not_refresh(tmp_path):
    refresh = Mock()
    with pytest.raises(CurrencyNotFoundError):
        service(tmp_path, refresh=refresh).get_rate("ABC", "USD")
    refresh.assert_not_called()


def test_settings_are_used(tmp_path, monkeypatch):
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    config = tmp_path / "pyproject.toml"
    config.write_text("[tool.valutatrade]\nrates_ttl_seconds = 60\n", encoding="utf-8")
    monkeypatch.setenv("VALUTATRADE_DATA_DIR", str(tmp_path / "custom"))
    SettingsLoader(config)
    rates = RateService()
    assert rates.path == tmp_path / "custom" / "rates.json"
    assert rates.ttl == 60


@pytest.mark.parametrize("ttl", [0, -1, True, "300"])
def test_invalid_ttl(tmp_path, ttl):
    with pytest.raises(ValueError):
        RateService(tmp_path, ttl=ttl)


def test_list_all_rates_without_refresh(tmp_path):
    save_rates(
        tmp_path, {"BTC_USD": 10000, "EUR_USD": 2, "ETH_USD": 1000, "SOL_USD": 10}
    )
    refresh = Mock()
    info = service(tmp_path, refresh=refresh).list_rates()
    assert info["base"] == "USD"
    assert [row["currency"] for row in info["rates"]] == ["BTC", "ETH", "EUR", "SOL"]
    assert all(not row["stale"] for row in info["rates"])
    refresh.assert_not_called()


def test_list_currency_and_base_filters(tmp_path):
    save_rates(tmp_path, {"BTC_USD": 10000, "EUR_USD": 2})
    info = service(tmp_path).list_rates(currency="btc", base="eur")
    assert info["base"] == "EUR"
    assert len(info["rates"]) == 1
    assert info["rates"][0]["currency"] == "BTC"
    assert info["rates"][0]["rate"] == 5000


def test_list_top_only_crypto_not_expensive_fiat(tmp_path):
    save_rates(
        tmp_path, {"EUR_USD": 1000000, "ETH_USD": 1000, "BTC_USD": 10000, "SOL_USD": 10}
    )
    info = service(tmp_path).list_rates(top=2)
    assert [row["currency"] for row in info["rates"]] == ["BTC", "ETH"]


def test_combined_currency_and_top_filters(tmp_path):
    save_rates(tmp_path, {"BTC_USD": 10000, "EUR_USD": 2})
    assert service(tmp_path).list_rates(currency="EUR", top=1)["rates"] == []
    assert (
        service(tmp_path).list_rates(currency="BTC", top=5)["rates"][0]["currency"]
        == "BTC"
    )


def test_top_order_is_stable_for_equal_rates(tmp_path):
    save_rates(tmp_path, {"SOL_USD": 10, "ETH_USD": 10, "BTC_USD": 10})
    assert [
        row["currency"] for row in service(tmp_path).list_rates(top=2)["rates"]
    ] == ["BTC", "ETH"]


def test_list_inverse_rate_for_another_base(tmp_path):
    save_rates(tmp_path, {"EUR_USD": 2})
    rows = service(tmp_path).list_rates(base="EUR")["rates"]
    assert len(rows) == 1
    assert rows[0]["currency"] == "USD"
    assert rows[0]["rate"] == 0.5


def test_list_empty_or_missing_currency_does_not_fetch(tmp_path):
    refresh = Mock()
    rates = service(tmp_path, refresh=refresh)
    assert rates.list_rates()["rates"] == []
    save_rates(tmp_path, {"BTC_USD": 10000})
    assert rates.list_rates(currency="ETH")["rates"] == []
    refresh.assert_not_called()


def test_list_stale_cross_rate_is_marked_not_used_for_trade(tmp_path):
    save_rates(tmp_path, {"BTC_USD": 10000, "EUR_USD": 2}, NOW - timedelta(seconds=300))
    rates = service(tmp_path)
    row = rates.list_rates(currency="BTC", base="EUR")["rates"][0]
    assert row["rate"] == 5000
    assert row["stale"]
    with pytest.raises(ApiRequestError, match="устарел"):
        rates.get_rate("BTC", "EUR")


@pytest.mark.parametrize("top", [0, -1, True, "1", 1.5])
def test_list_bad_top(tmp_path, top):
    with pytest.raises(ValueError, match="top"):
        service(tmp_path).list_rates(top=top)


@pytest.mark.parametrize("options", [{"currency": "XYZ"}, {"base": "XYZ"}])
def test_list_unknown_currency(tmp_path, options):
    with pytest.raises(CurrencyNotFoundError):
        service(tmp_path).list_rates(**options)


@pytest.mark.parametrize("pair", ["bad", "XYZ_USD", "btc_USD"])
def test_list_invalid_pair_in_cache(tmp_path, pair):
    save_rates(tmp_path, {pair: 1})
    with pytest.raises(StorageError):
        service(tmp_path).list_rates()


def test_list_future_quote_is_not_displayed_as_fresh(tmp_path):
    save_rates(tmp_path, {"BTC_USD": 1}, NOW + timedelta(hours=1))
    with pytest.raises(ApiRequestError, match="будущего"):
        service(tmp_path).list_rates()


def test_extreme_timezone_is_storage_error(tmp_path):
    save_json(
        tmp_path / "rates.json",
        {
            "pairs": {
                "BTC_USD": {
                    "rate": 1,
                    "source": "Test",
                    "updated_at": "0001-01-01T00:00:00+03:00",
                }
            }
        },
    )
    with pytest.raises(StorageError):
        service(tmp_path).get_rate("BTC", "USD")


def test_refresh_failure_explains_provider_error(tmp_path):
    refresh = Mock(
        return_value={"errors": {"exchangerate": "Задайте EXCHANGERATE_API_KEY"}}
    )
    with pytest.raises(ApiRequestError, match="EXCHANGERATE_API_KEY"):
        service(tmp_path, refresh=refresh).get_rate("EUR", "USD")
    refresh.assert_called_once()
