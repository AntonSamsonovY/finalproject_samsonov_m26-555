"""Ответы внешних API подменяются: тестам не нужны сеть и настоящие ключи."""

import copy
import traceback
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.infra.database import save_json
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
PROVIDER_TIME = int((NOW - timedelta(minutes=5)).timestamp())


def crypto_response():
    return {
        coin_id: {"usd": price, "last_updated_at": PROVIDER_TIME}
        for coin_id, price in {
            "bitcoin": 60000,
            "ethereum": 3000,
            "solana": 150,
        }.items()
    }


def fiat_response():
    return {
        "result": "success",
        "base_code": "USD",
        "time_last_update_unix": PROVIDER_TIME,
        "conversion_rates": {"USD": 1, "EUR": 0.8, "GBP": 0.5, "RUB": 100},
    }


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("EXCHANGERATE_API_KEY", "fake-exchange-key")
    monkeypatch.setenv("COINGECKO_DEMO_API_KEY", "fake-demo-key")
    return ParserConfig(tmp_path)


@pytest.fixture
def http_get(monkeypatch):
    response = Mock(status_code=200)
    get = Mock(return_value=response)
    monkeypatch.setattr("requests.get", get)
    return get


def test_base_client_is_abstract():
    with pytest.raises(TypeError):
        BaseApiClient()


def test_crypto_request_and_quotes(config, http_get):
    payload = crypto_response()
    before = copy.deepcopy(payload)
    http_get.return_value.json.return_value = payload
    result = CoinGeckoClient(config, clock=lambda: NOW).fetch_rates()
    assert set(result) == {"BTC_USD", "ETH_USD", "SOL_USD"}
    assert result["BTC_USD"] == {
        "rate": 60000.0,
        "updated_at": NOW.isoformat(),
        "source": "CoinGecko",
        "meta": {
            "provider_updated_at": datetime.fromtimestamp(
                PROVIDER_TIME, UTC
            ).isoformat()
        },
    }
    assert result["ETH_USD"]["rate"] == 3000
    assert result["SOL_USD"]["rate"] == 150
    assert payload == before
    http_get.assert_called_once_with(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": "bitcoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_last_updated_at": "true",
        },
        headers={"x-cg-demo-api-key": "fake-demo-key"},
        timeout=10.0,
        allow_redirects=False,
    )
    http_get.return_value.close.assert_called_once()


def test_crypto_without_demo_key(config, http_get):
    config.coingecko_api_key = ""
    http_get.return_value.json.return_value = crypto_response()
    assert CoinGeckoClient(config, clock=lambda: NOW).fetch_rates()
    assert http_get.call_args.kwargs["headers"] == {}


def test_fiat_direction_and_metadata(config, http_get):
    http_get.return_value.json.return_value = fiat_response()
    result = ExchangeRateApiClient(config, clock=lambda: NOW).fetch_rates()
    assert {pair: entry["rate"] for pair, entry in result.items()} == {
        "EUR_USD": 1.25,
        "GBP_USD": 2.0,
        "RUB_USD": 0.01,
    }
    assert result["EUR_USD"]["updated_at"] == NOW.isoformat()
    assert result["EUR_USD"]["source"] == "ExchangeRate-API"
    assert "provider_updated_at" in result["EUR_USD"]["meta"]
    http_get.assert_called_once_with(
        "https://v6.exchangerate-api.com/v6/fake-exchange-key/latest/USD",
        params=None,
        headers=None,
        timeout=10.0,
        allow_redirects=False,
    )


def test_missing_exchange_key_does_not_send_request(config, http_get):
    config.exchangerate_api_key = ""
    with pytest.raises(ApiRequestError, match="EXCHANGERATE_API_KEY"):
        ExchangeRateApiClient(config).fetch_rates()
    http_get.assert_not_called()


@pytest.mark.parametrize("client_class", [CoinGeckoClient, ExchangeRateApiClient])
def test_empty_config_does_not_send_request(config, http_get, client_class):
    config.crypto_ids = {}
    config.fiat_currencies = []
    assert client_class(config).fetch_rates() == {}
    http_get.assert_not_called()


@pytest.mark.parametrize("client_class", [CoinGeckoClient, ExchangeRateApiClient])
@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500, 503])
def test_http_errors_are_safe(config, http_get, client_class, status):
    response = http_get.return_value
    response.status_code = status
    response.text = "fake-exchange-key fake-demo-key"
    with pytest.raises(ApiRequestError, match=f"HTTP {status}") as caught:
        client_class(config).fetch_rates()
    assert "fake-exchange-key" not in str(caught.value)
    assert "fake-demo-key" not in str(caught.value)
    response.json.assert_not_called()
    response.close.assert_called_once()


@pytest.mark.parametrize("client_class", [CoinGeckoClient, ExchangeRateApiClient])
@pytest.mark.parametrize(
    "error_class",
    [requests.Timeout, requests.ConnectionError, requests.RequestException],
)
def test_network_errors_do_not_reveal_keys(config, http_get, client_class, error_class):
    http_get.side_effect = error_class(
        "https://example/fake-exchange-key fake-demo-key"
    )
    with pytest.raises(ApiRequestError) as caught:
        client_class(config).fetch_rates()
    printed = "".join(traceback.format_exception(caught.value))
    assert "fake-exchange-key" not in printed
    assert "fake-demo-key" not in printed
    assert "Повторите позже" in str(caught.value) or "Проверьте сеть" in str(
        caught.value
    )
    assert http_get.call_count == 1


@pytest.mark.parametrize("client_class", [CoinGeckoClient, ExchangeRateApiClient])
def test_invalid_json(config, http_get, client_class):
    http_get.return_value.json.side_effect = ValueError("fake-exchange-key")
    with pytest.raises(ApiRequestError, match="JSON") as caught:
        client_class(config).fetch_rates()
    assert "fake-exchange-key" not in str(caught.value)
    http_get.return_value.close.assert_called_once()


@pytest.mark.parametrize("client_class", [CoinGeckoClient, ExchangeRateApiClient])
@pytest.mark.parametrize("payload", [None, [], "text", 42])
def test_wrong_json_root(config, http_get, client_class, payload):
    http_get.return_value.json.return_value = payload
    with pytest.raises(ApiRequestError):
        client_class(config).fetch_rates()


@pytest.mark.parametrize(
    "value", [None, True, 0, -1, "100", float("nan"), float("inf")]
)
@pytest.mark.parametrize("kind", ["crypto", "fiat"])
def test_bad_rate(config, http_get, kind, value):
    if kind == "crypto":
        data = crypto_response()
        data["bitcoin"]["usd"] = value
        client = CoinGeckoClient(config, clock=lambda: NOW)
    else:
        data = fiat_response()
        data["conversion_rates"]["EUR"] = value
        client = ExchangeRateApiClient(config, clock=lambda: NOW)
    http_get.return_value.json.return_value = data
    with pytest.raises(ApiRequestError, match="некорректные курсы"):
        client.fetch_rates()


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        True,
        0,
        -1,
        "bad",
        float("inf"),
        1e100,
        int((NOW + timedelta(hours=1)).timestamp()),
    ],
)
@pytest.mark.parametrize("kind", ["crypto", "fiat"])
def test_bad_provider_time(config, http_get, kind, timestamp):
    if kind == "crypto":
        data = crypto_response()
        data["bitcoin"]["last_updated_at"] = timestamp
        client = CoinGeckoClient(config, clock=lambda: NOW)
    else:
        data = fiat_response()
        data["time_last_update_unix"] = timestamp
        client = ExchangeRateApiClient(config, clock=lambda: NOW)
    http_get.return_value.json.return_value = data
    with pytest.raises(ApiRequestError):
        client.fetch_rates()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"bitcoin": None},
        {"bitcoin": {"usd": 1}},
        {"bitcoin": {"usd": 1, "last_updated_at": PROVIDER_TIME}},
    ],
)
def test_incomplete_crypto_response(config, http_get, payload):
    http_get.return_value.json.return_value = payload
    with pytest.raises(ApiRequestError):
        CoinGeckoClient(config, clock=lambda: NOW).fetch_rates()


@pytest.mark.parametrize(
    "error_type",
    [
        "invalid-key",
        "inactive-account",
        "quota-reached",
        "unsupported-code",
        "fake-exchange-key",
        [],
        None,
    ],
)
def test_provider_error_is_safe(config, http_get, error_type):
    http_get.return_value.json.return_value = {
        "result": "error",
        "error-type": error_type,
    }
    with pytest.raises(ApiRequestError) as caught:
        ExchangeRateApiClient(config).fetch_rates()
    assert "fake-exchange-key" not in str(caught.value)


@pytest.mark.parametrize(
    "field", ["conversion_rates", "time_last_update_unix", "base_code"]
)
def test_missing_fiat_field(config, http_get, field):
    payload = fiat_response()
    del payload[field]
    http_get.return_value.json.return_value = payload
    with pytest.raises(ApiRequestError):
        ExchangeRateApiClient(config, clock=lambda: NOW).fetch_rates()


@pytest.mark.parametrize(
    "rates",
    [
        None,
        [],
        {},
        {"USD": 1, "EUR": 0.8},
        {"USD": 2, "EUR": 0.8, "GBP": 0.5, "RUB": 100},
    ],
)
def test_invalid_fiat_rates_table(config, http_get, rates):
    data = fiat_response()
    data["conversion_rates"] = rates
    http_get.return_value.json.return_value = data
    with pytest.raises(ApiRequestError):
        ExchangeRateApiClient(config, clock=lambda: NOW).fetch_rates()


def test_wrong_base_is_rejected(config, http_get):
    data = fiat_response()
    data["base_code"] = "EUR"
    http_get.return_value.json.return_value = data
    with pytest.raises(ApiRequestError):
        ExchangeRateApiClient(config, clock=lambda: NOW).fetch_rates()


def test_tiny_fiat_rate_does_not_produce_infinite_quote(config, http_get):
    data = fiat_response()
    data["conversion_rates"]["EUR"] = 1e-320
    http_get.return_value.json.return_value = data
    with pytest.raises(ApiRequestError):
        ExchangeRateApiClient(config, clock=lambda: NOW).fetch_rates()


def test_clients_match_cache_format(config, http_get, tmp_path):
    http_get.return_value.json.side_effect = [crypto_response(), fiat_response()]
    crypto = CoinGeckoClient(config, clock=lambda: NOW).fetch_rates()
    fiat = ExchangeRateApiClient(config, clock=lambda: NOW).fetch_rates()
    save_json(
        config.rates_file,
        {"pairs": {**crypto, **fiat}, "last_refresh": NOW.isoformat()},
    )
    rates = RateService(tmp_path, ttl=300, clock=lambda: NOW)
    assert rates.get_rate("BTC", "EUR")["rate"] == 48000
    assert rates.get_rate("EUR", "USD")["rate"] == 1.25


def test_fiat_daily_timestamp_is_preserved_separately(config, http_get):
    data = fiat_response()
    yesterday = NOW - timedelta(days=1)
    data["time_last_update_unix"] = int(yesterday.timestamp())
    http_get.return_value.json.return_value = data
    quote = ExchangeRateApiClient(config, clock=lambda: NOW).fetch_rates()["EUR_USD"]
    assert quote["updated_at"] == NOW.isoformat()
    assert quote["meta"]["provider_updated_at"] == yesterday.isoformat()
