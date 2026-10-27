"""Daily immutable full-calendar snapshots and net availability changes."""
import csv
import io
import json
import os
import sys
from datetime import datetime, date, timedelta
from monitor import ROOT, write_text
from seats.monitor import collect, TZ, save_json, experience_label
from telegram_report import send_message

TOURS = [
    {'id': 51192, 'title': 'Всё включено: магия Чёрного и Чарынского каньонов, экзотика озёр Кольсай и Каинды', 'guide': 'Юлия'},
    {'id': 96862, 'title': 'Комфорт-маршрут по озёрам Каинды, Кольсай и Чарынскому каньону', 'guide': 'Юлия'},
    {'id': 53900, 'title': 'Чарынский и Лунный каньоны + озеро Кольсай: групповое фотопутешествие по окрестностям Алматы', 'guide': 'Юлия'},
]
TOUR = next(t for t in TOURS if t['id'] == int(os.environ.get('CALENDAR_TOUR_ID', TOURS[0]['id'])))
DIRECTORY = ROOT / f"data/calendar_history/{TOUR['id']}"
REPORT_URL = f"https://github.com/AntonioSkin/tripster-visitors-monitor/blob/main/data/calendar_history/{TOUR['id']}/summary.csv"


def run_all(module):
    # Separate processes keep paths, receipts and baselines isolated by tour.
    import subprocess
    failed = []
    for tour in TOURS:
        env = dict(os.environ, CALENDAR_TOUR_ID=str(tour['id']))
        result = subprocess.run([sys.executable, '-m', module, '--single'], env=env)
        if result.returncode:
            failed.append(tour['id'])
    if failed:
        raise RuntimeError(f"Не завершены экскурсии: {failed}")


def build_rows(snapshots):
    baselines, rows = {}, []
    previous = {}
    previous_day = None
    for snapshot in sorted(snapshots, key=lambda s: s['date_from']):
        day = snapshot['date_from']
        current = {s['key']: s for s in snapshot['slots']}
        yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        for key, slot in current.items():
            baselines.setdefault(key, (day, slot['available']))
        # Keep absent departures explicitly unknown, and all historical rows forever.
        for key in sorted(baselines):
            if key[:10] < day:
                continue
            first_day, initial = baselines[key]
            slot = current.get(key)
            available = slot['available'] if slot else None
            old = previous.get(key)
            daily = old['available'] - available if old and available is not None and previous_day == yesterday else None
            rows.append({'observed': day, 'departure': key[:10], 'time': key[11:],
                         'first_seen': first_day, 'initial': initial, 'remaining': available,
                         'daily_decrease': daily,
                         'total_decrease': initial - available if available is not None else None,
                         'status': 'observed' if slot else 'not_published'})
        previous, previous_day = current, day
    return rows


HEADERS = {'observed': 'Дата замера', 'departure': 'Дата выезда', 'time': 'Время',
           'first_seen': 'Первый замер', 'initial': 'Начальный остаток', 'remaining': 'Остаток',
           'daily_decrease': 'Чистое уменьшение за сутки', 'total_decrease': 'Чистое уменьшение с начала',
           'status': 'Статус'}


CSV_HEADERS = {k: v for k, v in HEADERS.items() if k not in {'time', 'first_seen', 'status'}}


def group_rows(rows):
    """One row per observation/departure date; values follow departure time order."""
    groups = {}
    for row in rows:
        groups.setdefault((row['observed'], row['departure']), []).append(row)
    result = []
    for key in sorted(groups):
        slots = sorted(groups[key], key=lambda r: r['time'])
        row = {'observed': key[0], 'departure': key[1]}
        for field in HEADERS:
            if field not in row:
                row[field] = ' '.join('—' if r[field] is None else str(r[field]) for r in slots)
        result.append(row)
    return result


def write_csv(path, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS.values())
    for row in group_rows(rows):
        writer.writerow([row[k] if row[k] is not None else '' for k in CSV_HEADERS])
    write_text(path, '\ufeff' + buf.getvalue())


def summary_text(snapshot, rows):
    today = [r for r in rows if r['observed'] == snapshot['date_from']]
    comparable = [r for r in today if r['daily_decrease'] is not None]
    changed = [r for r in comparable if r['daily_decrease'] != 0]
    new = [r for r in today if r['first_seen'] == snapshot['date_from']]
    lines = ['Задача 3 — ежедневная история мест', experience_label(TOUR),
             f'Новых выездов с начальным остатком: {len(new)}.']
    if comparable:
        lines.append(f"Чистое уменьшение за сутки: {sum(r['daily_decrease'] for r in comparable):+d} мест по {len(comparable)} сравнимым выездам.")
    else:
        lines.append('Сравнения за сутки пока нет: нужен замер за вчера.')
    changed_dates = {r['departure'] for r in changed}
    grouped = [r for r in group_rows(today) if r['departure'] in changed_dates]
    shown = 0
    for r in grouped:
        line = f"{r['departure']} ({r['time']}): осталось {r['remaining']}; уменьшение {r['daily_decrease']}"
        if len('\n'.join(lines)) + len(line) > 3100:
            break
        lines.append(line)
        shown += 1
    if shown < len(grouped):
        lines.append(f'Ещё дат с изменениями: {len(grouped) - shown}; все — в таблице.')
    lines.append(REPORT_URL)
    return '\n'.join(lines)


def run(directory=DIRECTORY, now=None, collect_fn=None, send_fn=send_message):
    now = now or datetime.now(TZ)
    today = now.astimezone(TZ).date().isoformat()
    if os.environ.get('GITHUB_EVENT_NAME') == 'schedule' and now.astimezone(TZ).hour < 8:
        return
    receipt = directory / 'telegram' / f'{today}.json'
    if receipt.exists():
        print('Задача 3: сегодняшний замер и отправка уже выполнены.')
        return
    token, chat = os.environ.get('TELEGRAM_BOT_TOKEN', ''), os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        raise RuntimeError('Не заданы секреты Telegram')
    path = directory / 'daily' / f'{today}.json'
    try:
        if path.exists():
            snapshot = json.loads(path.read_text())
        else:
            snapshot = (collect_fn or (lambda: collect(TOUR, full_calendar=True)))()
            if snapshot['date_from'] != today:
                raise ValueError('Неверная дата снимка')
            save_json(path, snapshot)
        snapshots = [json.loads(p.read_text()) for p in sorted((directory / 'daily').glob('*.json'))]
        rows = build_rows(snapshots)
        write_csv(directory / 'history.csv', rows)
        # Latest observation for every departure, including dates that have already passed.
        latest = {}
        for row in rows:
            latest[(row['departure'], row['time'])] = row
        write_csv(directory / 'summary.csv', [latest[k] for k in sorted(latest)])
        text = summary_text(snapshot, rows)
        write_text(directory / 'latest.txt', text + '\n')
        send_fn(token, chat, text)
        save_json(receipt, {'sent_at': datetime.now(TZ).isoformat(), 'snapshot': path.name})
        save_json(directory / 'status.json', {'ok': True, 'checked_at': snapshot['checked_at'], 'slots': len(snapshot['slots']), 'date_to': snapshot['date_to']})
        print(f"Задача 3: сохранены {len(snapshot['slots'])} выездов до {snapshot['date_to']}; Telegram подтвердил отправку.")
    except Exception:
        save_json(directory / 'status.json', {'ok': False, 'attempted_at': now.isoformat(), 'message': 'Замер или доставка не завершены; история сохранена, следующая попытка повторит задачу.'})
        raise


if __name__ == '__main__':
    try:
        if '--single' in sys.argv:
            run()
        else:
            from calendar_history.combined import run as run_combined
            run_combined()
    except Exception:
        print('Задача 3 не завершена; см. status.json и журнал календаря.')
        sys.exit(1)
