"""Проверки командного цикла и запуска в отдельных процессах."""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from valutatrade_hub.cli.interface import main, run
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import AuthService, TradingService
from valutatrade_hub.infra.database import DatabaseManager, save_json
from valutatrade_hub.infra.settings import SettingsLoader


def test_cli_commands(tmp_path, monkeypatch, capsys):
    commands = iter(
        [
            "",
            "'",
            "unknown",
            "register",
            "whoami",
            "help",
            'register --username "Антон Самсонов"',
            'login --username "Антон Самсонов"',
            "whoami",
            "logout",
            "whoami",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    monkeypatch.setattr("getpass.getpass", lambda _: "test-password")
    run(AuthService(DatabaseManager(tmp_path)))
    output = capsys.readouterr().out
    for text in (
        "зарегистрирован",
        "Вы вошли как 'Антон Самсонов'",
        "Текущий пользователь: Антон Самсонов",
        "Сессия завершена",
        "Сначала выполните login",
        "До свидания",
    ):
        assert text in output
    assert "test-password" not in output


@pytest.mark.parametrize("exception", [EOFError, KeyboardInterrupt])
def test_exit_on_interrupted_input(tmp_path, monkeypatch, capsys, exception):
    def interrupted(_):
        raise exception

    monkeypatch.setattr("builtins.input", interrupted)
    run(AuthService(DatabaseManager(tmp_path)))
    assert "До свидания" in capsys.readouterr().out


def test_exit_during_password_input(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "register --username anton")

    def interrupted(_):
        raise EOFError

    monkeypatch.setattr("getpass.getpass", interrupted)
    run(AuthService(DatabaseManager(tmp_path)))
    assert "До свидания" in capsys.readouterr().out
    assert not (tmp_path / "users.json").exists()


def test_startup_error_has_nonzero_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    (tmp_path / "pyproject.toml").write_text("[broken", encoding="utf-8")
    assert main() == 1
    assert "Не удалось запустить приложение" in capsys.readouterr().out


def test_restart_in_another_process(tmp_path):
    env = {**os.environ, "VALUTATRADE_DATA_DIR": str(tmp_path)}
    commands = (
        (
            "register --username demo --password test-password\nexit\n",
            "зарегистрирован",
        ),
        (
            "login --username demo --password test-password\nwhoami\nexit\n",
            "Вы вошли как 'demo'",
        ),
    )
    for text, expected in commands:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "valutatrade_hub"],
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=tmp_path,
            timeout=15,
            check=True,
        )
        assert expected in result.stdout
        assert "test-password" not in result.stdout
        assert not result.stderr


def test_trading_commands(tmp_path, monkeypatch, capsys):
    now = datetime.now(UTC)
    save_json(
        tmp_path / "rates.json",
        {
            "pairs": {
                "BTC_USD": {
                    "rate": 60000,
                    "updated_at": now.isoformat(),
                    "source": "Test",
                }
            }
        },
    )
    database = DatabaseManager(tmp_path)
    auth = AuthService(database)
    trading = TradingService(database, auth, RateService(tmp_path, 300))
    commands = iter(
        [
            "get-rate --from btc --to usd",
            "buy --currency BTC --amount 0.1",
            "register --username demo --password test-password",
            "login --username demo --password test-password",
            "show-portfolio",
            "buy --currency btc --amount 0.1",
            "show-portfolio",
            "sell --currency BTC --amount 0.05",
            "show-portfolio --base BTC",
            "sell --currency BTC --amount 1",
            "buy --currency BTC --amount nan",
            "buy --currency BTC --amount -1",
            "buy --currency BTC --amount text",
            "buy --currency BTC",
            "buy --currency XYZ --amount 1",
            "get-rate --from BTC --to XYZ",
            "get-rate --from ETH --to USD",
            "show-portfolio --base XYZ",
            "whoami",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    run(auth, trading)
    output = capsys.readouterr().out
    for text in (
        "Курс BTC→USD: 60000.00000000",
        "Сначала выполните login",
        "Портфель пока пуст",
        "Куплено: 0.10000000 BTC",
        "Итого: 6000.00 USD",
        "Продано: 0.05000000 BTC",
        "база: BTC",
        "Недостаточно средств",
        "Сумма должна",
        "Некорректные аргументы",
        "Неизвестная валюта 'XYZ'",
        "Выполните update-rates",
        "Текущий пользователь: demo",
        "До свидания",
    ):
        assert text in output
    assert "test-password" not in output
    assert database.load()["portfolios"][0]["wallets"]["BTC"]["balance"] == 0.05


def test_main_uses_configured_base(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.valutatrade]\ndefault_base_currency = "EUR"\n', encoding="utf-8"
    )
    commands = iter(
        [
            "register --username demo --password test-password",
            "login --username demo --password test-password",
            "show-portfolio",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    assert main() == 0
    assert "Итого: 0.00 EUR" in capsys.readouterr().out


def test_cli_stale_rate_does_not_buy(tmp_path, monkeypatch, capsys):
    save_json(
        tmp_path / "rates.json",
        {
            "pairs": {
                "BTC_USD": {
                    "rate": 60000,
                    "source": "Test",
                    "updated_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                }
            }
        },
    )
    database = DatabaseManager(tmp_path)
    auth = AuthService(database)
    auth.register("demo", "test-password")
    auth.login("demo", "test-password")
    commands = iter(["buy --currency BTC --amount 1", "exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    run(auth, TradingService(database, auth, RateService(tmp_path, 300)))
    output = capsys.readouterr().out
    assert "устарел" in output
    assert "Куплено:" not in output
    assert database.load()["portfolios"][0]["wallets"] == {}


def test_trading_after_process_restart(tmp_path):
    save_json(
        tmp_path / "rates.json",
        {
            "pairs": {
                "BTC_USD": {
                    "rate": 60000,
                    "source": "Test",
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            }
        },
    )
    env = {**os.environ, "VALUTATRADE_DATA_DIR": str(tmp_path)}
    sessions = [
        (
            "register --username demo --password test-password\n"
            "login --username demo --password test-password\n"
            "buy --currency BTC --amount 0.1\nexit\n",
            "Куплено: 0.10000000 BTC",
        ),
        (
            "login --username demo --password test-password\n"
            "sell --currency BTC --amount 0.04\n"
            "show-portfolio\nget-rate --from BTC --to USD\nexit\n",
            "Итого: 3600.00 USD",
        ),
    ]
    for commands, expected in sessions:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "valutatrade_hub"],
            input=commands,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=tmp_path,
            timeout=15,
            check=True,
        )
        assert expected in result.stdout
        assert "Ошибка:" not in result.stdout
        assert "test-password" not in result.stdout
        assert not result.stderr
    balance = DatabaseManager(tmp_path).load()["portfolios"][0]["wallets"]["BTC"]
    assert balance["balance"] == pytest.approx(0.06)
