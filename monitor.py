"""Daily snapshots of the visitor counter used by Tripster's public pages."""
import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Almaty")


def parse_api_visitors(payload, experience_id):
    if not isinstance(payload, dict) or type(payload.get("id")) is not int or payload["id"] != experience_id:
        raise ValueError("Ответ относится к другой экскурсии")
    count = payload.get("visitors_count")
    if type(count) is not int or count < 0:
        raise ValueError("Нет корректного счётчика visitors_count")
    return count


def collect(page, tour):
    url = f"https://experience.tripster.ru/experience/{tour['id']}/"
    endpoint = f"https://experience.tripster.ru/api/web/v2/experiences/{tour['id']}/"
    # Let the public page perform its own normal request. No copied tokens,
    # private API calls, stored login, or cached search-engine pages.
    for attempt in range(2):
        try:
            with page.expect_response(
                lambda response: response.url.split("?")[0] == endpoint
                and response.request.method == "GET",
                timeout=30000,
            ) as pending:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            response = pending.value
            if response.status != 200:
                raise ValueError(f"Tripster HTTP {response.status}")
            count = parse_api_visitors(response.json(), tour["id"])
            return {
                "id": tour["id"], "name": tour["name"], "url": url,
                "visitors": count, "status": "ok",
                "source": endpoint, "source_field": "visitors_count",
                "checked_at": datetime.now(TZ).isoformat(timespec="seconds"),
            }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                time.sleep(3)
    return {
        "id": tour["id"], "name": tour["name"], "url": url,
        "visitors": None, "status": "error", "error": error[:600],
        "checked_at": datetime.now(TZ).isoformat(timespec="seconds"),
    }


def total(results):
    if not results or any(r["status"] != "ok" for r in results):
        return None
    return sum(r["visitors"] for r in results)


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def make_report(snapshot, previous):
    old = {r["id"]: r for r in previous["results"]} if previous else {}
    lines = [
        "# Посетители экскурсий Tripster", "",
        f"Замер: **{snapshot['checked_at']}** (Алматы).", "",
        "| Экскурсия | Всего посетили | Изменение за сутки |",
        "|---|---:|---:|",
    ]
    for r in snapshot["results"]:
        before = old.get(r["id"], {})
        diff = "—"
        if r["status"] == before.get("status") == "ok":
            diff = f"{r['visitors'] - before['visitors']:+d}"
        value = str(r["visitors"]) if r["status"] == "ok" else "Ошибка сбора"
        label = r["name"].replace("|", "/").replace("\n", " ")
        lines.append(f"| [{label}]({r['url']}) | {value} | {diff} |")
    current_total = total(snapshot["results"])
    previous_total = total(previous["results"]) if previous else None
    same_ids = set(old) == {r["id"] for r in snapshot["results"]}
    delta = "—"
    if current_total is not None and previous_total is not None and same_ids:
        delta = f"{current_total - previous_total:+d}"
    summary = str(current_total) if current_total is not None else "Нет полного замера"
    lines += [
        f"| **Итого** | **{summary}** | **{delta}** |", "",
        "Изменение считается относительно замера за предыдущую календарную дату. "
        "«—» означает отсутствие сравнимого замера. Это изменение публичного счётчика, "
        "а не подтверждённое число туристов за сутки. Сумма не является числом уникальных людей.",
        "", "[История по датам (CSV)](../data/daily.csv)", "",
    ]
    for r in snapshot["results"]:
        if r["status"] != "ok":
            lines.append(f"- Ошибка {r['id']}: {r['error'].replace(chr(10), ' ')}")
    return "\n".join(lines) + "\n"


def refresh_csv(tours):
    fields = ["date", "checked_at"] + [str(t["id"]) for t in tours] + ["total", "status"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for path in sorted((ROOT / "data/daily").glob("*.json")):
        snap = json.loads(path.read_text(encoding="utf-8"))
        by_id = {r["id"]: r for r in snap["results"]}
        selected = [by_id.get(t["id"], {"status": "missing"}) for t in tours]
        row = {"date": snap["date"], "checked_at": snap["checked_at"],
               "total": total(selected), "status": "ok" if total(selected) is not None else "incomplete"}
        row.update({str(t["id"]): by_id.get(t["id"], {}).get("visitors") for t in tours})
        writer.writerow(row)
    write_text(ROOT / "data/daily.csv", "\ufeff" + stream.getvalue())


def main():
    tours = json.loads((ROOT / "excursions.json").read_text(encoding="utf-8"))
    ids = [t["id"] for t in tours]
    if not tours or len(set(ids)) != len(ids) or any(not isinstance(i, int) or i <= 0 for i in ids):
        raise ValueError("Нужны уникальные положительные ID экскурсий")
    now = datetime.now(TZ)
    results = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            locale="ru-RU", service_workers="block",
            extra_http_headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        )
        page = context.new_page()
        for tour in tours:
            result = collect(page, tour)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            time.sleep(2)
        browser.close()
    snapshot = {
        "date": now.date().isoformat(),
        "checked_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "results": results,
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    # Preserve every attempt, including failed attempts and manual reruns.
    run_id = now.strftime("%Y%m%dT%H%M%S%f")
    write_text(ROOT / f"data/runs/{run_id}.json", encoded)
    daily_path = ROOT / f"data/daily/{snapshot['date']}.json"
    existing = json.loads(daily_path.read_text(encoding="utf-8")) if daily_path.exists() else None
    # A failed rerun must not erase an earlier complete daily observation.
    if total(results) is not None or existing is None or total(existing["results"]) is None:
        write_text(daily_path, encoded)
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    previous_path = ROOT / f"data/daily/{yesterday}.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else None
    report = make_report(snapshot, previous)
    write_text(ROOT / "reports/latest.md", report)
    refresh_csv(tours)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(report)
    return 0 if total(results) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
