"""Daily snapshots of public Tripster excursion visitor counters."""
from html.parser import HTMLParser
import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Almaty")
NUMBER = r"(\d+(?:[ \u00a0\u202f]\d{3})*)"
PAIR = re.compile(
    NUMBER + r"\s+отзыв(?:а|ов)?\s*[,·|]?\s*" + NUMBER + r"\s+посетил(?:и|о)?\b",
    re.I,
)
REVERSE_PAIR = re.compile(
    NUMBER + r"\s+посетил(?:и|о)?\s*[,·|]?\s*" + NUMBER + r"\s+отзыв(?:а|ов)?\b",
    re.I,
)


def parse_visitors(text):
    """Require the excursion's review+visitor pair; never take the guide total."""
    normalized = re.sub(r"\s+", " ", text)
    values = {
        int(re.sub(r"\s", "", m.group(2)))
        for m in PAIR.finditer(normalized)
    }
    values.update(
        int(re.sub(r"\s", "", m.group(1)))
        for m in REVERSE_PAIR.finditer(normalized)
    )
    if len(values) != 1:
        raise ValueError("Счётчик экскурсии отсутствует или найдено несколько разных значений")
    return values.pop()


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(source):
    parser = PageText()
    parser.feed(source)
    return " ".join(parser.parts)


def collect(page, tour):
    url = f"https://experience.tripster.ru/experience/{tour['id']}/"
    error = None
    for attempt in range(1):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if response is None or response.status >= 400:
                raise RuntimeError(f"HTTP {response.status if response else 'no response'}")
            source_text = html_text(response.text())
            try:
                value = parse_visitors(source_text)
            except ValueError:
                value = None
            page.locator("h1").first.wait_for(timeout=30000)
            cookie_button = page.get_by_role("button", name="Accept all", exact=True)
            if cookie_button.count() and cookie_button.first.is_visible():
                cookie_button.first.click()
                page.wait_for_timeout(2000)
            # Wait for the rendered public counter, not a search-engine copy.
            for _ in range(5):
                if value is not None:
                    break
                try:
                    value = parse_visitors(html_text(page.content()))
                    break
                except ValueError:
                    page.wait_for_timeout(1000)
            if value is None:
                print("COUNTER_DIAGNOSTIC", tour["id"], page.url, repr(page.locator("body").inner_text()[:10000]), flush=True)
                raise ValueError("На странице не найден однозначный счётчик")
            return {
                "id": tour["id"], "name": tour["name"], "url": url,
                "title": page.locator("h1").first.inner_text().strip(),
                "visitors": value, "status": "ok",
                "checked_at": datetime.now(TZ).isoformat(timespec="seconds"),
            }
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempt < 0:
                time.sleep(3 * (attempt + 1))
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
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            locale="ru-RU", service_workers="block",
            extra_http_headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        )
        page = context.new_page()
        page.on("pageerror", lambda err: print("JS_ERROR", str(err)[:500], flush=True))
        page.on("response", lambda res: print("HTTP_ERROR", res.status, res.url.split("?")[0], flush=True) if res.status >= 400 else None)
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
