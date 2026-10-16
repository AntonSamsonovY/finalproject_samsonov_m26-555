"""Проверка начальной команды запуска."""

from valutatrade_hub.cli.interface import main
from valutatrade_hub.infra.settings import SettingsLoader


def test_main_prints_greeting(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(SettingsLoader, "_instance", None)
    monkeypatch.setenv("VALUTATRADE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda _: "exit")
    assert main() == 0
    output = capsys.readouterr().out
    assert "ValutaTrade Hub" in output
    assert "До свидания!" in output
