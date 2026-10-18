"""Содержание журнала операций, ротация и защита секретов."""

import json
import logging
from datetime import UTC, datetime
from unittest.mock import Mock, patch
from urllib.parse import quote

import pytest

from valutatrade_hub.cli.interface import main
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    AuthenticationError,
    InsufficientFundsError,
    StorageError,
)
from valutatrade_hub.core.usecases import AuthService, TradingService
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import AppFileHandler, configure_logging


@pytest.fixture
def settings_factory(tmp_path, monkeypatch):
    def create(**options):
        monkeypatch.setattr(SettingsLoader, "_instance", None)
        config = tmp_path / "pyproject.toml"
        content = "[tool.valutatrade]\n" + "\n".join(
            f"{key} = {json.dumps(value)}" for key, value in options.items()
        )
        config.write_text(content, encoding="utf-8")
        return SettingsLoader(config)

    return create


@pytest.fixture
def services(tmp_path, settings_factory):
    settings = settings_factory(log_format="json")
    logger = configure_logging(settings)
    database = DatabaseManager(tmp_path / "data")
    auth = AuthService(database)
    auth.register("Антон", "not-for-log-password")
    auth.login("Антон", "not-for-log-password")
    rates = Mock(get_rate=Mock(return_value={"rate": 60000}))
    return TradingService(database, auth, rates), database, auth, rates, logger


def read_events(tmp_path):
    path = tmp_path / "logs" / "valutatrade.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_success_and_error_fields(services, tmp_path):
    trading, _, _, _, _ = services
    trading.buy("btc", 0.1, verbose=True)
    trading.sell("BTC", 0.04, verbose=True)
    with pytest.raises(InsufficientFundsError):
        trading.sell("BTC", 1)
    events = read_events(tmp_path)
    assert len(events) == 3
    buy, sell, error = events
    assert buy["action"] == "buy"
    assert buy["user"] == "Антон" and buy["user_id"] == 1
    assert buy["currency"] == "BTC"
    assert buy["amount"] == 0.1
    assert buy["rate"] == 60000 and buy["base"] == "USD"
    assert buy["result"] == "OK" and buy["level"] == "INFO"
    assert buy["balance_before"] == 0
    assert buy["balance_after"] == 0.1
    assert datetime.fromisoformat(buy["timestamp"]).tzinfo == UTC
    assert sell["action"] == "sell"
    assert sell["balance_before"] == 0.1
    assert sell["balance_after"] == pytest.approx(0.06)
    assert error["result"] == "ERROR" and error["level"] == "ERROR"
    assert error["error_type"] == "InsufficientFundsError"
    assert "Недостаточно средств" in error["error"]
    assert "balance_before" not in error
    assert "not-for-log-password" not in json.dumps(events)


def test_regular_mode_omits_verbose_fields(services, tmp_path):
    trading, _, _, _, _ = services
    trading.buy("BTC", 0.1)
    event = read_events(tmp_path)[0]
    assert "balance_before" not in event and "balance_after" not in event
    assert "error" not in event
    assert trading.buy.__name__ == "buy"
    assert trading.buy.__doc__
    assert hasattr(trading.buy, "__wrapped__")


def test_unauthenticated_operation_is_logged(services, tmp_path):
    trading, _, auth, _, _ = services
    auth.logout()
    with pytest.raises(AuthenticationError):
        trading.buy("BTC", 1)
    event = read_events(tmp_path)[0]
    assert event["user"] is None and event["user_id"] is None
    assert event["result"] == "ERROR"
    assert event["error_type"] == "AuthenticationError"


@pytest.mark.parametrize(
    "amount", [True, "not-for-log-password", float("inf"), float("nan")]
)
def test_invalid_amount_is_logged_as_valid_json(services, tmp_path, amount):
    trading, _, _, _, _ = services
    with pytest.raises(ValueError):
        trading.buy("BTC", amount)
    event = read_events(tmp_path)[0]
    assert event["amount"] is None
    assert event["result"] == "ERROR"
    assert "not-for-log-password" not in json.dumps(event)


@pytest.mark.parametrize("failure", ["rate", "save"])
def test_original_exception_is_reraised(services, tmp_path, failure):
    trading, database, _, rates, _ = services
    error = (
        ApiRequestError("test failure")
        if failure == "rate"
        else StorageError("test failure")
    )
    if failure == "rate":
        rates.get_rate.side_effect = error
        with pytest.raises(ApiRequestError) as caught:
            trading.buy("BTC", 1)
    else:
        with patch.object(database, "save", side_effect=error):
            with pytest.raises(StorageError) as caught:
                trading.buy("BTC", 1)
    assert caught.value is error
    assert read_events(tmp_path)[0]["result"] == "ERROR"
    assert database.load()["portfolios"][0]["wallets"] == {}


def test_logging_failure_does_not_repeat_successful_trade(services, tmp_path, capsys):
    trading, database, _, _, logger = services
    handler = next(
        handler for handler in logger.handlers if isinstance(handler, AppFileHandler)
    )
    with patch.object(handler, "emit", side_effect=OSError("sensitive-details")):
        result = trading.buy("BTC", 0.1)
    assert result["balance"] == 0.1
    assert database.load()["portfolios"][0]["wallets"]["BTC"]["balance"] == 0.1
    output = capsys.readouterr().err
    assert "Не удалось записать событие" in output
    assert "sensitive-details" not in output


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_keys_and_newlines_are_safe(
    tmp_path, monkeypatch, settings_factory, log_format
):
    settings = settings_factory(log_format=log_format)
    secret = 'fake-key/with"quote\\end'
    monkeypatch.setenv("EXCHANGERATE_API_KEY", secret)
    monkeypatch.setenv("COINGECKO_DEMO_API_KEY", "fake-demo-key")
    logger = configure_logging(settings)
    logger.warning(
        "Запрос: %s; %s; fake-demo-key\nПоддельная строка",
        secret,
        quote(secret, safe=""),
    )
    path = tmp_path / "logs" / "valutatrade.log"
    text = path.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 1
    assert "fake-key" not in text and "fake-demo-key" not in text
    assert quote(secret, safe="") not in text
    assert "[REDACTED]" in text
    assert "\\n" in text
    if log_format == "json":
        assert json.loads(text)["level"] == "WARNING"


def test_secret_in_operation_error_is_redacted(services, tmp_path, monkeypatch):
    trading, _, _, rates, _ = services
    monkeypatch.setenv("EXCHANGERATE_API_KEY", "fake-secret-key")
    rates.get_rate.side_effect = ApiRequestError("failure for fake-secret-key")
    with pytest.raises(ApiRequestError):
        trading.buy("BTC", 1)
    assert "fake-secret-key" not in (tmp_path / "logs" / "valutatrade.log").read_text()
    assert "[REDACTED]" in read_events(tmp_path)[0]["error"]


def test_rotation_and_backup_limit(tmp_path, settings_factory):
    settings = settings_factory(
        log_format="json", log_max_bytes=500, log_backup_count=2
    )
    logger = configure_logging(settings)
    for index in range(30):
        logger.info("Событие %s %s", index, "test" * 10)
    files = list((tmp_path / "logs").glob("valutatrade.log*"))
    assert len(files) == 3
    for path in files:
        assert path.stat().st_size < 500
        for line in path.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["level"] == "INFO"
    assert "Событие 29" in read_events(tmp_path)[-1]["message"]


def test_repeated_setup_does_not_duplicate_handlers(tmp_path, settings_factory):
    settings = settings_factory(log_format="json")
    root_handlers = logging.getLogger().handlers[:]
    logger = configure_logging(settings)
    logger.info("first")
    configure_logging(settings)
    logger.info("second")
    assert (
        len(
            [
                handler
                for handler in logger.handlers
                if isinstance(handler, AppFileHandler)
            ]
        )
        == 1
    )
    assert [event["message"] for event in read_events(tmp_path)] == ["first", "second"]
    assert logging.getLogger().handlers == root_handlers


def test_parser_uses_same_file(tmp_path, settings_factory):
    configure_logging(settings_factory(log_format="json"))
    logging.getLogger("valutatrade.parser").info("Обновлено курсов: 3")
    event = read_events(tmp_path)[0]
    assert event["logger"] == "valutatrade.parser"
    assert event["message"] == "Обновлено курсов: 3"


def test_debug_level(tmp_path, settings_factory):
    settings = settings_factory(log_format="json", log_level="DEBUG")
    logger = configure_logging(settings)
    logger.debug("debug")
    assert read_events(tmp_path)[0]["level"] == "DEBUG"


def test_default_level_omits_debug(tmp_path, settings_factory):
    logger = configure_logging(settings_factory(log_format="json"))
    logger.debug("debug")
    logger.info("info")
    assert [event["level"] for event in read_events(tmp_path)] == ["INFO"]


def test_rotation_failure_does_not_print_secrets(tmp_path, settings_factory, capsys):
    logger = configure_logging(settings_factory(log_max_bytes=1))
    logger.info("Первая запись перед ротацией")
    handler = next(
        handler for handler in logger.handlers if isinstance(handler, AppFileHandler)
    )
    with patch.object(handler, "doRollover", side_effect=OSError("sensitive-details")):
        logger.error("sensitive-event")
    output = capsys.readouterr().err
    assert "Не удалось записать событие" in output
    assert "sensitive-details" not in output and "sensitive-event" not in output


def test_main_verbose_and_password_not_logged(tmp_path, monkeypatch, settings_factory):
    settings_factory(log_format="json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VALUTATRADE_DATA_DIR", str(tmp_path / "data"))
    commands = iter(
        [
            "register --username demo --password private-password",
            "login --username demo --password private-password",
            "buy --currency USD --amount 20 --verbose",
            "sell --currency USD --amount 5 --verbose",
            "sell --currency USD --amount 50",
            "exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    assert main() == 0
    events = read_events(tmp_path)
    assert [event["result"] for event in events] == ["OK", "OK", "ERROR"]
    assert events[1]["balance_before"] == 20
    assert events[1]["balance_after"] == 15
    assert "private-password" not in json.dumps(events)


def test_unwritable_log_folder_gives_startup_error(
    tmp_path, monkeypatch, capsys, settings_factory
):
    settings_factory()
    path = tmp_path / "not-a-folder"
    path.write_text("file", encoding="utf-8")
    monkeypatch.setenv("VALUTATRADE_LOGS_DIR", str(path))
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.chdir(tmp_path)
    assert main() == 1
    assert "Не удалось открыть журнал" in capsys.readouterr().out
