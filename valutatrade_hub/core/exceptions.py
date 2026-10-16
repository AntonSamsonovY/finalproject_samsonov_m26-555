"""Ошибки, связанные с работой валютного портфеля."""


class CurrencyNotFoundError(Exception):
    """Валюта отсутствует в списке поддерживаемых."""

    def __init__(self, code):
        super().__init__(f"Неизвестная валюта '{code}'")
