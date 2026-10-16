"""Проверки JSON-файлов и согласованного сохранения данных."""

import os
from unittest.mock import patch

import pytest

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.infra.database import DatabaseManager, load_json, save_json


def make_data():
    user = User.create(1, "Антон", "test-password")
    portfolio = Portfolio(user)
    portfolio.add_currency("USD").deposit(100)
    return {"users": [user.to_dict()], "portfolios": [portfolio.to_dict()]}


def test_empty_database(tmp_path):
    assert DatabaseManager(tmp_path).load() == {"users": [], "portfolios": []}


def test_save_and_load_after_restart(tmp_path):
    data = make_data()
    DatabaseManager(tmp_path).save(data)
    restored = DatabaseManager(tmp_path).load()
    assert restored == data
    user = User.from_dict(restored["users"][0])
    assert user.verify_password("test-password")
    portfolio = Portfolio.from_dict(restored["portfolios"][0], user)
    assert portfolio.get_wallet("USD").balance == 100
    assert "test-password" not in (tmp_path / "users.json").read_text()
    assert not (tmp_path / ".pending.json").exists()


@pytest.mark.parametrize("text", ["{broken", "", "NaN", "Infinity"])
def test_broken_json_is_not_overwritten(tmp_path, text):
    path = tmp_path / "users.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(StorageError):
        DatabaseManager(tmp_path).load()
    assert path.read_text() == text


def test_invalid_json_value_does_not_replace_file(tmp_path):
    path = tmp_path / "test.json"
    save_json(path, [1])
    with pytest.raises(StorageError):
        save_json(path, [float("nan")])
    assert load_json(path, []) == [1]


def test_failed_file_replace_keeps_old_data(tmp_path):
    path = tmp_path / "test.json"
    save_json(path, [1])
    with patch("valutatrade_hub.infra.database.os.replace", side_effect=OSError):
        with pytest.raises(StorageError):
            save_json(path, [2])
    assert load_json(path, []) == [1]
    assert list(tmp_path.iterdir()) == [path]


def test_restore_interrupted_save(tmp_path):
    data = make_data()
    original_replace = os.replace

    def fail_on_portfolio(source, target):
        if target.name == "portfolios.json":
            raise OSError("test failure")
        original_replace(source, target)

    with patch("valutatrade_hub.infra.database.os.replace", fail_on_portfolio):
        with pytest.raises(StorageError):
            DatabaseManager(tmp_path).save(data)
    assert (tmp_path / ".pending.json").exists()
    assert DatabaseManager(tmp_path).load() == data
    assert not (tmp_path / ".pending.json").exists()


def test_inconsistent_records_do_not_change_files(tmp_path):
    database = DatabaseManager(tmp_path)
    data = make_data()
    database.save(data)
    before = (tmp_path / "users.json").read_bytes()
    data["portfolios"] = []
    with pytest.raises(StorageError):
        database.save(data)
    assert (tmp_path / "users.json").read_bytes() == before


def test_duplicate_user_is_rejected(tmp_path):
    data = make_data()
    data["users"].append(data["users"][0].copy())
    with pytest.raises(StorageError):
        DatabaseManager(tmp_path).save(data)


def test_duplicate_portfolio_is_rejected(tmp_path):
    data = make_data()
    data["portfolios"].append(data["portfolios"][0].copy())
    with pytest.raises(StorageError):
        DatabaseManager(tmp_path).save(data)


@pytest.mark.parametrize("record", [[], {}, {"users": {}, "portfolios": []}])
def test_invalid_data_shape(tmp_path, record):
    with pytest.raises(StorageError):
        DatabaseManager(tmp_path).save(record)
