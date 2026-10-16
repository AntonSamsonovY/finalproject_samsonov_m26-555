"""Ошибки, связанные с работой валютного портфеля."""


class CurrencyNotFoundError(Exception):
    """Валюта отсутствует в списке поддерживаемых."""

    def __init__(self, code):
        super().__init__(f"Неизвестная валюта '{code}'")


class InsufficientFundsError(Exception):
    """На кошельке не хватает средств для списания."""

    def __init__(self, available, required, code):
        self.available = available
        self.required = required
        self.code = code
        super().__init__(
            f"Недостаточно средств: доступно {available} {code}, "
            f"требуется {required} {code}"
        )


class StorageError(Exception):
    """Не удалось прочитать или сохранить данные проекта."""
