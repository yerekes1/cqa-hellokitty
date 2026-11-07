"""Итог и флаг. Считает только код, модель в этом не участвует."""
from decimal import Decimal as D, ROUND_HALF_UP


def share(c, v):
    """Да = 1, Нет = 0, шкала = значение / верхняя ступень."""
    if c["type"] == "scale":
        return D(v) / D(max(c["scale"]))
    return D(1) if v is True else D(0)


def final_score(criteria, values, section=None):
    """criteria: список критериев из чек-листа. values: {id: True/False/число/None}.
    section: посчитать только одну секцию. flag и N/A не участвуют."""
    earned, total = D(0), D(0)
    for c in criteria:
        if c["type"] == "flag" or (section and c.get("section") != section):
            continue
        v = values[c["id"]]
        if v is None:
            continue
        total += D(c["weight"])
        earned += D(c["weight"]) * share(c, v)
    if total == 0:
        return None  # нет применимых критериев с весом
    return float((earned / total * 100).quantize(D("0.01"), ROUND_HALF_UP))


def critical_flag(criteria, values):
    """Флаг берётся по типу, а не по имени. None, если в чек-листе нет критерия типа flag."""
    flags = [values[c["id"]] for c in criteria if c["type"] == "flag"]
    return any(v is True for v in flags) if flags else None
