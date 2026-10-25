import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(locale="ru-RU")
    def observe(response):
        if "/api/" not in response.url or "tripster.ru" not in response.url:
            return
        print("RESPONSE", response.status, response.url, flush=True)
        if response.status == 200 and ("51584" in response.url or any(s in response.url for s in ("calendar", "schedule", "event", "availab"))):
            try:
                data = response.json()
                if response.url.split("?")[0].endswith("/experiences/51584/"):
                    print("DETAIL_KEYS", list(data), flush=True)
                    print("CALENDAR_FIELDS", json.dumps({k:v for k,v in data.items() if any(s in k for s in ("event", "date", "schedule", "calendar", "availab"))}, ensure_ascii=False)[:20000], flush=True)
                else:
                    print("DATA", json.dumps(data, ensure_ascii=False)[:50000], flush=True)
            except Exception:
                pass
    page.on("response", observe)
    page.goto("https://experience.tripster.ru/experience/51584/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(12000)
    print("BODY", page.locator("body").inner_text()[:18000], flush=True)
    button = page.get_by_text("Выбрать дату", exact=True)
    if button.count():
        button.first.click(timeout=10000)
        page.wait_for_timeout(6000)
        print("CALENDAR_BODY", page.locator("body").inner_text()[-18000:], flush=True)
    browser.close()
