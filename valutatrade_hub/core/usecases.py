"""Регистрация, вход и операции с валютным портфелем."""

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import AuthenticationError, StorageError
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.core.utils import validate_amount


class AuthService:
    """Регистрация и вход без привязки к консольному вводу."""

    def __init__(self, database):
        self._database = database
        self._current_user = None

    def register(self, username, password):
        """Сохранить нового пользователя и его пустой портфель."""
        data = self._database.load()
        user_id = max((record["user_id"] for record in data["users"]), default=0) + 1
        user = User.create(user_id, username, password)

        if any(record["username"] == user.username for record in data["users"]):
            raise ValueError(f"Имя пользователя '{user.username}' уже занято.")

        data["users"].append(user.to_dict())
        data["portfolios"].append(Portfolio(user).to_dict())
        self._database.save(data)
        return user.get_user_info()

    def login(self, username, password):
        """Проверить пароль и запомнить пользователя до выхода из приложения."""
        self._current_user = None
        if not isinstance(username, str) or not username.strip():
            raise ValueError("Имя пользователя не может быть пустым.")
        username = username.strip()
        data = self._database.load()

        for record in data["users"]:
            if record["username"] == username:
                user = User.from_dict(record)
                if not user.verify_password(password):
                    raise AuthenticationError("Неверный пароль.")
                self._current_user = user
                return user.get_user_info()

        raise AuthenticationError(f"Пользователь '{username}' не найден.")

    def logout(self):
        """Завершить текущую сессию."""
        self._current_user = None

    def current_user(self):
        """Получить сведения о вошедшем пользователе без пароля и хеша."""
        if self._current_user is None:
            raise AuthenticationError("Сначала выполните login.")
        return self._current_user.get_user_info()


class TradingService:
    """Работа с кошельками вошедшего пользователя и оценка их стоимости."""

    def __init__(self, database, auth, rates):
        self._database = database
        self._auth = auth
        self._rates = rates

    def _load_portfolio(self):
        user_id = self._auth.current_user()["user_id"]
        data = self._database.load()
        for record in data["users"]:
            if record["user_id"] == user_id:
                user = User.from_dict(record)
                break
        else:
            raise AuthenticationError(
                "Пользователь больше не существует. Войдите снова."
            )
        for index, record in enumerate(data["portfolios"]):
            if record["user_id"] == user_id:
                return data, index, Portfolio.from_dict(record, user)
        raise StorageError("Не найден портфель пользователя.")

    def get_rate(self, from_code, to_code):
        """Получить курс; вход для этой команды не нужен."""
        left = get_currency(from_code).code
        right = get_currency(to_code).code
        return {"from": left, "to": right, **self._rates.get_rate(left, right)}

    def _trade(self, action, currency_code, amount):
        data, index, portfolio = self._load_portfolio()
        code = get_currency(currency_code).code
        amount = validate_amount(amount)
        if action == "sell":
            wallet = portfolio.get_wallet(code)
            wallet.withdraw(amount)
        else:
            wallet = portfolio.add_currency(code)
            wallet.deposit(amount)

        # Изменения пока только в памяти: без свежего курса файл не сохраняем.
        quote = self._rates.get_rate(code, "USD")
        estimated_cost = validate_amount(amount * quote["rate"])
        data["portfolios"][index] = portfolio.to_dict()
        self._database.save(data)
        return {
            "currency": code,
            "amount": amount,
            "balance": wallet.balance,
            "rate": quote["rate"],
            "estimated_cost": estimated_cost,
            "base": "USD",
        }

    def buy(self, currency_code, amount):
        """Увеличить баланс валюты и показать оценку покупки в USD."""
        return self._trade("buy", currency_code, amount)

    def sell(self, currency_code, amount):
        """Уменьшить баланс валюты, не допуская отрицательного остатка."""
        return self._trade("sell", currency_code, amount)

    def show_portfolio(self, base_currency="USD"):
        """Оценить все ненулевые кошельки по свежим курсам в выбранной валюте."""
        _, _, portfolio = self._load_portfolio()
        base = get_currency(base_currency).code
        rows = []
        total = 0.0
        for code, wallet in sorted(portfolio.wallets.items()):
            value = 0.0
            if wallet.balance > 0:
                rate = self._rates.get_rate(code, base)["rate"]
                value = validate_amount(wallet.balance * rate)
            total = validate_amount(total + value, allow_zero=True)
            rows.append({"currency": code, "balance": wallet.balance, "value": value})
        return {
            "username": portfolio.user.username,
            "base": base,
            "wallets": rows,
            "total": total,
        }
