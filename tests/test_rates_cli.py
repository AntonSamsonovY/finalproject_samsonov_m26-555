"""Обновление и просмотр курсов из командного цикла."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from valutatrade_hub.cli.interface import main, run
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import AuthService
from valutatrade_hub.infra.database import DatabaseManager, save_json
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage
from valutatrade_hub.parser_service.updater import RatesUpdater

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def quote(rate):
    return {"rate": rate, "updated_at": NOW.isoformat(), "source": "Test"}


def run_commands(monkeypatch, commands, auth, updater, rates):
    commands = iter([*commands, "exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    run(auth, updater=updater, rates=rates)


@pytest.fixture
def services(tmp_path):
    auth = AuthService(DatabaseManager(tmp_path))
    storage = RatesStorage(ParserConfig(tmp_path))
    clients = {
        "coingecko": Mock(
            fetch_rates=Mock(
                return_value={
                    "BTC_USD": quote(60000),
                    "ETH_USD": quote(3000),
                    "SOL_USD": quote(150),
                }
            )
        ),
        "exchangerate": Mock(fetch_rates=Mock(return_value={"EUR_USD": quote(1.2)})),
    }
    updater = RatesUpdater(clients, storage, clock=lambda: NOW)
    rates = RateService(tmp_path, 300, refresh=updater.run_update, clock=lambda: NOW)
    return auth, updater, rates, clients


def test_commands_without_login(services, monkeypatch, capsys):
    auth, updater, rates, clients = services
    run_commands(
        monkeypatch, ["show-rates", "update-rates", "show-rates"], auth, updater, rates
    )
    output = capsys.readouterr().out
    assert "нет подходящих курсов" in output
    assert "Обновлено курсов: 4" in output
    assert "Добавлено в историю: 4" in output
    assert "BTC" in output and "60000.00000000" in output
    assert "актуален" in output
    assert "Сначала выполните login" not in output
    for client in clients.values():
        client.fetch_rates.assert_called_once()


def test_show_filters_without_network(services, monkeypatch, capsys):
    auth, updater, rates, clients = services
    updater.run_update()
    capsys.readouterr()
    for client in clients.values():
        client.fetch_rates.reset_mock()
    run_commands(
        monkeypatch, ["show-rates --currency btc --base eur"], auth, updater, rates
    )
    output = capsys.readouterr().out
    assert "Курс к EUR" in output
    assert "50000.00000000" in output
    assert "ETH" not in output
    for client in clients.values():
        client.fetch_rates.assert_not_called()


def test_top_cli(services, monkeypatch, capsys):
    auth, updater, rates, _ = services
    updater.run_update()
    run_commands(monkeypatch, ["show-rates --top 2"], auth, updater, rates)
    output = capsys.readouterr().out
    assert "60000.00000000" in output
    assert "3000.00000000" in output
    assert "150.00000000" not in output
    assert "1.20000000" not in output


def test_selected_source_cli(services, monkeypatch, capsys):
    auth, updater, rates, clients = services
    run_commands(monkeypatch, ["update-rates --source coingecko"], auth, updater, rates)
    assert "Обновлено курсов: 3" in capsys.readouterr().out
    clients["coingecko"].fetch_rates.assert_called_once()
    clients["exchangerate"].fetch_rates.assert_not_called()


def test_partial_failure_report(services, monkeypatch, capsys):
    auth, updater, rates, clients = services
    clients["exchangerate"].fetch_rates.side_effect = ApiRequestError(
        "задайте EXCHANGERATE_API_KEY"
    )
    run_commands(monkeypatch, ["update-rates", "show-rates"], auth, updater, rates)
    output = capsys.readouterr().out
    assert "Обновлено курсов: 3" in output
    assert "Обновление выполнено частично" in output
    assert "EXCHANGERATE_API_KEY" in output
    assert "60000.00000000" in output


def test_complete_failure_is_not_reported_as_success(services, monkeypatch, capsys):
    auth, updater, rates, clients = services
    for client in clients.values():
        client.fetch_rates.side_effect = ApiRequestError("сервис недоступен")
    run_commands(monkeypatch, ["update-rates"], auth, updater, rates)
    output = capsys.readouterr().out
    assert "Курсы не обновлены" in output
    assert "Обновлено курсов:" not in output


def test_repeated_update_report(services, monkeypatch, capsys):
    auth, updater, rates, _ = services
    run_commands(monkeypatch, ["update-rates", "update-rates"], auth, updater, rates)
    assert "Новых курсов нет. В кэше: 4" in capsys.readouterr().out


def test_stale_rates_display_does_not_refresh(services, tmp_path, monkeypatch, capsys):
    auth, updater, rates, clients = services
    entry = quote(60000)
    entry["updated_at"] = (NOW - timedelta(hours=1)).isoformat()
    save_json(tmp_path / "rates.json", {"pairs": {"BTC_USD": entry}})
    run_commands(monkeypatch, ["show-rates"], auth, updater, rates)
    output = capsys.readouterr().out
    assert "устарел" in output
    assert "Для операций обновите" in output
    for client in clients.values():
        client.fetch_rates.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        "show-rates --top 0",
        "show-rates --top -1",
        "show-rates --top text",
        "show-rates --currency XYZ",
        "show-rates --base XYZ",
        "update-rates --source unknown",
        "update-rates --source",
        "show-rates --unknown",
    ],
)
def test_bad_arguments_do_not_request_network(services, monkeypatch, capsys, command):
    auth, updater, rates, clients = services
    run_commands(monkeypatch, [command], auth, updater, rates)
    output = capsys.readouterr().out
    assert "Ошибка:" in output
    assert "До свидания" in output
    for client in clients.values():
        client.fetch_rates.assert_not_called()


def test_broken_cache_is_not_overwritten(services, tmp_path, monkeypatch, capsys):
    auth, updater, rates, clients = services
    path = tmp_path / "rates.json"
    path.write_text("{broken", encoding="utf-8")
    run_commands(monkeypatch, ["show-rates", "update-rates"], auth, updater, rates)
    assert capsys.readouterr().out.count("Ошибка:") == 2
    assert path.read_text() == "{broken"
    for client in clients.values():
        client.fetch_rates.assert_not_called()


def test_main_connects_automatic_refresh_and_trading(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setenv("VALUTATRADE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EXCHANGERATE_API_KEY", "fake-key")
    timestamp = int(datetime.now(UTC).timestamp())
    response = Mock(status_code=200)
    response.json.side_effect = [
        {
            coin: {"usd": rate, "last_updated_at": timestamp}
            for coin, rate in {
                "bitcoin": 60000,
                "ethereum": 3000,
                "solana": 150,
            }.items()
        },
        {
            "result": "success",
            "base_code": "USD",
            "time_last_update_unix": timestamp,
            "conversion_rates": {"USD": 1, "EUR": 0.8, "GBP": 0.5, "RUB": 100},
        },
    ]
    get = Mock(return_value=response)
    monkeypatch.setattr("requests.get", get)
    commands = iter(
        [
            "get-rate --from BTC --to EUR",
            "register --username demo --password test-password",
            "login --username demo --password test-password",
            "buy --currency BTC --amount 0.1",
            "show-portfolio",
            "show-rates --top 1",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    assert main() == 0
    output = capsys.readouterr().out
    assert "Курс BTC→EUR: 48000.00000000" in output
    assert "Куплено: 0.10000000 BTC" in output
    assert "Итого: 6000.00 USD" in output
    assert "Ошибка:" not in output
    assert "fake-key" not in output
    assert get.call_count == 2
    storage = RatesStorage(ParserConfig(tmp_path))
    assert len(storage.load()[1]) == 6


def test_main_missing_key_report_stays_in_command_loop(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.delenv("EXCHANGERATE_API_KEY", raising=False)
    monkeypatch.setenv("VALUTATRADE_DATA_DIR", str(tmp_path))
    get = Mock(side_effect=AssertionError("Не должно быть запроса без ключа"))
    monkeypatch.setattr("requests.get", get)
    commands = iter(["update-rates --source exchangerate", "help", "exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    assert main() == 0
    output = capsys.readouterr().out
    assert "Курсы не обновлены" in output
    assert "EXCHANGERATE_API_KEY" in output
    assert "До свидания" in output
    get.assert_not_called()


@pytest.mark.parametrize("stale_cache", [False, True])
def test_buy_automatically_fetches_crypto_without_fiat_key(
    tmp_path, monkeypatch, capsys, stale_cache
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setenv("VALUTATRADE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("EXCHANGERATE_API_KEY", raising=False)
    if stale_cache:
        save_json(
            tmp_path / "rates.json",
            {
                "pairs": {
                    "BTC_USD": {
                        "rate": 50000,
                        "source": "Test",
                        "updated_at": (
                            datetime.now(UTC) - timedelta(hours=1)
                        ).isoformat(),
                    }
                }
            },
        )
    response = Mock(status_code=200)
    response.json.return_value = {
        coin: {"usd": rate, "last_updated_at": int(datetime.now(UTC).timestamp())}
        for coin, rate in {"bitcoin": 60000, "ethereum": 3000, "solana": 150}.items()
    }
    get = Mock(return_value=response)
    monkeypatch.setattr("requests.get", get)
    commands = iter(
        [
            "register --username demo --password test-password",
            "login --username demo --password test-password",
            "buy --currency BTC --amount 0.1",
            "show-portfolio",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    assert main() == 0
    output = capsys.readouterr().out
    assert "Куплено: 0.10000000 BTC" in output
    assert "Итого: 6000.00 USD" in output
    assert "Ошибка:" not in output
    get.assert_called_once()
    balance = DatabaseManager(tmp_path).load()["portfolios"][0]["wallets"]["BTC"]
    assert balance["balance"] == 0.1
