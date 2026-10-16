"""Проверки значений, общие для моделей и команд."""

import math


def validate_amount(value, *, allow_zero=False):
    """Проверить сумму и вернуть float; ноль разрешён только для баланса."""
    if type(value) not in (int, float):
        raise ValueError("Сумма должна быть числом.")

    try:
        amount = float(value)
    except OverflowError:
        raise ValueError("Слишком большое значение суммы.") from None

    if not math.isfinite(amount):
        raise ValueError("Сумма должна быть конечным числом.")
    if allow_zero:
        if amount < 0:
            raise ValueError("Баланс не может быть отрицательным.")
    elif amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")

    return amount
