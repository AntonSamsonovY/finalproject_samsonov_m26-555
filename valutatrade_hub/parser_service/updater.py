"""Опрос источников и передача полученных курсов в хранилище."""

import logging
from datetime import UTC, datetime

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.parser_service.storage import parse_timestamp, validate_quotes


class RatesUpdater:
    """Обновлять доступные источники, даже если один из них не отвечает."""

    def __init__(self, clients, storage, logger=None, clock=None):
        self.clients = dict(clients)
        self.storage = storage
        self.logger = (
            logger if logger is not None else logging.getLogger("valutatrade.parser")
        )
        self.clock = clock or (lambda: datetime.now(UTC))

    def run_update(self, source=None):
        """Опросить все источники или один выбранный и вернуть краткий отчёт."""
        if source is not None and source not in self.clients:
            raise ValueError("Неизвестный источник курсов.")
        # Повреждённые файлы проверяем до сетевых запросов, а не затираем ответами API.
        cache, _ = self.storage.load()
        selected = self.clients if source is None else {source: self.clients[source]}
        pairs = {}
        errors = {}
        sources = []
        for name, client in selected.items():
            self.logger.info("Запрос курсов: %s", name)
            try:
                try:
                    fetched = validate_quotes(client.fetch_rates())
                    if not fetched:
                        raise ValueError("Источник не вернул курсы.")
                    for quote in fetched.values():
                        if (
                            parse_timestamp(quote["updated_at"]) - self.clock()
                        ).total_seconds() > 60:
                            raise ValueError("Время получения курса указано в будущем.")
                except ValueError:
                    raise ApiRequestError(
                        f"{name}: некорректный пакет курсов"
                    ) from None
            except ApiRequestError as error:
                errors[name] = str(error)
                self.logger.warning("Не удалось получить курсы: %s", error)
                continue
            for pair, quote in fetched.items():
                previous = pairs.get(pair)
                if previous is None or parse_timestamp(
                    quote["updated_at"]
                ) > parse_timestamp(previous["updated_at"]):
                    pairs[pair] = quote
            sources.append(name)
            self.logger.info("Получено курсов от %s: %s", name, len(fetched))

        if pairs:
            result = self.storage.save(pairs)
        else:
            result = {
                "updated": 0,
                "history_added": 0,
                "skipped": 0,
                "cached": len(cache["pairs"]),
            }
        self.logger.info(
            "Обновлено курсов: %s; ошибок источников: %s",
            result["updated"],
            len(errors),
        )
        return {**result, "sources": sources, "errors": errors}
