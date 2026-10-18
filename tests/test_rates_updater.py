"""Обновление от нескольких источников с частичными отказами."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from valutatrade_hub.core.exceptions import ApiRequestError, StorageError
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.parser_service.api_clients import (
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage
from valutatrade_hub.parser_service.updater import RatesUpdater

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def quote(rate, seconds=0):
    return {
        "rate": rate,
        "source": "Test",
        "updated_at": (NOW + timedelta(seconds=seconds)).isoformat(),
    }


@pytest.fixture
def setup(tmp_path):
    storage = RatesStorage(ParserConfig(tmp_path))
    clients = {
        "coingecko": Mock(fetch_rates=Mock(return_value={"BTC_USD": quote(60000)})),
        "exchangerate": Mock(fetch_rates=Mock(return_value={"EUR_USD": quote(1.2)})),
    }
    return storage, clients, RatesUpdater(clients, storage, clock=lambda: NOW)


def test_update_all_and_log_stages(setup, caplog):
    storage, clients, updater = setup
    with caplog.at_level("INFO", logger="valutatrade.parser"):
        result = updater.run_update()
    assert result == {
        "updated": 2,
        "history_added": 2,
        "skipped": 0,
        "cached": 2,
        "sources": ["coingecko", "exchangerate"],
        "errors": {},
    }
    assert len(storage.load()[1]) == 2
    for client in clients.values():
        client.fetch_rates.assert_called_once()
    assert "Запрос курсов: coingecko" in caplog.text
    assert "Обновлено курсов: 2" in caplog.text


@pytest.mark.parametrize("source", ["coingecko", "exchangerate"])
def test_selected_source_only(setup, source):
    _, clients, updater = setup
    result = updater.run_update(source)
    assert result["updated"] == 1
    assert result["sources"] == [source]
    for name, client in clients.items():
        assert client.fetch_rates.call_count == (1 if name == source else 0)


def test_unknown_source_does_not_send_requests(setup):
    _, clients, updater = setup
    with pytest.raises(ValueError):
        updater.run_update("unknown")
    for client in clients.values():
        client.fetch_rates.assert_not_called()


@pytest.mark.parametrize("failed", ["coingecko", "exchangerate"])
def test_partial_failure_preserves_old_pairs(setup, failed):
    storage, clients, updater = setup
    storage.save({"BTC_USD": quote(50000, -10), "EUR_USD": quote(1.1, -10)})
    clients[failed].fetch_rates.side_effect = ApiRequestError("test failure")
    result = updater.run_update()
    cache, history = storage.load()
    assert result["updated"] == 1
    assert list(result["errors"]) == [failed]
    assert set(cache["pairs"]) == {"BTC_USD", "EUR_USD"}
    assert len(history) == 3
    old_pair = "BTC_USD" if failed == "coingecko" else "EUR_USD"
    assert cache["pairs"][old_pair]["updated_at"] == quote(1, -10)["updated_at"]


def test_all_sources_failed_does_not_change_files(setup, tmp_path):
    storage, clients, updater = setup
    storage.save({"BTC_USD": quote(50000, -10)})
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    for client in clients.values():
        client.fetch_rates.side_effect = ApiRequestError("test failure")
    result = updater.run_update()
    assert result["updated"] == 0
    assert result["cached"] == 1
    assert len(result["errors"]) == 2
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


def test_second_update_does_not_duplicate_history(setup):
    storage, _, updater = setup
    updater.run_update()
    assert updater.run_update()["skipped"] == 2
    assert len(storage.load()[1]) == 2


@pytest.mark.parametrize(
    "invalid", [None, [], {}, {"BTC_USD": quote(-1)}, {"BTC_USD": quote(60000, 120)}]
)
def test_invalid_provider_batch_does_not_block_other_source(setup, invalid):
    storage, clients, updater = setup
    clients["coingecko"].fetch_rates.return_value = invalid
    result = updater.run_update()
    assert result["sources"] == ["exchangerate"]
    assert "coingecko" in result["errors"]
    assert set(storage.load()[0]["pairs"]) == {"EUR_USD"}


def test_newest_quote_wins_if_sources_overlap(setup):
    storage, clients, updater = setup
    clients["coingecko"].fetch_rates.return_value = {"BTC_USD": quote(60000)}
    clients["exchangerate"].fetch_rates.return_value = {"BTC_USD": quote(50000, -10)}
    updater.run_update()
    assert storage.load()[0]["pairs"]["BTC_USD"]["rate"] == 60000


def test_broken_storage_stops_before_requests(setup):
    storage, clients, updater = setup
    storage.config.history_file.write_text("{broken", encoding="utf-8")
    with pytest.raises(StorageError):
        updater.run_update()
    for client in clients.values():
        client.fetch_rates.assert_not_called()


def test_save_failure_is_not_reported_as_success(setup):
    storage, _, updater = setup
    with patch.object(storage, "save", side_effect=StorageError("test failure")):
        with pytest.raises(StorageError):
            updater.run_update()


def test_rate_service_can_use_updater_for_missing_cache(setup, tmp_path):
    _, clients, updater = setup
    rates = RateService(tmp_path, 300, refresh=updater.run_update, clock=lambda: NOW)
    assert rates.get_rate("BTC", "EUR")["rate"] == 50000
    assert rates.get_rate("EUR", "USD")["rate"] == 1.2
    for client in clients.values():
        client.fetch_rates.assert_called_once()


def test_http_clients_through_updater_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("EXCHANGERATE_API_KEY", "fake-key")
    config = ParserConfig(tmp_path)
    payloads = [
        {
            coin_id: {"usd": rate, "last_updated_at": int(NOW.timestamp())}
            for coin_id, rate in {
                "bitcoin": 60000,
                "ethereum": 3000,
                "solana": 150,
            }.items()
        },
        {
            "result": "success",
            "base_code": "USD",
            "time_last_update_unix": int(NOW.timestamp()),
            "conversion_rates": {"USD": 1, "EUR": 0.8, "GBP": 0.5, "RUB": 100},
        },
    ]
    response = Mock(status_code=200)
    response.json.side_effect = payloads
    monkeypatch.setattr("requests.get", Mock(return_value=response))
    storage = RatesStorage(config)
    updater = RatesUpdater(
        {
            "coingecko": CoinGeckoClient(config, clock=lambda: NOW),
            "exchangerate": ExchangeRateApiClient(config, clock=lambda: NOW),
        },
        storage,
        clock=lambda: NOW,
    )
    assert updater.run_update()["updated"] == 6
    cache, history = storage.load()
    assert len(cache["pairs"]) == len(history) == 6
    rates = RateService(tmp_path, 300, clock=lambda: NOW)
    assert rates.get_rate("BTC", "EUR")["rate"] == 48000
    assert all("provider_updated_at" in record["meta"] for record in history)
