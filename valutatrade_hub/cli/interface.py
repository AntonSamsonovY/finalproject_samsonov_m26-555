"""Разбор команд и вывод результатов в терминале."""

import argparse
import getpass
import shlex

from valutatrade_hub.core.exceptions import (
    AuthenticationError,
    CurrencyNotFoundError,
    StorageError,
)
from valutatrade_hub.core.usecases import AuthService
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


class CommandParser(argparse.ArgumentParser):
    """Парсер, который при ошибке не закрывает приложение."""

    def error(self, message):
        """Передать ошибку в общий цикл команд."""
        raise ValueError(f"Некорректные аргументы: {message}. Введите help.")


def build_parser():
    """Описать аргументы доступных команд."""
    parser = CommandParser(prog="valutatrade-hub", add_help=False, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("register", "login"):
        command = commands.add_parser(name, add_help=False, allow_abbrev=False)
        command.add_argument("--username", required=True)
        command.add_argument("--password")
    for name in ("help", "whoami", "logout", "exit"):
        commands.add_parser(name, add_help=False)
    return parser


def show_help():
    """Показать уже реализованные команды."""
    print("Команды:")
    print("  register --username <имя> [--password <пароль>] — регистрация")
    print("  login --username <имя> [--password <пароль>] — вход")
    print("  whoami — текущий пользователь")
    print("  logout — завершить сессию")
    print("  help — справка")
    print("  exit — выйти")
    print("Если не указывать --password, пароль вводится отдельно и не отображается.")


def run(service):
    """Читать команды и передавать их сервису регистрации и входа."""
    print("ValutaTrade Hub")
    show_help()
    parser = build_parser()

    while True:
        try:
            command = input("Введите команду: ").strip()
            if not command:
                continue
            parts = shlex.split(command)
            if not parts:
                continue
            if parts in (["--help"], ["-h"]):
                show_help()
                continue
            args = parser.parse_args(parts)

            if args.command == "exit":
                print("До свидания!")
                break
            elif args.command == "help":
                show_help()
            elif args.command in ("register", "login"):
                password = args.password
                if password is None:
                    password = getpass.getpass("Пароль: ")
                if args.command == "register":
                    info = service.register(args.username, password)
                    print(
                        f"Пользователь '{info['username']}' зарегистрирован "
                        f"(id={info['user_id']}). Войдите командой login."
                    )
                else:
                    info = service.login(args.username, password)
                    print(f"Вы вошли как '{info['username']}'.")
            elif args.command == "whoami":
                info = service.current_user()
                print(
                    f"Текущий пользователь: {info['username']} (id={info['user_id']})."
                )
            elif args.command == "logout":
                service.logout()
                print("Сессия завершена.")
        except (EOFError, KeyboardInterrupt):
            print("\nДо свидания!")
            break
        except UnicodeError:
            print("Ошибка кодировки ввода. Повторите команду в UTF-8.")
        except (ValueError, AuthenticationError, StorageError) as error:
            print(f"Ошибка: {error}")


def main():
    """Загрузить настройки и запустить консольный интерфейс."""
    try:
        settings = SettingsLoader()
        database = DatabaseManager(settings.get("data_dir"))
        run(AuthService(database))
    except (ValueError, CurrencyNotFoundError, StorageError) as error:
        print(f"Не удалось запустить приложение: {error}")
        return 1
    return 0
