"""Monitor public Tripster calendar; never book or change capacity."""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from monitor import ROOT, write_text
from telegram_report import send_message

EXPERIENCES = [
    {"id": 51584, "title": "Озёра Каинды и Кольсай, Черный и Чарынский каньоны — все сокровища Алматы", "guide": "Антон"},
    {"id": 51691, "title": "Сказочные озёра Каинды и Кольсай + Долина Замков Чарынского каньона и река Чарын в группе", "guide": "Кристина"},
    {"id": 83542, "title": "1 день красоты: каньоны Чарын и Чёрный, озёра Кольсай и Каинды", "guide": "Яна"},
    {"id": 103689, "title": "Чарын, Каинды и Кольсай: путешествие из Алматы", "guide": "Фил"},
]
DEFAULT_EXPERIENCE = EXPERIENCES[0]


def experience_url(experience):
    return f"https://experience.tripster.ru/experience/{experience['id']}/"


def calendar_url(experience):
    return f"https://experience.tripster.ru/api/web/v1/experiences/{experience['id']}/schedule/"


def experience_label(experience):
    return f"{experience['title']}\nГид: {experience['guide']}\nЭкскурсия №{experience['id']}"


THRESHOLD = 7
DAYS = 30
TZ = ZoneInfo("Asia/Almaty")
DIRECTORY = ROOT / "data/seats"


def normalize(payload, now, experience=DEFAULT_EXPERIENCE, full_calendar=False):
    if not isinstance(payload, dict) or not isinstance(payload.get("schedule"), dict):
        raise ValueError("Нет корректного календаря")
    zone = ZoneInfo(payload["timezone"])
    local = now.astimezone(zone)
    start = local.date()
    end = start + timedelta(days=DAYS - 1)
    coverage_end = date.fromisoformat(payload["end"])
    coverage_begin = date.fromisoformat(payload["begin"])
    if full_calendar:
        end = coverage_end
        if end < start:
            raise ValueError("Календарь устарел")
    # begin is the first bookable date and may advance after the booking cutoff.
    # Earlier missing dates remain unknown; they are never treated as zero seats.
    if coverage_end < end or coverage_begin > coverage_end:
        raise ValueError(f"Ответ календаря не покрывает ближайшие 30 дней: begin={coverage_begin}, end={coverage_end}, required={start}..{end}")
    defaults = payload.get("defaults", {})
    result = []
    missing = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        entries = payload["schedule"].get(day.isoformat())
        if not entries:
            missing.append(day.isoformat())
            continue
        if not isinstance(entries, list):
            raise ValueError("Неожиданный формат выездов")
        for entry in entries:
            if entry.get("type") != "slot":
                raise ValueError("Неизвестный тип записи календаря")
            at = datetime.fromisoformat(day.isoformat() + "T" + entry["time"]).replace(tzinfo=zone)
            if at <= local:
                continue
            count = entry.get("available_persons", defaults.get("available_persons"))
            if type(count) is not int or count < 0:
                raise ValueError("Неизвестный остаток мест: не подставляем ноль")
            key = f"{day.isoformat()}T{entry['time']}"
            if any(r["key"] == key for r in result):
                raise ValueError("Неоднозначные выезды с одинаковым временем")
            result.append({"key": key, "date": day.isoformat(), "time": entry["time"],
                           "available": count})
    return {"experience_id": experience["id"], "checked_at": local.isoformat(timespec="seconds"),
            "timezone": payload["timezone"], "date_from": start.isoformat(), "date_to": end.isoformat(),
            "slots": sorted(result, key=lambda r: r["key"]), "dates_without_slots": missing,
            "source": calendar_url(experience)}


def collect(experience=DEFAULT_EXPERIENCE, full_calendar=False):
    endpoint = calendar_url(experience)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(locale="ru-RU", service_workers="block",
                extra_http_headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            page = context.new_page()
            for attempt in range(3):
                try:
                    with page.expect_response(
                        lambda r: r.url.split("?")[0] == endpoint and r.request.method == "GET",
                        timeout=45000,
                    ) as pending:
                        page.goto(experience_url(experience), wait_until="domcontentloaded", timeout=45000)
                    response = pending.value
                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status} при чтении календаря")
                    return normalize(response.json(), datetime.now(TZ), experience, full_calendar=full_calendar)
                except Exception as exc:
                    # Calendar errors contain no Telegram credentials; log only known validation messages.
                    detail = str(exc) if type(exc) is ValueError else type(exc).__name__
                    print(f"Calendar attempt {attempt + 1}/3 failed: {detail}", flush=True)
                    if attempt == 2:
                        raise
                    time.sleep(4)
        finally:
            browser.close()


def changed_low_slots(snapshot, state):
    previous = state.get("notified", {})
    return [slot for slot in snapshot["slots"]
            if slot["available"] < THRESHOLD and previous.get(slot["key"]) != slot["available"]]


def next_notifications(snapshot, state):
    # A hidden/missing departure is unknown, not sold out. Preserve its prior alert.
    notified = {k: v for k, v in state.get("notified", {}).items()
                if snapshot["date_from"] <= k[:10] <= snapshot["date_to"]}
    for slot in snapshot["slots"]:
        if slot["available"] >= THRESHOLD:
            notified.pop(slot["key"], None)
        else:
            notified[slot["key"]] = slot["available"]
    return notified


def alert_text(snapshot, slots, experience=DEFAULT_EXPERIENCE):
    lines = ["Tripster: осталось меньше 7 мест", experience_label(experience), ""]
    for slot in slots:
        day = date.fromisoformat(slot["date"]).strftime("%d.%m.%Y")
        lines.append(f"{day} в {slot['time']} — свободно {slot['available']} мест")
    lines.extend(["", "Проверьте календарь и при необходимости увеличьте количество мест.",
                  "Время выездов — местное, Алматы.", experience_url(experience)])
    return "\n".join(lines)


def save_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run_check(collect_fn=None, send_fn=send_message, directory=DIRECTORY, experience=DEFAULT_EXPERIENCE):
    if collect_fn is None:
        collect_fn = lambda: collect(experience)
    state_path = directory / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise RuntimeError("Нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
    try:
        snapshot = collect_fn()
    except Exception:
        # One error notice per uninterrupted outage. Do not expose browser logs/URLs with secrets.
        save_json(directory / "status.json", {"ok": False, "checked_at": datetime.now(TZ).isoformat(),
                  "message": "Не удалось прочитать полный календарь; старые остатки сохранены"})
        if not state.get("error_notified"):
            send_fn(token, chat, f"{experience_label(experience)}\nНе удалось проверить свободные места. "
                    "Остатки неизвестны. Повторные проверки запланированы каждые 30 минут; возможны задержки запуска.\n" + experience_url(experience))
            state["error_notified"] = True
            save_json(state_path, state)
        raise RuntimeError("Ошибка чтения календаря") from None
    save_json(directory / "latest.json", snapshot)
    low = changed_low_slots(snapshot, state)
    # Send batches safely below Telegram's text size limit.
    for offset in range(0, len(low), 35):
        batch = low[offset:offset + 35]
        send_fn(token, chat, alert_text(snapshot, batch, experience))
        state.setdefault("notified", {}).update({s["key"]: s["available"] for s in batch})
        save_json(state_path, state)
    if not state.get("initialized") and not low:
        counts = [r["available"] for r in snapshot["slots"]]
        summary = f"Минимальный остаток: {min(counts)} мест." if counts else "Открытые выезды не найдены; это не означает ноль мест."
        send_fn(token, chat, f"Мониторинг мест Tripster включён.\n{experience_label(experience)}\n"
                f"Период: {snapshot['date_from']} — {snapshot['date_to']} (30 календарных дней, Алматы).\n"
                f"Порог: 0–6 мест. Проверки запланированы каждые 30 минут.\n"
                f"Проверено выездов: {len(counts)}. {summary}\n" + experience_url(experience))
    elif state.get("error_notified") and not low:
        send_fn(token, chat, f"{experience_label(experience)}\nЧтение календаря восстановлено. "
                "Новых изменений ниже порога 7 мест нет.\n" + experience_url(experience))
    state.update({"initialized": True, "error_notified": False,
                  "notified": next_notifications(snapshot, state),
                  "last_success": snapshot["checked_at"]})
    save_json(state_path, state)
    save_json(directory / "status.json", {"ok": True, "checked_at": snapshot["checked_at"],
                                         "slots": len(snapshot["slots"]), "alerts": len(low)})
    print(f"Экскурсия {experience['id']}: проверено выездов: {len(snapshot['slots'])}; новых уведомлений о низком остатке: {len(low)}")
    report = [f"# Свободные места — Tripster {experience['id']}", "", experience["title"], f"Гид: {experience['guide']}", "",
              f"Проверка: {snapshot['checked_at']}", f"Период: {snapshot['date_from']} — {snapshot['date_to']}",
              "", "| Дата | Время | Свободно |", "|---|---|---:|"]
    report += [f"| {s['date']} | {s['time']} | {s['available']} |" for s in snapshot["slots"]]
    report += ["", "Даты без опубликованных выездов не считаются нулём мест.",
               "Остаток available_persons читается напрямую; booked_persons повторно не вычитается."]
    report_path = ROOT / "reports/seats.md" if experience["id"] == 51584 else directory / "report.md"
    write_text(report_path, "\n".join(report) + "\n")


def run_all(check_fn=run_check, directory=DIRECTORY):
    failed = []
    for experience in EXPERIENCES:
        # Keep the original tour's state in place to avoid duplicate alerts.
        tour_directory = directory if experience["id"] == 51584 else directory / str(experience["id"])
        try:
            check_fn(directory=tour_directory, experience=experience)
        except Exception:
            failed.append(experience["id"])
            print(f"Экскурсия {experience['id']}: проверка завершилась ошибкой; продолжаем остальные.", flush=True)
    if failed:
        raise RuntimeError(f"Не проверены экскурсии: {failed}")


if __name__ == "__main__":
    try:
        run_all()
    except Exception:
        print("Проверка мест завершилась ошибкой. См. data/seats и шаг GitHub Actions.")
        sys.exit(1)
