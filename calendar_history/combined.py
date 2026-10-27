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

DIRECTORY = ROOT / 'data/calendar_history/yulia'
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
    writer.writerow(['Дата замера','Дата выезда','Начальный остаток','Остаток','Чистое уменьшение за сутки','Чистое уменьшение с начала'])
    for row in rows:
        writer.writerow([row[k] if row[k] is not None else '—' for k in ('observed','departure','initial','remaining','daily','total')])
    write_text(path,'\ufeff'+out.getvalue())


def places_word(count):
    if 11 <= count % 100 <= 14:
        return 'мест'
    return 'место' if count % 10 == 1 else 'места' if count % 10 in (2, 3, 4) else 'мест'


def report_text(current):
    lines=['Задача 3 — остатки мест','Гид: Юлия','51192 / 96862 / 53900','']
    changed=[r for r in current if r['daily'] is not None and r['daily'] != 0]
    if not changed:
        lines.append('Изменений за сутки нет.' if any(r['daily'] is not None for r in current) else 'Начальные остатки зафиксированы.')
    for row in changed:
        delta=row['daily']
        count=abs(delta)
        action='продано' if delta > 0 else 'добавлено'
        date=datetime.fromisoformat(row['departure']).strftime('%d.%m.%Y')
        line=f'{date}: {action} {count} {places_word(count)}'
        if len('\n'.join(lines))+len(line)>3400:
            break
        lines.append(line)
    lines.extend(['','Все актуальные данные — в таблице.',URL])
    return '\n'.join(lines)


def run():
    now=datetime.now(TZ); day=now.date().isoformat()
    if os.environ.get('GITHUB_EVENT_NAME')=='schedule' and now.hour<8:
        return
    receipt=DIRECTORY/'telegram'/f'{day}.json'
    if receipt.exists():
        print('Общий отчёт Юлии уже отправлен.'); return
    token,chat=os.environ['TELEGRAM_BOT_TOKEN'],os.environ['TELEGRAM_CHAT_ID']
    all_rows=[]; failed=[]
    for tour in TOURS:
        directory=ROOT/'data/calendar_history'/str(tour['id'])
        path=directory/'daily'/f'{day}.json'
        try:
            if not path.exists():
                snapshot=collect(tour,full_calendar=True)
                if snapshot['date_from']!=day:
                    raise ValueError('Дата замера изменилась')
                save_json(path,snapshot)
            snapshots=[json.loads(p.read_text()) for p in sorted((directory/'daily').glob('*.json'))]
            rows=build_rows(snapshots); all_rows.append(rows)
            write_csv(directory/'history.csv',rows)
            latest={}
            for r in rows: latest[(r['departure'],r['time'])]=r
            write_csv(directory/'summary.csv',list(latest.values()))
        except Exception:
            failed.append(tour['id'])
    if failed:
        save_json(DIRECTORY/'status.json',{'ok':False,'failed':failed,'checked_at':now.isoformat()})
        raise RuntimeError('Неполный общий сбор; отправка при следующем запуске')
    history=[]
    for observed in sorted({r['observed'] for rows in all_rows for r in rows}):
        history.extend(merge_rows(all_rows,observed))
    latest={r['departure']:r for r in history}
    save_table(DIRECTORY/'history.csv',history)
    save_table(DIRECTORY/'summary.csv',[latest[k] for k in sorted(latest)])
    current=merge_rows(all_rows,day)
    text=report_text(current)
    write_text(DIRECTORY/'latest.txt',text+'\n')
    send_message(token,chat,text)
    save_json(receipt,{'sent_at':datetime.now(TZ).isoformat(),'tours':[t['id'] for t in TOURS]})
    save_json(DIRECTORY/'status.json',{'ok':True,'checked_at':datetime.now(TZ).isoformat(),'dates':len(current)})
