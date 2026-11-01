"""Read tickets through the normal UI; persist hashes, never QR codes or orders."""
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
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
        login_responses = []
        script_errors = []
        page.on('pageerror', lambda error: script_errors.append(str(error)))
        page.on('console', lambda message: script_errors.append(message.text) if message.type == 'error' else None)
        def observe_login(response):
            # Record status codes only; response bodies can contain credentials.
            if response.request.method == 'POST':
                path = urlsplit(response.url).path
                safe_path = re.sub(r'[0-9a-f]{16,}|[0-9]{5,}', '<id>', path, flags=re.I)
                login_responses.append({'status': response.status, 'path': safe_path})
        page.on('response', observe_login)
        print('Task 6: opening ticket page')
        page.goto(URL, wait_until='domcontentloaded')
        page.locator('input[type="password"], table').first.wait_for()
        print('Task 6: page path ' + urlsplit(page.url).path)
        if page.locator('input[type="password"]').count():
            print('Task 6: signing in with Actions secrets')
            page.locator('input[type="text"], input[type="email"]').first.fill(email.strip())
            print('Task 6: account field filled')
            page.locator('input[type="password"]').fill(password)
            print('Task 6: password field filled')
            print('Task 6: form validity ' + json.dumps(page.locator('input').evaluate_all('''(inputs) => inputs.filter(n => n.type !== 'checkbox').map(n => ({
                type: n.type, valid: n.checkValidity(), missing: n.validity.valueMissing,
                typeMismatch: n.validity.typeMismatch, patternMismatch: n.validity.patternMismatch
            }))''')))
            page.get_by_role('button', name='Войти', exact=True).click()
            print('Task 6: login submitted')
            try:
                page.get_by_role('button', name='Выйти', exact=True).wait_for()
            except Exception:
                flags = page.locator('body').evaluate('''(body) => ({
                    invalidCredentials: /неверн|неправильн|invalid credentials|incorrect password|credentials.*(?:match|invalid)|не соответствуют|не совпадают|ошибка авторизации|authentication failed/i.test(body.innerText),
                    requiredField: /обязательн|заполните|is required/i.test(body.innerText),
                    captcha: /captcha|капч|робот|verify you are human/i.test(body.innerText),
                    loginForm: !!body.querySelector('input[type="password"]')
                })''')
                print('Task 6: sign-in outcome ' + json.dumps(flags))
                print('Task 6: sign-in POST status codes ' + json.dumps(login_responses))
                for error in script_errors:
                    # Login-page diagnostics only. Never emit credentials or URLs.
                    for secret in (email, password, *[os.environ.get(k, '') for k in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID')]):
                        if secret:
                            error = error.replace(secret, '<redacted>')
                    error = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+', '<redacted>', error)
                    print('Task 6: login script error ' + error[:350])
                print('Task 6: final page path ' + urlsplit(page.url).path)
                raise RuntimeError('Website did not confirm sign-in; baseline and completion were not saved') from None
            print('Task 6: sign-in confirmed')
            page.goto(URL, wait_until='domcontentloaded')
        print('Task 6: reading ticket table')
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
    checked = datetime.now(TZ).isoformat()
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
        print(str(exc) if isinstance(exc, RuntimeError) else f'Task 6 failed ({type(exc).__name__}); private browser details suppressed')
        raise SystemExit(1) from None
