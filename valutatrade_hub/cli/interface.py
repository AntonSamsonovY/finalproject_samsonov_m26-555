"""Разбор команд и вывод результатов в терминале."""

import argparse
import getpass
import shlex

from prettytable import PrettyTable

from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    AuthenticationError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    StorageError,
)
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.usecases import AuthService, TradingService
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.api_clients import (
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage
from valutatrade_hub.parser_service.updater import RatesUpdater


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
    for name in ("buy", "sell"):
        command = commands.add_parser(name, add_help=False, allow_abbrev=False)
        command.add_argument("--currency", required=True)
        command.add_argument("--amount", required=True, type=float)
    command = commands.add_parser("show-portfolio", add_help=False, allow_abbrev=False)
    command.add_argument("--base")
    command = commands.add_parser("get-rate", add_help=False, allow_abbrev=False)
    command.add_argument("--from", dest="from_code", required=True)
    command.add_argument("--to", dest="to_code", required=True)
    command = commands.add_parser("update-rates", add_help=False, allow_abbrev=False)
    command.add_argument("--source", choices=("coingecko", "exchangerate"))
    command = commands.add_parser("show-rates", add_help=False, allow_abbrev=False)
    command.add_argument("--currency")
    command.add_argument("--top", type=int)
    command.add_argument("--base", default="USD")
    return parser


def show_help():
    """Показать уже реализованные команды."""
    print("Команды:")
    print("  register --username <имя> [--password <пароль>] — регистрация")
    print("  login --username <имя> [--password <пароль>] — вход")
    print("  buy --currency <код> --amount <количество> — купить валюту")
    print("  sell --currency <код> --amount <количество> — продать валюту")
    print("  show-portfolio [--base <код>] — кошельки и общая стоимость")
    print("  get-rate --from <код> --to <код> — получить курс")
    print("  update-rates [--source coingecko|exchangerate] — обновить курсы")
    print("  show-rates [--currency <код>] [--top <N>] [--base <код>] — кэш курсов")
    print("  --top показывает самые дорогие из отслеживаемых криптовалют.")
    print("  whoami — текущий пользователь")
    print("  logout — завершить сессию")
    print("  help — справка")
    print("  exit — выйти")
    print("Если не указывать --password, пароль вводится отдельно и не отображается.")


def print_portfolio(info):
    """Вывести балансы и оценку портфеля в таблице."""
    print(f"Портфель пользователя '{info['username']}' (база: {info['base']}):")
    if not info["wallets"]:
        print("Портфель пока пуст. Добавьте валюту командой buy.")
    else:
        table = PrettyTable(["Валюта", "Баланс", f"Стоимость, {info['base']}"])
        for row in info["wallets"]:
            table.add_row(
                [row["currency"], f"{row['balance']:.8f}", f"{row['value']:.2f}"]
            )
        print(table)
    print(f"Итого: {info['total']:.2f} {info['base']}")


def print_rates(info):
    """Показать сохранённые курсы с явной отметкой об устаревших данных."""
    if not info["rates"]:
        print(
            "В кэше нет подходящих курсов. Выполните update-rates или измените фильтр."
        )
        return
    table = PrettyTable(
        ["Валюта", f"Курс к {info['base']}", "Получен (UTC)", "Источник", "Статус"]
    )
    for row in info["rates"]:
        table.add_row(
            [
                row["currency"],
                f"{row['rate']:.8f}",
                row["updated_at"],
                row["source"],
                "устарел" if row["stale"] else "актуален",
            ]
        )
    print(table)
    if any(row["stale"] for row in info["rates"]):
        print(
            "Устаревшие курсы показаны для справки. "
            "Для операций обновите их: update-rates."
        )


def print_update_report(report):
    """Вывести результат обновления и отдельные ошибки источников."""
    if report["updated"]:
        print(f"Обновлено курсов: {report['updated']}. В кэше: {report['cached']}.")
        print(f"Добавлено в историю: {report['history_added']}.")
    elif report["sources"]:
        print(f"Новых курсов нет. В кэше: {report['cached']}.")
    else:
        print("Курсы не обновлены.")
    if report["errors"] and report["sources"]:
        print("Обновление выполнено частично.")
    for source, error in report["errors"].items():
        print(f"Источник {source}: {error}")


def run(service, trading=None, base_currency="USD", updater=None, rates=None):
    """Читать команды, передавать их сервисам и показывать результат."""
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
            elif args.command == "update-rates":
                if updater is None:
                    raise ValueError("Сервис обновления курсов не подключён.")
                print_update_report(updater.run_update(args.source))
            elif args.command == "show-rates":
                if rates is None:
                    raise ValueError("Сервис курсов не подключён.")
                print_rates(rates.list_rates(args.currency, args.top, args.base))
            elif args.command in ("buy", "sell", "show-portfolio", "get-rate"):
                if trading is None:
                    raise ValueError("Сервис операций с валютами не подключён.")
                if args.command in ("buy", "sell"):
                    operation = trading.buy if args.command == "buy" else trading.sell
                    result = operation(args.currency, args.amount)
                    label = "Куплено" if args.command == "buy" else "Продано"
                    print(f"{label}: {result['amount']:.8f} {result['currency']}.")
                    print(f"Оценочная стоимость: {result['estimated_cost']:.2f} USD.")
                    print(f"Баланс: {result['balance']:.8f} {result['currency']}.")
                elif args.command == "show-portfolio":
                    print_portfolio(trading.show_portfolio(args.base or base_currency))
                else:
                    quote = trading.get_rate(args.from_code, args.to_code)
                    print(f"Курс {quote['from']}→{quote['to']}: {quote['rate']:.8f}")
                    print(
                        f"Обновлён: {quote['updated_at']}. Источник: {quote['source']}."
                    )
        except (EOFError, KeyboardInterrupt):
            print("\nДо свидания!")
            break
        except UnicodeError:
            print("Ошибка кодировки ввода. Повторите команду в UTF-8.")
        except (
            ValueError,
            AuthenticationError,
            CurrencyNotFoundError,
            InsufficientFundsError,
            ApiRequestError,
            StorageError,
        ) as error:
            print(f"Ошибка: {error}")


def main():
    """Загрузить настройки и запустить консольный интерфейс."""
    try:
        settings = SettingsLoader()
        database = DatabaseManager(settings.get("data_dir"))
        auth = AuthService(database)
        config = ParserConfig(settings.get("data_dir"))
        storage = RatesStorage(config)
        updater = RatesUpdater(
            {
                "coingecko": CoinGeckoClient(config),
                "exchangerate": ExchangeRateApiClient(config),
            },
            storage,
        )
        rates = RateService(
            settings.get("data_dir"),
            settings.get("rates_ttl_seconds"),
            refresh=updater.run_update,
        )
        trading = TradingService(database, auth, rates)
        run(auth, trading, settings.get("default_base_currency"), updater, rates)
    except (ValueError, CurrencyNotFoundError, StorageError) as error:
        print(f"Не удалось запустить приложение: {error}")
        return 1
    return 0
