"""Чтение и сохранение пользователей и портфелей в JSON."""

import copy
import json
import os
import tempfile
from pathlib import Path

from valutatrade_hub.core.exceptions import CurrencyNotFoundError, StorageError
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.infra.settings import SettingsLoader


def _reject_constant(value):
    raise ValueError(f"Недопустимое число в JSON: {value}")


def load_json(path, default):
    """Прочитать JSON; значение по умолчанию нужно только для отсутствующего файла."""
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file, parse_constant=_reject_constant)
    except FileNotFoundError:
        return copy.deepcopy(default)
    except (OSError, ValueError):
        raise StorageError(f"Не удалось прочитать JSON: {path.name}") from None


def save_json(path, data):
    """Сохранить через временный файл, не обрезая старый при ошибке записи."""
    path = Path(path)
    temporary = None
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        raise StorageError(f"Не удалось сохранить JSON: {path.name}") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


class DatabaseManager:
    """Хранить связанные данные одного запущенного приложения."""

    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir or SettingsLoader().get("data_dir"))

    @staticmethod
    def _check_data(data):
        try:
            if not isinstance(data, dict) or set(data) != {"users", "portfolios"}:
                raise ValueError("Нужны пользователи и портфели.")
            if not isinstance(data["users"], list) or not isinstance(
                data["portfolios"], list
            ):
                raise ValueError("Пользователи и портфели должны быть списками.")

            users = [User.from_dict(record) for record in data["users"]]
            users_by_id = {user.user_id: user for user in users}
            names = {user.username for user in users}
            if len(users_by_id) != len(users) or len(names) != len(users):
                raise ValueError("Повторяются ID или имена пользователей.")

            owners = []
            for record in data["portfolios"]:
                user = users_by_id[record["user_id"]]
                portfolio = Portfolio.from_dict(record, user)
                owners.append(portfolio.user_id)
            if len(set(owners)) != len(owners) or set(owners) != set(users_by_id):
                raise ValueError("У каждого пользователя должен быть один портфель.")
        except (KeyError, TypeError, ValueError, CurrencyNotFoundError):
            raise StorageError(
                "Некорректные данные пользователей или портфелей."
            ) from None

    def _finish_pending_save(self):
        pending_file = self.data_dir / ".pending.json"
        if not pending_file.exists():
            return
        data = load_json(pending_file, None)
        self._check_data(data)
        for name in ("users", "portfolios"):
            save_json(self.data_dir / f"{name}.json", data[name])
        try:
            pending_file.unlink()
        except OSError:
            raise StorageError("Не удалось завершить сохранение данных.") from None

    def load(self):
        """Загрузить проверенные записи, завершив прерванное сохранение."""
        self._finish_pending_save()
        data = {
            "users": load_json(self.data_dir / "users.json", []),
            "portfolios": load_json(self.data_dir / "portfolios.json", []),
        }
        self._check_data(data)
        return data

    def save(self, data):
        """Сохранить пользователей и портфели после проверки связей между ними."""
        self._finish_pending_save()
        self._check_data(data)
        # Если процесс остановится между двумя файлами, этот снимок завершит запись.
        save_json(self.data_dir / ".pending.json", data)
        self._finish_pending_save()
