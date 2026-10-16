"""Проверки регистрации и сессии пользователя."""

from unittest.mock import patch

import pytest

from valutatrade_hub.core.exceptions import AuthenticationError, StorageError
from valutatrade_hub.core.usecases import AuthService
from valutatrade_hub.infra.database import DatabaseManager


def test_register_and_login_after_restart(tmp_path):
    service = AuthService(DatabaseManager(tmp_path))
    info = service.register("Антон", "test-password")
    assert set(info) == {"user_id", "username", "registration_date"}
    assert info["user_id"] == 1
    assert service._database.load()["portfolios"] == [{"user_id": 1, "wallets": {}}]
    with pytest.raises(AuthenticationError):
        service.current_user()

    restarted = AuthService(DatabaseManager(tmp_path))
    assert restarted.login("Антон", "test-password")["user_id"] == 1
    assert restarted.current_user()["username"] == "Антон"
    restarted.logout()
    with pytest.raises(AuthenticationError):
        restarted.current_user()


def test_failed_registration_does_not_change_data(tmp_path):
    database = DatabaseManager(tmp_path)
    service = AuthService(database)
    service.register("anton", "test-password")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    for name, password in (("anton", "other-password"), ("ivan", "123"), (" ", "1234")):
        with pytest.raises(ValueError):
            service.register(name, password)
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert service.register("ivan", "test-password")["user_id"] == 2


def test_failed_login_clears_previous_session(tmp_path):
    service = AuthService(DatabaseManager(tmp_path))
    service.register("anton", "test-password")
    for name, password in (("anton", "wrong-password"), ("nobody", "1234")):
        service.login("anton", "test-password")
        with pytest.raises(AuthenticationError):
            service.login(name, password)
        with pytest.raises(AuthenticationError):
            service.current_user()


def test_name_is_trimmed_and_case_sensitive(tmp_path):
    service = AuthService(DatabaseManager(tmp_path))
    service.register(" anton ", "test-password")
    assert service.login(" anton ", "test-password")["username"] == "anton"
    with pytest.raises(ValueError):
        service.register("anton", "test-password")
    assert service.register("Anton", "test-password")["user_id"] == 2


def test_save_failure_does_not_open_session(tmp_path):
    database = DatabaseManager(tmp_path)
    service = AuthService(database)
    with patch.object(database, "save", side_effect=StorageError("test failure")):
        with pytest.raises(StorageError):
            service.register("anton", "test-password")
    with pytest.raises(AuthenticationError):
        service.current_user()
    assert database.load()["users"] == []
