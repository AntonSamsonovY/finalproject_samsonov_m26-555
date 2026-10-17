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
