"""Проверки пользователя, паролей и подготовки данных для JSON."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from valutatrade_hub.core.models import User


def test_create_user():
    user = User.create(1, " Антон ", "test-password")
    assert user.user_id == 1
    assert user.username == "Антон"
    assert user.registration_date.tzinfo == UTC
    assert (
        user.hashed_password
        == hashlib.sha256(("test-password" + user.salt).encode("utf-8")).hexdigest()
    )


def test_verify_password():
    user = User.create(1, "anton", "test-password")
    assert user.verify_password("test-password")
    assert not user.verify_password("wrong-password")
    assert not user.verify_password(None)


def test_same_password_has_different_hashes():
    first = User.create(1, "anton", "test-password")
    second = User.create(2, "ivan", "test-password")
    assert first.salt != second.salt
    assert first.hashed_password != second.hashed_password


def test_user_info_has_no_password_fields():
    user = User.create(1, "anton", "test-password")
    info = user.get_user_info()
    assert set(info) == {"user_id", "username", "registration_date"}
    info["username"] = "ivan"
    assert user.username == "anton"


def test_change_password():
    user = User.create(1, "anton", "old-password")
    old_salt = user.salt
    user.change_password("новый пароль")
    assert user.verify_password("новый пароль")
    assert not user.verify_password("old-password")
    assert user.salt != old_salt


def test_invalid_password_does_not_replace_old_one():
    user = User.create(1, "anton", "old-password")
    before = user.to_dict()
    with pytest.raises(ValueError):
        user.change_password("123")
    assert user.to_dict() == before
    assert user.verify_password("old-password")


@pytest.mark.parametrize("password", ["", "123", None, 1234])
def test_invalid_password(password):
    with pytest.raises(ValueError):
        User.create(1, "anton", password)


def test_minimum_password_length():
    assert User.create(1, "anton", "1234").verify_password("1234")


@pytest.mark.parametrize("name", ["", "   ", None, 123])
def test_invalid_username(name):
    with pytest.raises(ValueError):
        User.create(1, name, "test-password")


@pytest.mark.parametrize("user_id", [0, -1, True, "1"])
def test_invalid_id(user_id):
    with pytest.raises(ValueError):
        User.create(user_id, "anton", "test-password")


def test_id_is_read_only_and_name_is_checked():
    user = User.create(1, "anton", "test-password")
    with pytest.raises(AttributeError):
        user.user_id = 2
    with pytest.raises(ValueError):
        user.username = " "
    assert user.username == "anton"


def test_json_roundtrip():
    user = User.create(1, "Антон", "test-password")
    saved = json.dumps(user.to_dict(), ensure_ascii=False)
    restored = User.from_dict(json.loads(saved))
    assert "test-password" not in saved
    assert restored.get_user_info() == user.get_user_info()
    assert restored.verify_password("test-password")


@pytest.mark.parametrize("data", [[], {}, {"user_id": 1}])
def test_incomplete_record(data):
    with pytest.raises(ValueError):
        User.from_dict(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("hashed_password", "not-a-hash"),
        ("salt", ""),
        ("registration_date", "not-a-date"),
        ("user_id", True),
    ],
)
def test_corrupted_record(field, value):
    data = User.create(1, "anton", "test-password").to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        User.from_dict(data)


def test_date_without_timezone_is_loaded_as_utc():
    data = User.create(1, "anton", "test-password").to_dict()
    data["registration_date"] = "2026-10-16T12:00:00"
    assert User.from_dict(data).registration_date == datetime(
        2026, 10, 16, 12, tzinfo=UTC
    )
