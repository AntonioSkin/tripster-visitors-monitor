"""Morning delivery policy, evaluated using the actual Almaty clock."""
import os
from datetime import datetime
from monitor import ROOT, TZ


def delivery_allowed(event, now=None, root=ROOT):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if event == "push":
        return False, "Изменение кода: только проверки"
    if event == "schedule" and not (8 <= now.hour < 10):
        return False, "Вне утреннего окна 08:00–10:00 Алматы"
    if (root / f"data/telegram/{now.date().isoformat()}.json").exists():
        return False, "Сегодня отчёт уже отправлен"
    return True, "Можно собирать и отправлять"


if __name__ == "__main__":
    allowed, reason = delivery_allowed(os.environ.get("GITHUB_EVENT_NAME", ""))
    print(reason)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"collect={'true' if allowed else 'false'}\n")
