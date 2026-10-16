"""Пользователи и модели валютного портфеля."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import InsufficientFundsError
from valutatrade_hub.core.utils import validate_amount

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


class Wallet:
    """Баланс пользователя в одной валюте."""

    def __init__(self, currency_code, balance=0.0):
        self._currency_code = get_currency(currency_code).code
        self.balance = balance

    @property
    def currency_code(self):
        """Код валюты кошелька без возможности изменить его."""
        return self._currency_code

    @property
    def balance(self):
        """Текущий баланс."""
        return self._balance

    @balance.setter
    def balance(self, value):
        self._balance = validate_amount(value, allow_zero=True)

    def deposit(self, amount):
        """Пополнить кошелёк на положительную сумму."""
        amount = validate_amount(amount)
        self.balance = self.balance + amount

    def withdraw(self, amount):
        """Списать сумму, если её хватает на балансе."""
        amount = validate_amount(amount)
        if amount > self.balance:
            raise InsufficientFundsError(self.balance, amount, self.currency_code)
        self.balance = self.balance - amount

    def get_balance_info(self):
        """Вернуть строку с валютой и остатком."""
        return f"{self.currency_code}: {self.balance:.8f}"

    def to_dict(self):
        """Подготовить данные кошелька для сохранения."""
        return {"currency_code": self.currency_code, "balance": self.balance}


class Portfolio:
    """Все валютные кошельки одного пользователя."""

    def __init__(self, user, wallets=None):
        if not isinstance(user, User):
            raise ValueError("Для портфеля нужен объект пользователя.")
        if wallets is None:
            wallets = {}
        if not isinstance(wallets, dict):
            raise ValueError("Кошельки должны быть словарём.")

        self._user = user
        self._user_id = user.user_id
        self._wallets = {}
        for code, wallet in wallets.items():
            if not isinstance(wallet, Wallet) or code != wallet.currency_code:
                raise ValueError("Код кошелька должен совпадать с ключом словаря.")
            self._wallets[code] = wallet

    @property
    def user(self):
        """Владелец портфеля без возможности его заменить."""
        return self._user

    @property
    def user_id(self):
        """ID владельца для сохранения портфеля."""
        return self._user_id

    @property
    def wallets(self):
        """Вернуть копию словаря кошельков."""
        return self._wallets.copy()

    def add_currency(self, currency_code):
        """Создать кошелёк, если его ещё нет, и вернуть его."""
        code = get_currency(currency_code).code
        if code not in self._wallets:
            self._wallets[code] = Wallet(code)
        return self._wallets[code]

    def get_wallet(self, currency_code):
        """Получить существующий кошелёк по коду валюты."""
        code = get_currency(currency_code).code
        if code not in self._wallets:
            raise ValueError(f"У вас нет кошелька '{code}'.")
        return self._wallets[code]

    @staticmethod
    def _rate_to_usd(code, exchange_rates):
        if code == "USD":
            return 1.0
        pair = f"{code}_USD"
        if pair not in exchange_rates:
            raise ValueError(f"Нет курса {code}→USD.")
        return validate_amount(exchange_rates[pair])

    def get_total_value(self, base_currency="USD", exchange_rates=None):
        """Посчитать стоимость портфеля по переданным курсам вида EUR_USD."""
        base = get_currency(base_currency).code
        if exchange_rates is None:
            exchange_rates = {}
        if not isinstance(exchange_rates, dict):
            raise ValueError("Курсы должны быть словарём.")

        total = 0.0
        for wallet in self._wallets.values():
            if wallet.balance == 0:
                continue
            if wallet.currency_code == base:
                rate = 1.0
            else:
                # Обе валюты сравниваем с USD, затем получаем курс между ними.
                source_rate = self._rate_to_usd(wallet.currency_code, exchange_rates)
                base_rate = self._rate_to_usd(base, exchange_rates)
                rate = validate_amount(source_rate / base_rate)
            value = validate_amount(wallet.balance * rate, allow_zero=True)
            total = validate_amount(total + value, allow_zero=True)
        return total

    def to_dict(self):
        """Подготовить портфель для portfolios.json."""
        return {
            "user_id": self.user_id,
            "wallets": {
                code: {"balance": wallet.balance}
                for code, wallet in self._wallets.items()
            },
        }

    @classmethod
    def from_dict(cls, data, user):
        """Загрузить кошельки, проверив владельца и балансы."""
        if not isinstance(user, User):
            raise ValueError("Для портфеля нужен объект пользователя.")
        if not isinstance(data, dict):
            raise ValueError("Запись портфеля должна быть словарём.")
        user_id = data.get("user_id")
        if type(user_id) is not int or user_id != user.user_id:
            raise ValueError("Портфель принадлежит другому пользователю.")
        saved_wallets = data.get("wallets")
        if not isinstance(saved_wallets, dict):
            raise ValueError("Кошельки должны быть словарём.")

        wallets = {}
        for code, entry in saved_wallets.items():
            if not isinstance(entry, dict) or "balance" not in entry:
                raise ValueError("Некорректная запись кошелька.")
            if entry.get("currency_code", code) != code:
                raise ValueError("Код валюты в записи кошелька не совпадает с ключом.")
            wallets[code] = Wallet(code, entry["balance"])
        return cls(user, wallets)
