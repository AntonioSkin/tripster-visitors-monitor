"""An extra observation without replacing the daily snapshot or receipt."""
import csv
import io
import json
import os
import sys
from datetime import datetime
from calendar_history.monitor import TOUR, DIRECTORY, group_rows, run_all
from seats.monitor import collect, save_json, TZ, experience_label
from monitor import write_text
from telegram_report import send_message


def compare(snapshot, previous, baselines):
    current = {s['key']: s for s in snapshot['slots']}
    old = {s['key']: s for s in previous['slots']}
    rows = []
    for key in sorted(set(current) | set(old)):
        a, b = old.get(key), current.get(key)
        before = a['available'] if a else None
        after = b['available'] if b else None
        initial = baselines.get(key, after)
        rows.append({'observed': snapshot['date_from'], 'departure': key[:10], 'time': key[11:],
                     'first_seen': '', 'initial': initial, 'remaining': after,
                     'daily_decrease': before - after if before is not None and after is not None else None,
                     'total_decrease': initial - after if initial is not None and after is not None else None,
                     'status': 'observed' if b else 'not_published', 'before': before,
                     'changed': before != after})
    return rows


def run():
    token, chat = os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID']
    run_id = os.environ.get('GITHUB_RUN_ID', datetime.now(TZ).strftime('%Y%m%dT%H%M%S'))
    root = DIRECTORY / 'manual' / run_id
    if (root/'telegram.json').exists():
        return
    daily = [json.loads(p.read_text()) for p in (DIRECTORY/'daily').glob('*.json')]
    extra = [json.loads(p.read_text()) for p in (DIRECTORY/'manual').glob('*/snapshot.json') if p.parent != root]
    snapshots = sorted(daily + extra, key=lambda s: s['checked_at'])
    if not snapshots:
        raise ValueError('Нет исходного замера')
    previous = snapshots[-1]
    baselines = {}
    for snap in snapshots:
        for slot in snap['slots']:
            baselines.setdefault(slot['key'], slot['available'])
    if (root/'snapshot.json').exists():
        snapshot = json.loads((root/'snapshot.json').read_text())
    else:
        snapshot = collect(TOUR, full_calendar=True)
        save_json(root/'snapshot.json', snapshot)
    rows = compare(snapshot, previous, baselines)
    grouped = group_rows(rows)
    dates = sorted({r['departure'] for r in rows if r['changed']})
    old_groups = group_rows([dict(r, remaining=r['before']) for r in rows])
    old_by_date = {r['departure']: r['remaining'] for r in old_groups}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Дата выезда','Начальный остаток','Предыдущий остаток','Текущий остаток','Чистое уменьшение с прошлого замера','Чистое уменьшение с начала'])
    details = []
    for r in grouped:
        before = old_by_date[r['departure']]
        writer.writerow([r['departure'],r['initial'],before,r['remaining'],r['daily_decrease'],r['total_decrease']])
        if r['departure'] in dates:
            details.append(f"{r['departure']}: {before} → {r['remaining']}; уменьшение {r['daily_decrease']}")
    write_text(root/'comparison.csv', '\ufeff'+buffer.getvalue())
    decreased = sum(max(r['daily_decrease'],0) for r in rows if r['daily_decrease'] is not None)
    increased = sum(max(-r['daily_decrease'],0) for r in rows if r['daily_decrease'] is not None)
    new = sum(r['before'] is None and r['remaining'] is not None for r in rows)
    missing = sum(r['remaining'] is None for r in rows)
    report = {'previous_checked_at': previous['checked_at'], 'checked_at': snapshot['checked_at'],
              'slots': len(snapshot['slots']), 'date_to': snapshot['date_to'],
              'changed_dates': dates, 'decreased': decreased, 'increased': increased,
              'new_slots': new, 'missing_slots': missing, 'details': details}
    save_json(root/'comparison.json', report)
    save_json(DIRECTORY/'manual_latest.json', dict(report, path=str(root.relative_to(DIRECTORY))))
    url = f"https://github.com/AntonioSkin/tripster-visitors-monitor/blob/main/data/calendar_history/{TOUR['id']}/manual/{run_id}/comparison.csv"
    lines = ['Задача 3 — внеплановый замер', experience_label(TOUR),
             f'Уменьшение: {decreased}; увеличение: {increased}; новых выездов: {new}; отсутствуют: {missing}.']
    for detail in details:
        if len('\n'.join(lines))+len(detail)>3000:
            lines.append('Остальные изменения — в таблице.'); break
        lines.append(detail)
    if not details:
        lines.append('Изменений нет.')
    lines.append(url)
    text='\n'.join(lines)
    write_text(root/'report.txt', text+'\n')
    send_message(token,chat,text)
    save_json(root/'telegram.json',{'sent_at':datetime.now(TZ).isoformat()})
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    try:
        if '--single' in sys.argv:
            run()
        else:
            run_all('calendar_history.manual')
    except Exception:
        print('Внеплановый замер или отправка не завершены.')
        sys.exit(1)
