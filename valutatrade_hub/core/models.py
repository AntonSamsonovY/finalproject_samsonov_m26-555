"""Пользователи и модели валютного портфеля."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

MIN_PASSWORD_LENGTH = 4


class User:
    """Пользователь с хешем пароля и датой регистрации."""

    def __init__(self, user_id, username, hashed_password, salt, registration_date):
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("ID пользователя должен быть положительным целым числом.")
        if (
            not isinstance(hashed_password, str)
            or len(hashed_password) != 64
            or any(char not in "0123456789abcdef" for char in hashed_password)
        ):
            raise ValueError("Некорректный хеш пароля.")
        if not isinstance(salt, str) or not salt:
            raise ValueError("Соль пароля не может быть пустой.")
        if not isinstance(registration_date, datetime):
            raise ValueError("Дата регистрации должна быть datetime.")

        self._user_id = user_id
        self.username = username
        self._hashed_password = hashed_password
        self._salt = salt
        if registration_date.tzinfo is None:
            registration_date = registration_date.replace(tzinfo=UTC)
        self._registration_date = registration_date.astimezone(UTC)

    @staticmethod
    def _check_password(password):
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError("Пароль должен быть не короче 4 символов.")

    @staticmethod
    def _hash_password(password, salt):
        return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()

    @classmethod
    def create(cls, user_id, username, password):
        """Создать пользователя, не сохраняя открытый пароль."""
        cls._check_password(password)
        salt = secrets.token_hex(16)
        hashed_password = cls._hash_password(password, salt)
        return cls(user_id, username, hashed_password, salt, datetime.now(UTC))

    @property
    def user_id(self):
        """ID пользователя без возможности изменить его."""
        return self._user_id

    @property
    def username(self):
        """Имя пользователя."""
        return self._username

    @username.setter
    def username(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Имя пользователя не может быть пустым.")
        self._username = value.strip()

    @property
    def hashed_password(self):
        """Хеш для сохранения в файл, не для вывода в консоль."""
        return self._hashed_password

    @property
    def salt(self):
        """Соль, необходимая для проверки пароля после загрузки."""
        return self._salt

    @property
    def registration_date(self):
        """Дата регистрации в UTC."""
        return self._registration_date

    def get_user_info(self):
        """Вернуть сведения о пользователе без хеша и соли."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "registration_date": self.registration_date.isoformat(),
        }

    def verify_password(self, password):
        """Проверить пароль по сохранённой соли и хешу."""
        if not isinstance(password, str):
            return False
        candidate = self._hash_password(password, self.salt)
        return hmac.compare_digest(candidate, self.hashed_password)

    def change_password(self, new_password):
        """Заменить пароль и сгенерировать для него новую соль."""
        self._check_password(new_password)
        salt = secrets.token_hex(16)
        hashed_password = self._hash_password(new_password, salt)
        self._salt = salt
        self._hashed_password = hashed_password

    def to_dict(self):
        """Подготовить запись для users.json."""
        return {
            **self.get_user_info(),
            "hashed_password": self.hashed_password,
            "salt": self.salt,
        }

    @classmethod
    def from_dict(cls, data):
        """Загрузить пользователя из записи JSON, проверив её поля."""
        if not isinstance(data, dict):
            raise ValueError("Запись пользователя должна быть словарём.")
        try:
            return cls(
                data["user_id"],
                data["username"],
                data["hashed_password"],
                data["salt"],
                datetime.fromisoformat(data["registration_date"]),
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("Некорректная запись пользователя.") from None
