"""Получение курсов из локального кэша."""

from datetime import UTC, datetime
from pathlib import Path

from valutatrade_hub.core.currencies import CryptoCurrency, get_currency
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    StorageError,
)
from valutatrade_hub.core.utils import validate_amount
from valutatrade_hub.infra.database import load_json
from valutatrade_hub.infra.settings import SettingsLoader


class RateService:
    """Проверить актуальность курса и при необходимости пересчитать его через USD."""

    def __init__(self, data_dir=None, ttl=None, refresh=None, clock=None):
        if data_dir is None or ttl is None:
            settings = SettingsLoader()
            if data_dir is None:
                data_dir = settings.get("data_dir")
            if ttl is None:
                ttl = settings.get("rates_ttl_seconds")
        if type(ttl) is not int or ttl <= 0:
            raise ValueError("TTL курсов должен быть положительным целым числом.")

        self.path = Path(data_dir) / "rates.json"
        self.ttl = ttl
        self.refresh = refresh
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _parse_time(value):
        if not isinstance(value, str):
            raise ValueError("Время обновления должно быть строкой.")
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            raise ValueError("У времени обновления должен быть часовой пояс.")
        try:
            return timestamp.astimezone(UTC)
        except OverflowError:
            raise ValueError("Время выходит за допустимые границы.") from None

    def _load_pairs(self):
        data = load_json(self.path, {"pairs": {}, "last_refresh": None})
        if not isinstance(data, dict) or not isinstance(data.get("pairs"), dict):
            raise StorageError("Неверный формат кэша курсов.")
        return data["pairs"]

    def _read_pair(self, pairs, pair, *, check_freshness=True):
        if pair not in pairs:
            raise ApiRequestError(f"курс {pair} отсутствует. Выполните update-rates")
        entry = pairs[pair]
        try:
            rate = validate_amount(entry["rate"])
            timestamp = self._parse_time(entry["updated_at"])
            source = entry["source"]
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Не указан источник курса.")
        except (KeyError, TypeError, ValueError):
            raise StorageError(f"Некорректная запись курса {pair}.") from None

        age = (self.clock() - timestamp).total_seconds()
        if age < -60:
            raise ApiRequestError(f"у курса {pair} указано время из будущего")
        if check_freshness and age >= self.ttl:
            raise ApiRequestError(f"курс {pair} устарел. Выполните update-rates")
        return {"rate": rate, "updated_at": timestamp.isoformat(), "source": source}

    @staticmethod
    def _divide(first, second):
        try:
            return validate_amount(first / second)
        except (ValueError, OverflowError, ZeroDivisionError):
            raise ApiRequestError("не удалось пересчитать курс") from None

    def _usd_quote(self, code, pairs, *, check_freshness=True):
        if code == "USD":
            return {
                "rate": 1.0,
                "updated_at": self.clock().isoformat(),
                "source": "Identity",
            }
        pair = f"{code}_USD"
        if pair in pairs:
            return self._read_pair(pairs, pair, check_freshness=check_freshness)
        reverse = self._read_pair(pairs, f"USD_{code}", check_freshness=check_freshness)
        return {**reverse, "rate": self._divide(1.0, reverse["rate"])}

    def _get_quote(self, left, right, *, pairs=None, check_freshness=True):
        if left == right:
            return {
                "rate": 1.0,
                "updated_at": self.clock().isoformat(),
                "source": "Identity",
            }
        if pairs is None:
            pairs = self._load_pairs()
        pair = f"{left}_{right}"
        reverse_pair = f"{right}_{left}"
        if pair in pairs:
            return self._read_pair(pairs, pair, check_freshness=check_freshness)
        if reverse_pair in pairs:
            quote = self._read_pair(
                pairs, reverse_pair, check_freshness=check_freshness
            )
            return {**quote, "rate": self._divide(1.0, quote["rate"])}

        # Например, BTC→EUR = BTC→USD / EUR→USD.
        first = self._usd_quote(left, pairs, check_freshness=check_freshness)
        second = self._usd_quote(right, pairs, check_freshness=check_freshness)
        updated_at = min(
            first["updated_at"], second["updated_at"], key=self._parse_time
        )
        sources = {first["source"], second["source"]} - {"Identity"}
        return {
            "rate": self._divide(first["rate"], second["rate"]),
            "updated_at": updated_at,
            "source": ", ".join(sorted(sources)),
        }

    def get_rate(self, from_code, to_code, *, allow_refresh=True):
        """Вернуть свежий курс с одной попыткой обновления, если updater подключён."""
        left = get_currency(from_code).code
        right = get_currency(to_code).code
        try:
            return self._get_quote(left, right)
        except ApiRequestError:
            if self.refresh is None or not allow_refresh:
                raise
            report = self.refresh()
            try:
                return self._get_quote(left, right)
            except ApiRequestError:
                if isinstance(report, dict) and report.get("errors"):
                    details = "; ".join(report["errors"].values())
                    raise ApiRequestError(
                        f"не удалось обновить курс {left}→{right}. {details}"
                    ) from None
                raise

    def list_rates(self, currency=None, top=None, base="USD"):
        """Прочитать кэш без запросов к API; устаревшие строки помечаются отдельно."""
        base = get_currency(base).code
        if currency is not None:
            currency = get_currency(currency).code
        if top is not None and (type(top) is not int or top <= 0):
            raise ValueError("Параметр top должен быть положительным целым числом.")
        pairs = self._load_pairs()
        codes = set()
        try:
            for pair in pairs:
                left, right = pair.split("_")
                for code in (left, right):
                    if get_currency(code).code != code:
                        raise ValueError("Код валюты должен быть в верхнем регистре.")
                    codes.add(code)
        except (AttributeError, ValueError, CurrencyNotFoundError):
            raise StorageError("Некорректная валютная пара в кэше.") from None

        rows = []
        for code in sorted(codes - {base}):
            if currency is not None and code != currency:
                continue
            if top is not None and not isinstance(get_currency(code), CryptoCurrency):
                continue
            quote = self._get_quote(code, base, pairs=pairs, check_freshness=False)
            age = (self.clock() - self._parse_time(quote["updated_at"])).total_seconds()
            rows.append({"currency": code, **quote, "stale": age >= self.ttl})
        if top is not None:
            rows.sort(key=lambda row: (-row["rate"], row["currency"]))
            rows = rows[:top]
        return {"base": base, "rates": rows}
