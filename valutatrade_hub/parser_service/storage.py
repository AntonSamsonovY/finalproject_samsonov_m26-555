"""Кэш курсов и история полученных измерений."""

import copy
import json
from datetime import UTC, datetime

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import CurrencyNotFoundError, StorageError
from valutatrade_hub.core.utils import validate_amount
from valutatrade_hub.infra.database import load_json, save_json
from valutatrade_hub.parser_service.config import ParserConfig


def parse_timestamp(value):
    """Разобрать ISO-время с обязательным часовым поясом."""
    if not isinstance(value, str):
        raise ValueError("Время должно быть строкой ISO.")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("У времени должен быть часовой пояс.")
    try:
        return timestamp.astimezone(UTC)
    except OverflowError:
        raise ValueError("Время выходит за допустимые границы.") from None


def validate_quotes(pairs):
    """Проверить пакет курсов и привести время к UTC до сохранения."""
    if not isinstance(pairs, dict):
        raise ValueError("Курсы должны быть словарём.")
    result = {}
    try:
        for pair, entry in pairs.items():
            left, right = pair.split("_")
            if f"{get_currency(left).code}_{get_currency(right).code}" != pair:
                raise ValueError("Коды валют должны быть в верхнем регистре.")
            rate = validate_amount(entry["rate"])
            timestamp = parse_timestamp(entry["updated_at"]).isoformat()
            source = entry["source"]
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Не указан источник курса.")
            quote = {"rate": rate, "updated_at": timestamp, "source": source.strip()}
            if "meta" in entry:
                if not isinstance(entry["meta"], dict):
                    raise ValueError("Метаданные должны быть словарём.")
                json.dumps(entry["meta"], allow_nan=False)
                quote["meta"] = copy.deepcopy(entry["meta"])
                if "provider_updated_at" in quote["meta"]:
                    quote["meta"]["provider_updated_at"] = parse_timestamp(
                        quote["meta"]["provider_updated_at"]
                    ).isoformat()
            result[pair] = quote
    except (AttributeError, KeyError, TypeError, CurrencyNotFoundError):
        raise ValueError("Некорректная запись курса.") from None
    return result


def make_history_record(pair, quote):
    """Подготовить измерение с ID из валютной пары и времени получения."""
    left, right = pair.split("_")
    record = {
        "id": f"{pair}_{quote['updated_at']}",
        "from_currency": left,
        "to_currency": right,
        "rate": quote["rate"],
        "timestamp": quote["updated_at"],
        "source": quote["source"],
    }
    if "meta" in quote:
        record["meta"] = copy.deepcopy(quote["meta"])
    return record


class RatesStorage:
    """Сохранить историю и кэш, не теряя предыдущие пары при частичном обновлении."""

    def __init__(self, config=None):
        self.config = config if config is not None else ParserConfig()
        self.pending_file = self.config.rates_file.with_name(".rates_pending.json")

    @staticmethod
    def _check_snapshot(cache, history):
        try:
            if not isinstance(cache, dict) or "pairs" not in cache:
                raise ValueError("Неверный формат кэша.")
            cache = {
                "pairs": validate_quotes(cache["pairs"]),
                "last_refresh": cache.get("last_refresh"),
            }
            if cache["last_refresh"] is not None:
                cache["last_refresh"] = parse_timestamp(
                    cache["last_refresh"]
                ).isoformat()
            if not isinstance(history, list):
                raise ValueError("История должна быть списком.")
            checked_history = []
            seen = set()
            for record in history:
                pair = f"{record['from_currency']}_{record['to_currency']}"
                quote = {
                    "rate": record["rate"],
                    "updated_at": record["timestamp"],
                    "source": record["source"],
                }
                if "meta" in record:
                    quote["meta"] = record["meta"]
                normalized = make_history_record(
                    pair, validate_quotes({pair: quote})[pair]
                )
                if record["id"] != normalized["id"] or record["id"] in seen:
                    raise ValueError("Неверный или повторяющийся ID измерения.")
                seen.add(record["id"])
                checked_history.append(normalized)
            return cache, checked_history
        except (KeyError, TypeError, ValueError):
            raise StorageError("Некорректные данные кэша или истории курсов.") from None

    def _finish_pending_save(self):
        if not self.pending_file.exists():
            return
        pending = load_json(self.pending_file, None)
        if not isinstance(pending, dict) or set(pending) != {"cache", "history"}:
            raise StorageError("Некорректный снимок обновления курсов.")
        cache, history = self._check_snapshot(pending["cache"], pending["history"])
        # Сначала история: каждый курс в новом кэше уже будет иметь запись в ней.
        save_json(self.config.history_file, history)
        save_json(self.config.rates_file, cache)
        try:
            self.pending_file.unlink()
        except OSError:
            raise StorageError("Не удалось завершить сохранение курсов.") from None

    def load(self):
        """Загрузить оба файла, завершив прерванное обновление при необходимости."""
        self._finish_pending_save()
        cache = load_json(self.config.rates_file, {"pairs": {}, "last_refresh": None})
        history = load_json(self.config.history_file, [])
        return self._check_snapshot(cache, history)

    def save(self, pairs):
        """Объединить новые курсы со старыми и вернуть количество изменений."""
        pairs = validate_quotes(pairs)
        cache, history = self.load()
        history_by_id = {record["id"]: record for record in history}
        updated = 0
        added = 0
        for pair, quote in pairs.items():
            previous = cache["pairs"].get(pair)
            if previous is not None and parse_timestamp(
                quote["updated_at"]
            ) <= parse_timestamp(previous["updated_at"]):
                continue
            if previous is not None and previous["source"] == quote["source"]:
                old_time = previous.get("meta", {}).get("provider_updated_at")
                new_time = quote.get("meta", {}).get("provider_updated_at")
                if (
                    old_time
                    and new_time
                    and parse_timestamp(new_time) < parse_timestamp(old_time)
                ):
                    continue
            record = make_history_record(pair, quote)
            if record["id"] in history_by_id:
                if record != history_by_id[record["id"]]:
                    raise StorageError("Измерения с одним ID содержат разные данные.")
            else:
                history.append(record)
                history_by_id[record["id"]] = record
                added += 1
            cache["pairs"][pair] = quote
            updated += 1

        if updated:
            times = [quote["updated_at"] for quote in cache["pairs"].values()]
            if cache["last_refresh"] is not None:
                times.append(cache["last_refresh"])
            cache["last_refresh"] = max(times, key=parse_timestamp)
            save_json(self.pending_file, {"cache": cache, "history": history})
            self._finish_pending_save()
        return {
            "updated": updated,
            "history_added": added,
            "skipped": len(pairs) - updated,
            "cached": len(cache["pairs"]),
        }
