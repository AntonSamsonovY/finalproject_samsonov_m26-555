"""Запись результата покупки и продажи без изменения их поведения."""

import logging
import math
import sys
from functools import wraps


def _log_amount(value):
    if type(value) not in (int, float):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _write_event(level, event):
    try:
        logging.getLogger("valutatrade.actions").log(
            level, "Операция с валютой", extra={"action_event": event}
        )
    except Exception:
        # Ошибка журнала не должна превращать успешную покупку в повторную попытку.
        print("Не удалось записать событие в журнал.", file=sys.stderr)


def log_action(function):
    """Записать OK/ERROR; при verbose добавить баланс до и после операции."""

    @wraps(function)
    def wrapper(self, currency_code, amount, *, verbose=False):
        event = {
            "action": function.__name__,
            "user": None,
            "user_id": None,
            "currency": currency_code.upper()
            if isinstance(currency_code, str)
            else None,
            "amount": _log_amount(amount),
            "base": "USD",
            "rate": None,
        }
        if verbose:
            event.update(balance_before=None, balance_after=None)
        try:
            user = self._auth.current_user()
            event.update(user=user["username"], user_id=user["user_id"])
            result = function(self, currency_code, amount, verbose=verbose)
        except Exception as error:
            event.update(
                result="ERROR", error_type=type(error).__name__, error=str(error)
            )
            _write_event(logging.ERROR, event)
            raise
        event.update(
            currency=result["currency"],
            amount=result["amount"],
            rate=result["rate"],
            result="OK",
        )
        if verbose:
            event.update(
                balance_before=result["balance_before"], balance_after=result["balance"]
            )
        _write_event(logging.INFO, event)
        return result

    return wrapper
