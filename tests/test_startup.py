"""Проверка начальной команды запуска."""

from valutatrade_hub.cli.interface import main


def test_main_prints_greeting(capsys):
    main()
    assert capsys.readouterr().out == "ValutaTrade Hub\n"
