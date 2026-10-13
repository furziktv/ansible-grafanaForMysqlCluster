"""Проверить доставку Telegram через прокси, не печатая секреты."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def main():
    credentials = json.load(sys.stdin)
    token = str(credentials["token"])
    chat_id = str(credentials["chat_id"])
    base = f"https://api.telegram.org/bot{token}/"
    result = {}

    for method, parameters in (
        ("getMe", None),
        ("getChat", {"chat_id": chat_id}),
        (
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Проверка доставки уведомлений MySQL Cluster",
                "disable_notification": "true",
            },
        ),
    ):
        if method == "sendMessage" and not result.get("getMe", {}).get("ok"):
            result[method] = {"skipped": "бот не прошёл getMe"}
            continue

        data = urllib.parse.urlencode(parameters).encode() if parameters else None
        request = urllib.request.Request(base + method, data=data)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            try:
                payload = json.load(error)
            except (ValueError, OSError):
                payload = {}
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            result[method] = {"ok": False, "reason": type(error).__name__}
            continue

        description = str(payload.get("description", ""))
        description = description.replace(token, "[скрыто]").replace(chat_id, "[скрыто]")
        result[method] = {
            "ok": bool(payload.get("ok")),
            "http": status,
            "reason": description[:240],
        }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
