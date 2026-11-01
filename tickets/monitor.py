"""Read tickets through the normal UI; persist hashes, never QR codes or orders."""
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo('Asia/Almaty')
URL = 'https://admin.zakazbiletov.kz/organizer/tickets'


def identify(cells):
    if len(cells) != 7 or not cells[4].strip():
        raise RuntimeError('Ticket row has no stable identifier; results were not saved')
    return hashlib.sha256(('zakazbiletov:qr:' + cells[4].strip()).encode()).hexdigest()


def plan(previous, rows):
    current = {}
    for cells in rows:
        key = identify(cells)
        if key in current:
            raise RuntimeError('Duplicate ticket across pages; results were not saved')
        current[key] = cells
    if previous is None:
        if not current:
            raise RuntimeError('Empty first collection; baseline requires inspection')
        return set(current), []
    seen = previous.get('seen')
    if (previous.get('version') != 1 or not isinstance(seen, list)
            or not seen or any(not isinstance(x, str) or not re.fullmatch('[a-f0-9]{64}', x) for x in seen)):
        raise RuntimeError('Invalid saved ticket baseline; refusing to reset it')
    if not current:
        raise RuntimeError('Unexpected empty ticket list; results were not saved')
    return set(seen) | set(current), [cells for key, cells in current.items() if key not in seen]


def scan(page):
    page.get_by_role('heading', name='Электронные билеты', exact=True).wait_for()
    table = page.locator('table')
    table.wait_for()
    headers = table.locator('thead tr').first.inner_text()
    if not all(label in headers for label in ('Мероприятие', 'Дата мероприятия', 'QR код', 'Отсканирован')):
        raise RuntimeError('Ticket table format changed')
    rows, signatures = [], set()
    for _ in range(1000):
        body = table.locator('tbody tr')
        body.first.wait_for()
        # Read one DOM snapshot so rows cannot change halfway through a page.
        cells = body.evaluate_all('(rows) => rows.map(r => Array.from(r.querySelectorAll("td"), c => c.innerText))')
        signature = tuple(identify(row) for row in cells)
        if signature in signatures:
            raise RuntimeError('Pagination repeated a page; results were not saved')
        signatures.add(signature)
        rows.extend(cells)
        next_page = page.get_by_role('button', name='Next Page', exact=True)
        if next_page.is_disabled():
            return rows
        old = body.first.inner_text()
        next_page.click()
        page.wait_for_function('(old) => document.querySelector("table tbody tr")?.innerText !== old', arg=old)
    raise RuntimeError('Ticket pagination limit exceeded')


def collect(email, password):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(locale='ru-RU')
        page = context.new_page()
        page.set_default_timeout(30000)
        page.goto(URL, wait_until='networkidle')
        if '/login' in page.url:
            page.locator('input[type="text"], input[type="email"]').first.fill(email)
            page.locator('input[type="password"]').fill(password)
            page.get_by_role('button', name='Войти', exact=True).click()
            page.get_by_role('button', name='Выйти', exact=True).wait_for()
            page.goto(URL, wait_until='networkidle')
        rows = scan(page)
        browser.close()
        return rows


def notify(rows):
    token, chat = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        raise RuntimeError('Telegram secrets are missing')
    groups = Counter((r[0].strip(), r[1].strip()) for r in rows)
    lines = [f'🎟 Новые билеты Zakazbiletov: {len(rows)}']
    lines += [f'{event}\n{date} — {count} шт.' for (event, date), count in groups.items()]
    # Plain text: website content is not interpreted as Telegram markup.
    message = '\n\n'.join(lines)
    if len(message) > 4000:
        message = f'🎟 Новые билеты Zakazbiletov: {len(rows)}. Подробности: {URL}'
    req = Request(f'https://api.telegram.org/bot{token}/sendMessage',
                  data=json.dumps({'chat_id': chat, 'text': message}).encode(),
                  headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(req, timeout=30) as response:
            result = json.load(response)
        if result.get('ok') is not True:
            raise ValueError()
    except Exception:
        # Never print request URLs (they contain the bot token) or API response.
        raise RuntimeError('Telegram delivery was not confirmed; new tickets remain unacknowledged') from None


def run(root=ROOT, collector=collect, sender=notify, now=None):
    now = now or datetime.now(TZ)
    base = root / 'data/tickets'
    state_file = base / 'state.json'
    previous = json.loads(state_file.read_text()) if state_file.exists() else None
    email, password = os.environ.get('ZAKAZBILETOV_EMAIL'), os.environ.get('ZAKAZBILETOV_PASSWORD')
    if not email or not password:
        raise RuntimeError('Add ZAKAZBILETOV_EMAIL and ZAKAZBILETOV_PASSWORD to Actions secrets')
    rows = collector(email, password)
    seen, new = plan(previous, rows)
    if new:
        sender(new)
    checked = now.astimezone(TZ).isoformat()
    state = {'version': 1, 'seen': sorted(seen), 'last_success': checked}
    receipt = {'ok': True, 'completed_at': checked, 'baseline': previous is None,
               'tickets_count': len(rows), 'new_count': len(new), 'alert_sent': bool(new)}
    if new:
        receipt['sent_at'] = datetime.now(TZ).isoformat()
    base.mkdir(parents=True, exist_ok=True)
    for name, value in (('state.json', state), ('completed.json', receipt)):
        (base / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(f'Task 6: checked {len(rows)} tickets; new {len(new)}; baseline {previous is None}; delivery complete')


if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        # Playwright errors can include private row text; keep logs redacted.
        print(str(exc) if isinstance(exc, RuntimeError) else 'Task 6 failed; private browser details suppressed')
        raise SystemExit(1) from None
