"""История, объединение кэша и восстановление незавершённой записи."""

import copy
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.infra.database import load_json, save_json
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def quote(rate=60000, seconds=0, source="Test", **extra):
    return {
        "rate": rate,
        "updated_at": (NOW + timedelta(seconds=seconds)).isoformat(),
        "source": source,
        **extra,
    }


@pytest.fixture
def storage(tmp_path):
    return RatesStorage(ParserConfig(tmp_path))


def snapshot(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir()}


def test_empty_storage_does_not_create_files(storage, tmp_path):
    assert storage.load() == ({"pairs": {}, "last_refresh": None}, [])
    assert storage.save({}) == {
        "updated": 0,
        "history_added": 0,
        "skipped": 0,
        "cached": 0,
    }
    assert list(tmp_path.iterdir()) == []


def test_first_save_and_read_by_core(storage, tmp_path):
    pairs = {"BTC_USD": quote(meta={"provider_updated_at": NOW.isoformat()})}
    before = copy.deepcopy(pairs)
    assert storage.save(pairs) == {
        "updated": 1,
        "history_added": 1,
        "skipped": 0,
        "cached": 1,
    }
    cache, history = storage.load()
    assert cache == {"pairs": pairs, "last_refresh": NOW.isoformat()}
    assert history == [
        {
            "id": f"BTC_USD_{NOW.isoformat()}",
            "from_currency": "BTC",
            "to_currency": "USD",
            "rate": 60000.0,
            "timestamp": NOW.isoformat(),
            "source": "Test",
            "meta": {"provider_updated_at": NOW.isoformat()},
        }
    ]
    assert not storage.pending_file.exists()
    assert pairs == before
    assert (
        RateService(tmp_path, 300, clock=lambda: NOW).get_rate("BTC", "USD")["rate"]
        == 60000
    )


def test_partial_update_keeps_other_pairs(storage):
    storage.save({"BTC_USD": quote(), "EUR_USD": quote(1.2)})
    storage.save({"BTC_USD": quote(61000, seconds=10)})
    cache, history = storage.load()
    assert cache["pairs"]["EUR_USD"]["rate"] == 1.2
    assert cache["pairs"]["BTC_USD"]["rate"] == 61000
    assert cache["last_refresh"] == (NOW + timedelta(seconds=10)).isoformat()
    assert len(history) == 3
    assert history[0]["rate"] == 60000


@pytest.mark.parametrize("seconds", [-10, 0])
def test_older_or_repeated_quote_does_not_change_files(storage, tmp_path, seconds):
    storage.save({"BTC_USD": quote()})
    before = snapshot(tmp_path)
    result = storage.save({"BTC_USD": quote(50000, seconds=seconds)})
    assert result["updated"] == result["history_added"] == 0
    assert result["skipped"] == 1
    assert snapshot(tmp_path) == before


def test_same_time_in_another_timezone_is_not_duplicated(storage):
    storage.save({"BTC_USD": quote()})
    entry = quote()
    entry["updated_at"] = "2026-10-18T15:00:00+03:00"
    assert storage.save({"BTC_USD": entry})["skipped"] == 1
    assert len(storage.load()[1]) == 1


def test_provider_time_cannot_move_backwards(storage, tmp_path):
    storage.save({"BTC_USD": quote(meta={"provider_updated_at": NOW.isoformat()})})
    before = snapshot(tmp_path)
    older = (NOW - timedelta(hours=1)).isoformat()
    result = storage.save(
        {"BTC_USD": quote(50000, seconds=10, meta={"provider_updated_at": older})}
    )
    assert result["skipped"] == 1
    assert snapshot(tmp_path) == before


def test_fiat_same_daily_quote_can_refresh_receipt_time(storage):
    provider_time = (NOW - timedelta(days=1)).isoformat()
    storage.save({"EUR_USD": quote(1.2, meta={"provider_updated_at": provider_time})})
    result = storage.save(
        {"EUR_USD": quote(1.2, seconds=10, meta={"provider_updated_at": provider_time})}
    )
    assert result["updated"] == 1
    assert len(storage.load()[1]) == 2


@pytest.mark.parametrize("bad", [None, True, 0, -1, "1", float("nan"), float("inf")])
def test_invalid_batch_is_not_partially_saved(storage, tmp_path, bad):
    storage.save({"BTC_USD": quote()})
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        storage.save({"BTC_USD": quote(61000, seconds=10), "EUR_USD": quote(bad)})
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "pairs",
    [
        [],
        {"bad": {}},
        {1: {}},
        {"XYZ_USD": quote()},
        {"btc_USD": quote()},
        {"BTC_USD": None},
        {"BTC_USD": {"rate": 1}},
        {"BTC_USD": quote(source=" ")},
        {"BTC_USD": quote(meta=[])},
        {"BTC_USD": quote(meta={"bad": {1, 2}})},
    ],
)
def test_invalid_quote_shape(storage, tmp_path, pairs):
    with pytest.raises(ValueError):
        storage.save(pairs)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field", ["updated_at", "provider_updated_at"])
@pytest.mark.parametrize(
    "timestamp", [None, "bad", "2026-10-18T12:00:00", "0001-01-01T00:00:00+03:00"]
)
def test_invalid_timestamps(storage, field, timestamp):
    entry = quote()
    if field == "updated_at":
        entry[field] = timestamp
    else:
        entry["meta"] = {field: timestamp}
    with pytest.raises(ValueError):
        storage.save({"BTC_USD": entry})


@pytest.mark.parametrize("filename", ["rates.json", "exchange_rates.json"])
def test_broken_json_is_not_overwritten(storage, tmp_path, filename):
    storage.save({"BTC_USD": quote()})
    (tmp_path / filename).write_text("{broken", encoding="utf-8")
    before = snapshot(tmp_path)
    with pytest.raises(StorageError):
        storage.save({"BTC_USD": quote(61000, seconds=10)})
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "cache,history",
    [
        ([], []),
        ({}, []),
        ({"pairs": [], "last_refresh": None}, []),
        ({"pairs": {}, "last_refresh": "bad"}, []),
        ({"pairs": {}}, {}),
        ({"pairs": {}}, [None]),
        ({"pairs": {}}, [{"id": "bad"}]),
    ],
)
def test_invalid_snapshot(storage, cache, history):
    save_json(storage.config.rates_file, cache)
    save_json(storage.config.history_file, history)
    with pytest.raises(StorageError):
        storage.load()


def test_duplicate_history_id_is_rejected(storage):
    storage.save({"BTC_USD": quote()})
    _, history = storage.load()
    save_json(storage.config.history_file, history + history)
    with pytest.raises(StorageError):
        storage.load()


@pytest.mark.parametrize("filename", ["rates.json", "exchange_rates.json"])
def test_failed_replace_is_recovered_on_next_load(storage, tmp_path, filename):
    storage.save({"BTC_USD": quote()})
    old_cache = storage.config.rates_file.read_bytes()
    import os

    replace = os.replace

    def fail_selected_file(source, target):
        if target.name == filename:
            raise OSError("test failure")
        replace(source, target)

    with patch(
        "valutatrade_hub.infra.database.os.replace", side_effect=fail_selected_file
    ):
        with pytest.raises(StorageError):
            storage.save({"BTC_USD": quote(61000, seconds=10)})
    assert storage.config.rates_file.read_bytes() == old_cache
    assert storage.pending_file.exists()
    assert set(path.name for path in tmp_path.iterdir()) == {
        "rates.json",
        "exchange_rates.json",
        ".rates_pending.json",
    }
    restarted = RatesStorage(storage.config)
    cache, history = restarted.load()
    assert cache["pairs"]["BTC_USD"]["rate"] == 61000
    assert len(history) == 2
    assert not restarted.pending_file.exists()
    restarted.save({"BTC_USD": quote(61000, seconds=10)})
    assert len(restarted.load()[1]) == 2


def test_pending_snapshot_failure_leaves_old_files(storage, tmp_path):
    storage.save({"BTC_USD": quote()})
    before = snapshot(tmp_path)
    with patch(
        "valutatrade_hub.parser_service.storage.save_json",
        side_effect=StorageError("test"),
    ):
        with pytest.raises(StorageError):
            storage.save({"BTC_USD": quote(61000, seconds=10)})
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("pending", [None, [], {}, {"cache": {}, "history": []}])
def test_invalid_pending_snapshot_is_preserved(storage, tmp_path, pending):
    save_json(storage.pending_file, pending)
    before = snapshot(tmp_path)
    with pytest.raises(StorageError):
        storage.load()
    assert snapshot(tmp_path) == before


def test_files_contain_valid_json(storage):
    storage.save({"BTC_USD": quote()})
    assert load_json(storage.config.rates_file, None)["pairs"]
    assert load_json(storage.config.history_file, None)[0]["from_currency"] == "BTC"
