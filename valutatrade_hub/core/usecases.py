"""Регистрация пользователей и работа с текущей сессией."""

from valutatrade_hub.core.exceptions import AuthenticationError
from valutatrade_hub.core.models import Portfolio, User


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
