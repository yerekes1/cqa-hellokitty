"""Факты о диалоге, которые считает код, чтобы модель не считала их сама."""

END_EVENTS = {
    "Оператор закрыл чат.": "operator_closed",
    "Оператор чатты жапты.": "operator_closed",
    "Agent closed the chat.": "operator_closed",
    "Соединение с клиентом прервано.": "lost",
    "Клиентпен байланыс үзілді.": "lost",
    "Connection to customer lost.": "lost",
    "Клиент покинул чат.": "client_left",
    "Клиент завершил разговор.": "client_ended",
}
IDENTITY = {
    "Личность клиента подтверждена.",
    "Клиенттің жеке басы расталды.",
    "Customer identity verified.",
}
HOLD_ON, HOLD_OFF = "Клиент находится на удержании.", "Клиент больше не находится на удержании."


def scene_facts(transcript):
    """Номера реплик с 1. Неизвестная фраза ничего не ломает: модель видит весь текст."""
    f = {"first_operator": None, "identity_turn": None, "end_turn": None, "end_kind": None,
         "last_operator": None, "after_end": [], "hold": []}
    hold_start = None
    for n, t in enumerate(transcript, 1):
        sp, tx = t["speaker"], t["text"].strip()
        if f["end_turn"]:  # всё после конца разговора не считается
            f["after_end"].append(n)
            continue
        if sp == "operator":
            f["first_operator"] = f["first_operator"] or n
            if hold_start is None:  # на удержании клиент не слышит
                f["last_operator"] = n
        elif sp == "system":
            if tx in IDENTITY and not f["identity_turn"]:
                f["identity_turn"] = n
            elif tx == HOLD_ON:
                hold_start = n
            elif tx == HOLD_OFF and hold_start:
                f["hold"].append([hold_start, n])
                hold_start = None
            elif tx in END_EVENTS:
                f["end_turn"], f["end_kind"] = n, END_EVENTS[tx]
    return f


def facts_text(f):
    """Блок фактов для промпта."""
    lines = [f"Первая реплика оператора: [{f['first_operator']}]."]
    hold = ", ".join(f"[{a}]–[{b}]" for a, b in f["hold"])
    lines.append(f"Удержание: {hold}." if hold else "Удержание: нет.")
    lines.append(f"Системное подтверждение личности: [{f['identity_turn']}]."
                 if f["identity_turn"] else "Системное подтверждение личности: нет.")
    if f["end_turn"]:
        lines.append(f"Событие конца разговора: [{f['end_turn']}], тип {f['end_kind']}.")
    else:
        lines.append("Системного события конца разговора нет.")
    lines.append(f"Последняя реплика оператора до конца: [{f['last_operator']}].")
    if f["after_end"]:
        lines.append("После конца, не учитывать: " + ", ".join(f"[{n}]" for n in f["after_end"]) + ".")
    return "\n".join(lines)
