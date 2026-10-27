"""One guide report, ordered tour values, one shared delivery receipt."""
import csv
import io
import json
import os
from datetime import datetime
from calendar_history.monitor import TOURS, ROOT, TZ, build_rows, write_csv
from seats.monitor import collect, save_json
from monitor import write_text
from telegram_report import send_message

DIRECTORY = ROOT / 'data/calendar_history/joint'
URL = 'https://github.com/AntonioSkin/tripster-visitors-monitor/blob/main/data/calendar_history/yulia/summary.csv'


def format_values(groups):
    return ' '.join(('' if index == 0 else '-') + ('—' if value is None else str(value))
                    for index, values in enumerate(groups) for value in (values or [None]))


def merge_rows(by_tour, day):
    selected = [[r for r in rows if r['observed'] == day] for rows in by_tour]
    days = sorted({r['departure'] for rows in selected for r in rows})
    result = []
    for departure in days:
        groups = [sorted([r for r in rows if r['departure'] == departure], key=lambda r:r['time']) for rows in selected]
        flat = [r for g in groups for r in g]
        # Compare matching times only. Removed times are closed, not sold;
        # newly published times have no previous count and do not hide other sales.
        deltas = [r['daily_decrease'] for r in flat if r['daily_decrease'] is not None]
        totals = [r['total_decrease'] for r in flat]
        result.append({'observed':day, 'departure':departure,
            'initial':format_values([[r['initial'] for r in g] for g in groups]),
            'remaining':format_values([[r['remaining'] for r in g] for g in groups]),
            'daily':sum(deltas) if deltas else None,
            'total':sum(totals) if totals and all(v is not None for v in totals) else None,
            'changed':any(v is not None and v != 0 for v in deltas)})
    return result


def save_table(path, rows):
    out=io.StringIO(); writer=csv.writer(out)
    writer.writerow(['Дата замера','Дата выезда','Начальный остаток','Остаток','Чистое уменьшение с прошлого замера','Чистое уменьшение с начала'])
    for row in rows:
        writer.writerow([row[k] if row[k] is not None else '—' for k in ('observed','departure','initial','remaining','daily','total')])
    write_text(path,'\ufeff'+out.getvalue())


def places_word(count):
    if 11 <= count % 100 <= 14:
        return 'мест'
    return 'место' if count % 10 == 1 else 'места' if count % 10 in (2, 3, 4) else 'мест'


GROUPS = [
    {'key': 'yulia', 'name': 'Юлия', 'tours': TOURS},
    {'key': 'dmitry', 'name': 'Дмитрий', 'tours': [
        {'id': value, 'guide': 'Дмитрий'} for value in (57434, 85543, 55575, 54971)
    ]},
]


def group_url(group):
    return URL.replace('/yulia/', '/' + group['key'] + '/')


def report_text(current):
    lines = ['Задача 3 — остатки мест', '']
    dates = sorted({r['departure'] for rows in current.values() for r in rows
                    if r['daily'] is not None and r['daily'] != 0})
    for departure in dates:
        lines.append(datetime.fromisoformat(departure).strftime('%d.%m.%Y') + ':')
        values = []
        for group in GROUPS:
            row = next((r for r in current[group['key']] if r['departure'] == departure), None)
            delta = row['daily'] if row else None
            if delta is None:
                lines.append(group['name'] + ' — нет сравнения')
                continue
            values.append(delta)
            if delta == 0:
                lines.append(group['name'] + ' — без изменений')
            else:
                action = 'продано' if delta > 0 else 'добавлено'
                lines.append(f"{group['name']} — {action} {abs(delta)} {places_word(abs(delta))}")
        if len(values) == len(GROUPS):
            total = sum(values)
            action = 'продано' if total >= 0 else 'добавлено'
            lines.append(f'Всего — {action} {abs(total)} {places_word(abs(total))}')
        lines.append('')
    if not dates:
        compared = [group['name'] for group in GROUPS if any(
            r['daily'] is not None for r in current[group['key']]
        )]
        if len(compared) == len(GROUPS):
            return 'Задача 3 — проверка выполнена.\nПо Юлии и Дмитрию изменений нет.'
        return ('Задача 3 — проверка выполнена.\n'
                'Остатки сохранены. Пока недостаточно данных для сравнения по обоим гидам.')
    lines.append('Все актуальные данные — в таблицах.')
    for group in GROUPS:
        lines.extend([group['name'], group_url(group)])
    return '\n'.join(lines)


def observation_id(now):
    now = now.astimezone(TZ)
    if now.hour < 11:
        return None
    return now.date().isoformat() + ('T20:00' if now.hour >= 20 else 'T11:00')


def due_observation(now, directory=DIRECTORY):
    observation = observation_id(now)
    if observation is None:
        return None
    if (directory / 'telegram' / f'{observation}.json').exists():
        return None
    # The existing daily report counts as the morning report on migration day.
    if observation.endswith('T11:00') and (directory / 'telegram' / f'{observation[:10]}.json').exists():
        return None
    return observation


def interval_rows(snapshots):
    baselines, previous, rows = {}, {}, []
    for snapshot in sorted(snapshots, key=lambda s: s.get('checked_at', s['date_from'])):
        day = snapshot['date_from']
        observed = snapshot.get('observation_id', day)
        current = {s['key']: s for s in snapshot['slots']}
        for key, slot in current.items():
            baselines.setdefault(key, (observed, slot['available']))
        for key in sorted(baselines):
            if key[:10] < day:
                continue
            first_seen, initial = baselines[key]
            slot, old = current.get(key), previous.get(key)
            remaining = slot['available'] if slot else None
            rows.append({
                'observed': observed, 'departure': key[:10], 'time': key[11:],
                'first_seen': first_seen, 'initial': initial, 'remaining': remaining,
                'daily_decrease': old['available'] - remaining if old and slot else None,
                'total_decrease': initial - remaining if slot else None,
                'status': 'observed' if slot else 'not_published',
            })
        previous = current
    return rows


def run():
    now = datetime.now(TZ)
    day = now.date().isoformat()
    observation = due_observation(now)
    if observation is None:
        print('Время замера ещё не наступило или отчёт уже отправлен.')
        return
    receipt = DIRECTORY / 'telegram' / f'{observation}.json'
    token, chat = os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID']
    current, failed = {}, []
    for group in GROUPS:
        group_directory = ROOT / 'data/calendar_history' / group['key']
        all_rows = []
        for tour in group['tours']:
            # Dmitry starts fresh; do not reuse the archived 57434 campaign.
            directory = (ROOT / 'data/calendar_history' / str(tour['id'])
                         if group['key'] == 'yulia'
                         else group_directory / 'tours' / str(tour['id']))
            path = directory / 'measurements' / f'{observation}.json'
            try:
                if not path.exists():
                    snapshot = collect(tour, full_calendar=True)
                    if snapshot['date_from'] != day:
                        raise ValueError('Дата замера изменилась')
                    snapshot['observation_id'] = observation
                    save_json(path, snapshot)
                snapshots = [json.loads(p.read_text()) for p in sorted((directory / 'daily').glob('*.json'))]
                snapshots += [json.loads(p.read_text()) for p in sorted((directory / 'measurements').glob('*.json'))]
                rows = interval_rows(snapshots)
                all_rows.append(rows)
                write_csv(directory / 'history.csv', rows)
                latest = {(r['departure'], r['time']): r for r in rows}
                write_csv(directory / 'summary.csv', list(latest.values()))
            except Exception:
                failed.append(tour['id'])
        if len(all_rows) != len(group['tours']):
            continue
        history = []
        for observed in sorted({r['observed'] for rows in all_rows for r in rows}):
            history.extend(merge_rows(all_rows, observed))
        latest = {r['departure']: r for r in history}
        save_table(group_directory / 'history.csv', history)
        save_table(group_directory / 'summary.csv', [latest[k] for k in sorted(latest)])
        current[group['key']] = merge_rows(all_rows, observation)
    if failed:
        save_json(DIRECTORY / 'status.json', {'ok': False, 'failed': failed, 'checked_at': now.isoformat()})
        raise RuntimeError('Неполный общий сбор; отправка при следующем запуске')
    text = report_text(current)
    write_text(DIRECTORY / 'latest.txt', text + '\n')
    if len(text) > 4000:
        text = ('Задача 3 — остатки мест\nЮлия и Дмитрий\n\n'
                'Все изменения по датам — в полном отчёте:\n'
                + URL.replace('/yulia/summary.csv', '/joint/latest.txt'))
    send_message(token, chat, text)
    save_json(receipt, {'sent_at': datetime.now(TZ).isoformat(), 'observation_id': observation,
                       'tours': [t['id'] for g in GROUPS for t in g['tours']]})
    save_json(DIRECTORY / 'status.json', {'ok': True, 'checked_at': datetime.now(TZ).isoformat(),
                                        'dates': {key: len(rows) for key, rows in current.items()}})
