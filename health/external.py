"""Provision and ping Healthchecks without exposing account or ping credentials."""
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from monitor import ROOT, TZ

API = 'https://healthchecks.io/api/v3/'
TAG = 'tripster-monitor'
CALENDAR_TOURS = {
    51192: 'data/calendar_history/51192',
    96862: 'data/calendar_history/96862',
    53900: 'data/calendar_history/53900',
    **{i: f'data/calendar_history/dmitry/tours/{i}' for i in (57434, 85543, 55575, 54971)},
}
SPECS = {
    '1': {'name': 'Задача 1 — посетители Tripster', 'schedule': '0 8 * * *', 'grace': 18000},
    '2': {'name': 'Задача 2 — свободные места 0–6', 'timeout': 1800, 'grace': 3600},
    '3': {'name': 'Задача 3 — Юлия и Дмитрий', 'schedule': '0 11,20 * * *', 'grace': 7200},
    '4': {'name': 'Задача 4 — курс RUB/KZT Wise', 'schedule': '0 11,20 * * *', 'grace': 7200},
    '5': {'name': 'Задача 5 — закрытие мест конкурентов', 'timeout': 1800, 'grace': 3600},
}


def read(root, path):
    file = root / path
    return json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}


def stamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(TZ) if result.tzinfo else None
    except ValueError:
        return None


def window(task, now):
    """Current observation and its retry deadline, in Shymkent time."""
    now = now.astimezone(TZ)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if task == '1':
        return (day.replace(hour=8) if now.hour >= 8 else None), day.replace(hour=13)
    if now.hour < 11:
        return None, day.replace(hour=13)
    hour = 20 if now.hour >= 20 else 11
    return day.replace(hour=hour), day.replace(hour=hour + 2)


def seat_stamps(root):
    return [stamp(read(root, ('data/seats' if i == 51584 else f'data/seats/{i}')
                       + '/state.json').get('last_success')) for i in (51584, 51691, 83542, 103689)]


def closure_stamps(root):
    return [stamp(read(root, f'data/closures/{tour}/state.json').get('last_success'))
            for tour in CALENDAR_TOURS]


def completion(task, root, now):
    """Return a fresh, fully delivered observation, never an old successful exit."""
    now = now.astimezone(TZ)
    if task == '5':
        times = closure_stamps(root)
        receipt = read(root, 'data/closures/completed.json')
        done = stamp(receipt.get('completed_at'))
        if (not all(times) or not done or set(receipt.get('tours', [])) != set(CALENDAR_TOURS)
                or receipt.get('ok') is not True or read(root, 'data/closures/status.json').get('ok') is not True
                or not all(timedelta(0) <= now - t <= timedelta(minutes=20) for t in times)
                or not max(times) <= done <= now):
            return None
        for tour, checked in zip(CALENDAR_TOURS, times):
            state = read(root, f'data/closures/{tour}/state.json')
            status = read(root, f'data/closures/{tour}/status.json')
            if (status.get('ok') is not True or state.get('pending')
                    or stamp(status.get('checked_at')) != checked
                    or stamp(state.get('last_observed')) != checked):
                return None
        return done
    if task == '2':
        times = seat_stamps(root)
        if not all(times) or not all(timedelta(0) <= now - t <= timedelta(minutes=20) for t in times):
            return None
        for i in (51584, 51691, 83542, 103689):
            base = 'data/seats' if i == 51584 else f'data/seats/{i}'
            state, status = read(root, base + '/state.json'), read(root, base + '/status.json')
            if status.get('ok') is not True or state.get('error_notified'):
                return None
        return min(times)
    start, _ = window(task, now)
    if start is None:
        return None
    day, obs = start.date().isoformat(), start.strftime('%Y-%m-%dT%H:%M')
    if task == '1':
        snapshot, receipt = read(root, f'data/daily/{day}.json'), read(root, f'data/telegram/{day}.json')
        expected = {r['id'] for r in read(root, 'excursions.json')}
        rows = snapshot.get('results', [])
        if not expected or len(rows) != len(expected) or {r.get('id') for r in rows} != expected:
            return None
        if not all(r.get('status') == 'ok' and type(r.get('visitors')) is int and r['visitors'] >= 0 for r in rows):
            return None
        times = [stamp(snapshot.get('checked_at')), stamp(receipt.get('sent_at'))]
    elif task == '3':
        receipt = read(root, f'data/calendar_history/joint/telegram/{obs}.json')
        if receipt.get('observation_id') != obs or set(receipt.get('tours', [])) != set(CALENDAR_TOURS):
            return None
        times = []
        for tour, base in CALENDAR_TOURS.items():
            snapshot = read(root, f'{base}/measurements/{obs}.json')
            if (snapshot.get('experience_id') != tour or snapshot.get('observation_id') != obs
                    or snapshot.get('date_from') != day or not isinstance(snapshot.get('slots'), list)):
                return None
            times.append(stamp(snapshot.get('checked_at')))
        times.append(stamp(receipt.get('sent_at')))
    else:
        measurement = read(root, f'data/fx/measurements/{obs}.json')
        receipt = read(root, f'data/fx/completed/{obs}.json')
        if (not measurement.get('rate') or receipt.get('rate') != measurement['rate']
                or type(receipt.get('alert_sent')) is not bool):
            return None
        times = [stamp(measurement.get('checked_at')),
                 stamp(receipt.get('completed_at', receipt.get('checked_at')))]
    if not all(times) or not all(start <= t <= now for t in times) or times[-1] < max(times[:-1]):
        return None
    return times[-1]


def request(url, key=None, body=None, ping=False):
    headers = {'User-Agent': 'tripster-visitors-monitor/1.0'}
    if key:
        headers['X-Api-Key'] = key
    if body is not None:
        headers['Content-Type'] = 'application/json'
    for attempt in range(3):
        try:
            req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method='POST' if body is not None else 'GET')
            with urlopen(req, timeout=15) as response:
                data = response.read()
            if ping:
                if data.strip() != b'OK':
                    raise ValueError('Ping was not accepted')
                return None
            return json.loads(data)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f'Healthchecks HTTP {exc.code}; проверьте ключ Read-write и настройки проекта') from None
        except (URLError, TimeoutError, ValueError):
            if attempt == 2:
                raise RuntimeError('Healthchecks не подтвердил запрос; адреса и ключи скрыты') from None
        time.sleep(2)


def ping(check, suffix='', message=''):
    url = check.get('ping_url', '')
    if not re.fullmatch(r'https://hc-ping\.com/[0-9a-f-]{36}', url):
        raise RuntimeError('Нужен ключ Healthchecks Read-write с доступом к ping URL')
    request(url + suffix, body={'message': message}, ping=True)


def slug(task):
    return 'tripster-task-' + task


def update(key, check, fields):
    uuid = check.get('uuid', '')
    if not re.fullmatch(r'[0-9a-f-]{36}', uuid):
        raise RuntimeError('Некорректный ответ Management API Healthchecks')
    return request(API + 'checks/' + uuid, key, fields)


def report(task, check, key, root, now):
    done = completion(task, root, now)
    previous = stamp(check.get('last_ping'))
    if check.get('status') == 'paused':
        raise RuntimeError(f'Контроль задачи {task} приостановлен в Healthchecks')
    if done is None or (previous and done <= previous and not check.get('started')):
        print(f'Healthchecks: задача {task}, нового полного замера нет')
        return False
    if check.get('grace') != SPECS[task]['grace']:
        check = update(key, check, {'grace': SPECS[task]['grace']})
    ping(check, message=f'Task {task}: collection and required delivery complete; observed {done.isoformat()}')
    print(f'Healthchecks: задача {task}, успешный замер подтверждён')
    return True


def first_deadline(task, root, now):
    if task not in ('2', '5'):
        return window(task, now)[1]
    times = seat_stamps(root) if task == '2' else closure_stamps(root)
    return min(times) + timedelta(minutes=90) if all(times) else now + timedelta(minutes=90)


def setup(key, root, now):
    channels = request(API + 'channels/', key).get('channels', [])
    if not any(c.get('kind') == 'telegram' for c in channels):
        raise RuntimeError('В этом проекте Healthchecks не найдена подключённая интеграция Telegram')
    assigned = ','.join(c['id'] for c in channels if c.get('kind') in ('telegram', 'email'))
    for task, spec in SPECS.items():
        fields = dict(spec, slug=slug(task), tags=TAG, unique=['slug'], channels=assigned,
                      tz='Asia/Almaty', methods='POST', manual_resume=True,
                      desc='Шымкент (UTC+5). Успех: полный сбор и необходимая отправка в Telegram. '
                           'Повторный запуск без нового замера не подтверждает успех. '
                           'При пропуске срока или исчезновении GitHub schedule уведомляет Healthchecks.')
        if task in ('2', '5'):
            fields.pop('tz')
        check = request(API + 'checks/', key, fields)
        if report(task, check, key, root, now):
            continue
        # Arm the very first cycle too, without pretending data was collected.
        # A start signal expires at the retry deadline if GitHub never runs again.
        if check.get('status') == 'new' and not check.get('started'):
            seconds = int((first_deadline(task, root, now) - now).total_seconds())
            if seconds <= 0:
                ping(check, '/fail', f'Task {task}: initial inspection found a missed retry deadline')
            else:
                check = update(key, check, {'grace': max(60, seconds)})
                ping(check, '/start', f'Task {task}: monitoring enabled; waiting for first complete observation')
    checks = request(API + 'checks/?tag=' + TAG, key).get('checks', [])
    for task in SPECS:
        found = [c for c in checks if c.get('slug') == slug(task)]
        if len(found) != 1 or (not found[0].get('last_ping') and not found[0].get('started')):
            raise RuntimeError(f'Healthchecks не подтвердил активацию задачи {task}')
    print(f'Healthchecks: все {len(SPECS)} проверок созданы, Telegram назначен, контроль активирован')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=['setup', 'all', *SPECS])
    task = parser.parse_args().task
    key = os.environ.get('HEALTHCHECKS_API_KEY', '').strip()
    if not key:
        if task == 'setup':
            raise RuntimeError('Добавьте HEALTHCHECKS_API_KEY в GitHub Actions Secrets; контроль ещё не активен')
        print('Healthchecks ещё не подключён: отсутствует HEALTHCHECKS_API_KEY')
        return
    now = datetime.now(TZ)
    if task == 'setup':
        setup(key, ROOT, now)
        return
    checks = request(API + 'checks/?tag=' + TAG, key).get('checks', [])
    failures = []
    for current in (SPECS if task == 'all' else [task]):
        try:
            selected = [c for c in checks if c.get('slug') == slug(current)]
            if len(selected) != 1:
                raise RuntimeError(f'Сначала запустите настройку Healthchecks для задачи {current}')
            report(current, selected[0], key, ROOT, now)
        except RuntimeError as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError('; '.join(failures))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else 'Ошибка внешнего контроля Healthchecks; секреты скрыты')
        raise SystemExit(1) from None

