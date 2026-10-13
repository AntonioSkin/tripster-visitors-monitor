"""Daily delivery: prefer morning, but never discard a delayed daytime run."""
import os
from datetime import datetime
from monitor import ROOT, TZ


def delivery_allowed(event, now=None, root=ROOT):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if event == "push" and os.environ.get("COLLECT_NOW") != "true":
        return False, "Изменение кода: только проверки"
    if event == "schedule" and now.hour < 8:
        return False, "До 08:00 Алматы: ждём утреннего запуска"
    if (root / f"data/telegram/{now.date().isoformat()}.json").exists():
        return False, "Сегодня отчёт уже отправлен"
    return True, "Можно собирать и отправлять"


if __name__ == "__main__":
    allowed, reason = delivery_allowed(os.environ.get("GITHUB_EVENT_NAME", ""))
    print(reason)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"collect={'true' if allowed else 'false'}\n")
