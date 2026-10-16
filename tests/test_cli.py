"""Проверки командного цикла и запуска в отдельных процессах."""

import os
import subprocess
import sys

import pytest

from valutatrade_hub.cli.interface import main, run
from valutatrade_hub.core.usecases import AuthService
from valutatrade_hub.infra.database import DatabaseManager
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
