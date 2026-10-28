"""Twice-daily RUB/KZT quote from Wise."""
import json
import os
import re
import time
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from monitor import ROOT, TZ, write_text
from telegram_report import send_message

URL = 'https://wise.com/gb/currency-converter/rub-to-kzt-rate'
DIRECTORY = ROOT / 'data/fx'
ENABLED_FROM = '2026-10-27T20:00'


def parse_quote(html):
    match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError('Нет данных курса Wise')
    data = json.loads(match.group(1), parse_float=Decimal)
    rate = data['props']['pageProps']['model']['rate']
    if rate['sourceCurrency']['code'] != 'RUB' or rate['targetCurrency']['code'] != 'KZT':
        raise ValueError('Неверная валютная пара')
    value = Decimal(str(rate['value']))
    if not value.is_finite() or value <= 0:
        raise ValueError('Некорректный курс Wise')
    return str(value)


def changed(before, after):
    return (Decimal(before) * 10).to_integral_value(rounding=ROUND_FLOOR) != (Decimal(after) * 10).to_integral_value(rounding=ROUND_FLOOR)


def collect():
    for attempt in range(3):
        try:
            request = Request(URL, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache'})
            with urlopen(request, timeout=45) as response:
                quote = parse_quote(response.read().decode('utf-8'))
            return {'rate': quote, 'checked_at': datetime.now(TZ).isoformat(), 'source': URL}
        except Exception as exc:
            print(f'Quote attempt {attempt + 1}/3: {type(exc).__name__}')
            if attempt == 2:
                raise RuntimeError('Не удалось получить курс Wise') from None
            time.sleep(5)


def observation(now):
    now = now.astimezone(TZ)
    if now.hour < 11:
        return None
    key = now.date().isoformat() + ('T20:00' if now.hour >= 20 else 'T11:00')
    return key if key >= ENABLED_FROM else None


def save(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def run():
    key = observation(datetime.now(TZ))
    if key is None or (DIRECTORY / 'completed' / f'{key}.json').exists():
        return
    path = DIRECTORY / 'measurements' / f'{key}.json'
    state_path = DIRECTORY / 'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    try:
        snapshot = json.loads(path.read_text()) if path.exists() else collect()
        save(path, snapshot)
        before = state.get('rate')
        alert = before is not None and changed(before, snapshot['rate'])
        if alert:
            text = ('Задача 4 — изменение курса RUB/KZT (Wise)\n'
                    + before.replace('.', ',') + ' → ' + snapshot['rate'].replace('.', ',')
                    + ' ₸ за 1 ₽\n' + URL)
            send_message(os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID'], text)
        save(state_path, dict(snapshot, observation_id=key))
        save(DIRECTORY / 'completed' / f'{key}.json', dict(snapshot, alert_sent=alert, completed_at=datetime.now(TZ).isoformat()))
        save(DIRECTORY / 'status.json', {'ok': True, 'observation_id': key, **snapshot})
        print(f'RUB/KZT: {snapshot["rate"]}; alert: {alert}')
    except Exception:
        save(DIRECTORY / 'status.json', {'ok': False, 'observation_id': key, 'checked_at': datetime.now(TZ).isoformat()})
        raise


if __name__ == '__main__':
    run()
