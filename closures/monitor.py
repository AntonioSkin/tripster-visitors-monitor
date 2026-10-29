"""Alert when a future departure becomes zero or is unpublished."""
import hashlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from calendar_history.combined import GROUPS
from monitor import ROOT, TZ
from seats.monitor import collect, experience_label, experience_url, save_json
from telegram_report import send_message

DIRECTORY = ROOT / 'data/closures'
TITLES = {
    57434: 'Природа Казахстана: 5 лучших мест за 1 день из Алматы',
    85543: 'Топ-локации Алматинской области: Чарын, Кольсай и Каинды в мини-группе',
    55575: 'Фотопрогулка по озёрам Кольсай, Каинды и Чарынскому каньону',
    54971: 'Прогулка по Чарынскому и Чёрному каньонам и озеру Каинды в компании фотографа-гида',
}
# Derive membership from task 3 so the two tasks always monitor the same tours.
EXPERIENCES = [dict(tour, title=tour.get('title') or TITLES[tour['id']], guide=group['name'])
               for group in GROUPS for tour in group['tours']]


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else ({} if default is None else default)


def reconcile_history(state, experience, snapshot, root):
    """Recover real previous counts missed by the original task-5 baseline.

    Task 3 already observed these exact departures. Import only the latest
    known value for absent task-5 keys; never replace a newer task-5 value.
    This is a one-time migration, not a fabricated zero or a sales estimate.
    """
    if state.get('history_reconciled'):
        return state
    group = next(g for g in GROUPS if any(t['id'] == experience['id'] for t in g['tours']))
    base = root / 'data/calendar_history'
    directory = (base / str(experience['id']) if group['key'] == 'yulia'
                 else base / group['key'] / 'tours' / str(experience['id']))
    checked = datetime.fromisoformat(snapshot['checked_at'])
    # Use one recent complete calendar before task 5 began, rather than merging
    # all historic time keys (old timetable changes must not create new alarms).
    initial = read(root / 'data/closures/initialized.json').get('sent_at',
                   state.get('last_observed', snapshot['checked_at']))
    first_day = datetime.fromisoformat(initial).astimezone(checked.tzinfo).date()
    candidates = []
    for folder in ('daily', 'measurements'):
        for path in (directory / folder).glob('*.json'):
            old = read(path)
            at = datetime.fromisoformat(old['checked_at'])
            if old.get('experience_id') != experience['id'] or at >= checked or at.date() >= first_day:
                continue
            candidates.append((at, str(path), old))
    historical = {}
    if candidates:
        _, source, old = max(candidates, key=lambda row: (row[0], row[1]))
        for slot in old['slots']:
            key = slot['key']
            if (snapshot['date_from'] <= slot['date'] <= snapshot['date_to']
                    and datetime.fromisoformat(key).replace(tzinfo=checked.tzinfo) > checked):
                historical[key] = {'available': slot['available'], 'observed_at': old['checked_at'],
                                   'source': str(Path(source).relative_to(root))}
    known = dict(state.get('slots', {}))
    for key, value in historical.items():
        if key not in known and value['available'] > 0:
            known[key] = value
    return dict(state, slots=known, history_reconciled=True)


def closure_event(snapshot, key, old, kind, slot=None):
    identity = f"{snapshot['experience_id']}|{key}|{old['observed_at']}|{snapshot['checked_at']}|{kind}"
    return dict(slot or {'key': key, 'date': key[:10], 'time': key[11:], 'available': None},
                previous=old['available'], previous_observed_at=old['observed_at'], kind=kind,
                detected_at=snapshot['checked_at'], event_id=hashlib.sha256(identity.encode()).hexdigest())


def prepare(snapshot, state):
    """Persist a comparison and its outbox together, before attempting Telegram."""
    checked = datetime.fromisoformat(snapshot['checked_at'])
    start, end = snapshot['date_from'], snapshot['date_to']
    if (checked.tzinfo is None or start != checked.date().isoformat()
            or date.fromisoformat(end) != date.fromisoformat(start) + timedelta(days=29)):
        raise ValueError('Нужен свежий календарь на 30 дней')
    last = state.get('last_observed')
    if last and checked < datetime.fromisoformat(last):
        raise ValueError('Старый замер не должен заменять более новый')
    previous = state.get('slots', {})
    # A previously available future departure that is no longer published is
    # unavailable too. Preserve the distinction from an explicit numeric zero.
    known = {key: value for key, value in previous.items()
             if start <= key[:10] <= end and datetime.fromisoformat(key).replace(tzinfo=checked.tzinfo) > checked}
    pending = list(state.get('pending', []))
    events, seen = [], set()
    for slot in snapshot['slots']:
        key, count = slot['key'], slot['available']
        at = datetime.fromisoformat(key).replace(tzinfo=checked.tzinfo)
        if (key in seen or key != slot['date'] + 'T' + slot['time']
                or not start <= slot['date'] <= end or at <= checked
                or type(count) is not int or count < 0):
            raise ValueError('Некорректный остаток или время выезда')
        seen.add(key)
        old = previous.get(key)
        if old is not None and old['available'] > 0 and not old.get('closed') and count == 0:
            event = closure_event(snapshot, key, old, 'zero', slot)
            events.append(event)
            pending.append(event)
        known[key] = {'available': count, 'observed_at': snapshot['checked_at'], 'closed': count == 0}
    for key, old in list(known.items()):
        if key not in seen and old['available'] > 0 and not old.get('closed'):
            event = closure_event(snapshot, key, old, 'unpublished')
            events.append(event)
            pending.append(event)
            known[key] = dict(old, closed=True, unavailable_since=snapshot['checked_at'])
    return dict(state, version=2, initialized=True, slots=known, pending=pending,
                last_observed=snapshot['checked_at']), events


def alert_text(events, experience):
    lines = ['Задача 5 — закрыты места', experience_label(experience), '']
    for event in events:
        day = date.fromisoformat(event['date']).strftime('%d.%m.%Y')
        if event.get('kind') == 'unpublished':
            lines.append(f"{day} в {event['time']}: выезд больше недоступен в календаре (было {event['previous']} мест)")
        else:
            lines.append(f"{day} в {event['time']}: {event['previous']} → 0 мест")
    lines.extend(['', 'При необходимости проверьте и закройте свои места.', experience_url(experience)])
    return '\n'.join(lines)


def run_check(experience, directory, collect_fn=None, send_fn=send_message, now_fn=None):
    collect_fn = collect_fn or (lambda: collect(experience))
    now_fn = now_fn or (lambda: datetime.now(TZ))
    state_path = directory / 'state.json'
    state = read(state_path)
    stage = 'collection'
    try:
        token, chat = os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID']
        snapshot = collect_fn()
        if snapshot.get('experience_id') != experience['id']:
            raise ValueError('Получен календарь другой экскурсии')
        state = reconcile_history(state, experience, snapshot, ROOT)
        state, events = prepare(snapshot, state)
        save_json(directory / 'latest.json', snapshot)
        save_json(state_path, state)
        stage = 'delivery'
        delivered = 0
        while state['pending']:
            batch = state['pending'][:20]
            send_fn(token, chat, alert_text(batch, experience))
            sent_at = now_fn().isoformat()
            # A small daily journal preserves evidence of confirmed alerts.
            for event in batch:
                path = directory / 'alerts' / (event['detected_at'][:10] + '.json')
                journal = read(path, [])
                if not any(row['event_id'] == event['event_id'] for row in journal):
                    journal.append(dict(event, sent_at=sent_at))
                    save_json(path, journal)
            state['pending'] = state['pending'][len(batch):]
            save_json(state_path, state)
            delivered += len(batch)
        completed_at = now_fn().isoformat()
        state['last_success'] = snapshot['checked_at']
        save_json(state_path, state)
        save_json(directory / 'status.json', {'ok': True, 'checked_at': snapshot['checked_at'],
                  'completed_at': completed_at, 'slots': len(snapshot['slots']), 'alerts': delivered})
        print(f"Task 5, tour {experience['id']}: {len(snapshot['slots'])} departures; {delivered} closure alerts")
        return snapshot
    except Exception:
        # Healthchecks owns technical alerts; never expose Telegram credentials.
        save_json(directory / 'status.json', {'ok': False, 'checked_at': now_fn().isoformat(), 'stage': stage})
        raise RuntimeError(f"Task 5, tour {experience['id']}: {stage} not completed") from None


def run_all(directory=DIRECTORY, check_fn=None, send_fn=send_message, now_fn=None):
    check_fn = check_fn or run_check
    now_fn = now_fn or (lambda: datetime.now(TZ))
    failed = []
    for experience in EXPERIENCES:
        try:
            check_fn(experience=experience, directory=directory / str(experience['id']),
                     send_fn=send_fn, now_fn=now_fn)
        except Exception:
            failed.append(experience['id'])
            print(f"Task 5, tour {experience['id']}: failed; checking the remaining tours", flush=True)
    if failed:
        save_json(directory / 'status.json', {'ok': False, 'failed': failed, 'checked_at': now_fn().isoformat()})
        raise RuntimeError('Task 5: incomplete collection or delivery')
    initialized = directory / 'initialized.json'
    if not initialized.exists():
        send_fn(os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID'],
                'Задача 5 — мониторинг закрытия мест включён.\n'
                'Юлия: 51192 / 96862 / 53900\n'
                'Дмитрий: 57434 / 85543 / 55575 / 54971\n\n'
                'Исходные остатки на ближайшие 30 дней сохранены.\n'
                'Проверки запланированы каждые 30 минут. '
                'Уведомлю при переходе остатка с положительного числа на ноль '
                'или исчезновении ранее доступного выезда из календаря.')
        save_json(initialized, {'sent_at': now_fn().isoformat()})
    value = {'ok': True, 'completed_at': now_fn().isoformat(),
             'tours': [experience['id'] for experience in EXPERIENCES]}
    save_json(directory / 'completed.json', value)
    save_json(directory / 'status.json', value)


if __name__ == '__main__':
    try:
        run_all()
    except Exception:
        print('Task 5 failed. Check data/closures status files; technical alerts use Healthchecks.')
        raise SystemExit(1) from None
