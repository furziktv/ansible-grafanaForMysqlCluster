"""Return only safe, actionable details from Grafana Telegram delivery logs."""

import re


_REASONS = (
    ("message is too long", "Сообщение длиннее лимита Telegram"),
    ("message text is empty", "Grafana отправляет пустой текст"),
    ("chat not found", "Telegram не находит Chat ID для этого бота"),
    ("bot was blocked", "Бот заблокирован адресатом"),
    ("can't parse entities", "Ошибка форматирования сообщения Telegram"),
    ("can't send messages", "Боту запрещена отправка в этот чат"),
    ("message thread not found", "Telegram не находит тему чата"),
    ("not enough rights", "У бота недостаточно прав в чате"),
)


def grafana_telegram_log_report(lines):
    """Classify Telegram failures without returning raw logs, URLs or secrets."""
    relevant = []
    for line in lines:
        low = line.lower()
        if not any(term in low for term in
                   ("telegram", "webhook failed", "bad request data")):
            continue
        if not any(term in low for term in
                   ("400 bad request", "statuscode=400", "bad request data",
                    "failed to send", "webhook failed")):
            continue
        reason = next((safe for needle, safe in _REASONS if needle in low), None)
        if reason is None and "bad request data" in low:
            reason = "Grafana отклонила диагностический API-запрос до отправки"
        if reason is None and ("400 bad request" in low or "statuscode=400" in low):
            reason = "Telegram ответил HTTP 400; подробность не записана"
        if reason is None:
            reason = "Ошибка отправки Telegram; подробность не записана"
        match = re.search(r'\b(?:t|time)=(?:"?)(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)', line)
        relevant.append({"время_UTC": match.group(1) if match else "не указано",
                         "причина": reason})
    return relevant[-12:]


class FilterModule:
    def filters(self):
        return {"grafana_telegram_log_report": grafana_telegram_log_report}
