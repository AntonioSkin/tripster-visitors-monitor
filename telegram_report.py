"""Send a fresh Tripster observation to the explicitly configured Telegram chat."""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from monitor import ROOT, TZ, total

REPORT_URL = "https://github.com/AntonioSkin/tripster-visitors-monitor/blob/main/reports/latest.md"


def format_report(snapshot, previous):
    old = {r["id"]: r for r in previous["results"]} if previous else {}
    checked = datetime.fromisoformat(snapshot["checked_at"]).astimezone(TZ)
    lines = ["Tripster — посетители", checked.strftime("%d.%m.%Y, %H:%M (Алматы)"), ""]
    for r in snapshot["results"]:
        before = old.get(r["id"], {})
        delta = "—"
        if r["status"] == before.get("status") == "ok":
            delta = f"{r['visitors'] - before['visitors']:+d}"
        value = str(r["visitors"]) if r["status"] == "ok" else "Ошибка сбора"
        name = " ".join(r["name"].split())[:180]
        lines.extend([f"{name} [{r['id']}]", f"Посетили: {value} · изменение: {delta}", ""])
    current_total = total(snapshot["results"])
    previous_total = total(previous["results"]) if previous else None
    delta = "—"
    if (current_total is not None and previous_total is not None
            and set(old) == {r["id"] for r in snapshot["results"]}):
        delta = f"{current_total - previous_total:+d}"
    value = str(current_total) if current_total is not None else "Нет полного замера"
    lines.extend([f"ИТОГО: {value}", f"Изменение суммы: {delta}", "",
                  "Изменение — к замеру за вчера. «—» — нет сравнимого замера.",
                  "Сумма — посещения, не уникальные люди; прирост счётчика не равен числу туристов за сутки.",
                  "", REPORT_URL])
    text = "\n".join(lines)
    if len(text.encode("utf-16-le")) // 2 > 4096:
        raise ValueError("Отчёт превышает размер сообщения Telegram")
    return text


def send_message(token, chat_id, text):
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("Проверьте формат TELEGRAM_BOT_TOKEN")
    if not re.fullmatch(r"-?[0-9]+", chat_id):
        raise ValueError("TELEGRAM_CHAT_ID должен быть числовым ID чата")
    data = json.dumps({"chat_id": chat_id, "text": text,
                       "link_preview_options": {"is_disabled": True}}).encode("utf-8")
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Never print exception strings: their URLs contain the bot token.
        raise RuntimeError(f"Telegram HTTP {exc.code}; проверьте секреты и /start у бота") from None
    except Exception:
        # No automatic retries: a timeout might occur after Telegram accepted the message.
        raise RuntimeError("Не удалось подтвердить отправку в Telegram; проверьте чат перед повтором") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Telegram не подтвердил отправку")
    print("Отчёт отправлен в Telegram")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token and not chat_id:
        print("Telegram не подключён: добавьте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в GitHub Secrets")
        return 0
    if not token or not chat_id:
        print("Ошибка настройки: нужны оба секрета TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
        return 1
    try:
        if len(sys.argv) != 2:
            raise ValueError("Нужен путь к снимку текущего запуска")
        snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        yesterday = (date.fromisoformat(snapshot["date"]) - timedelta(days=1)).isoformat()
        previous_path = ROOT / f"data/daily/{yesterday}.json"
        previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else None
        send_message(token, chat_id, format_report(snapshot, previous))
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("Не удалось подготовить Telegram-отчёт")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
