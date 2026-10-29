"""Independent deadline checks; no Tripster/browser dependency."""
import base64
import json
import os
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from monitor import ROOT, TZ
from telegram_report import send_message


def read(root, path):
    p = root / path
    return json.loads(p.read_text()) if p.exists() else {}


def assess(root, now):
    now = now.astimezone(TZ)
    checks = {}
    # Include yesterday so a delayed watchdog does not silently miss a deadline.
    for offset in (1, 0):
        day = now.date() - timedelta(days=offset)
        ds = day.isoformat()
        if now >= datetime.combine(day, datetime.min.time(), TZ).replace(hour=13):
            data = read(root, f'data/daily/{ds}.json')
            rows = data.get('results', [])
            complete = len(rows) == 10 and all(r.get('status') == 'ok' for r in rows)
            delivered = bool(read(root, f'data/telegram/{ds}.json'))
            checks[f'1:{ds}'] = ('Задача 1 — посетители', complete and delivered,
                f'{ds}, до 13:00', 'сбор данных' if not complete else 'доставка в Telegram')
        for hour, deadline in ((11, 13), (20, 22)):
            if now < datetime.combine(day, datetime.min.time(), TZ).replace(hour=deadline):
                continue
            obs = f'{ds}T{hour}:00'
            receipt = read(root, f'data/calendar_history/joint/telegram/{obs}.json')
            if hour == 11:
                receipt = receipt or read(root, f'data/calendar_history/joint/telegram/{ds}.json')
            paths = [f'data/calendar_history/{i}/measurements/{obs}.json' for i in (51192,96862,53900)]
            paths += [f'data/calendar_history/dmitry/tours/{i}/measurements/{obs}.json' for i in (57434,85543,55575,54971)]
            complete = all((root / p).exists() for p in paths)
            checks[f'3:{obs}'] = ('Задача 3 — Юлия и Дмитрий', bool(receipt),
                f'{ds}, замер {hour}:00, попытки до {deadline}:00',
                'доставка в Telegram' if complete else 'сбор данных')
    from fx.monitor import ENABLED_FROM
    for offset in (1, 0):
        day = now.date() - timedelta(days=offset)
        for hour, deadline in ((11, 13), (20, 22)):
            obs = f'{day.isoformat()}T{hour}:00'
            if obs < ENABLED_FROM or now < datetime.combine(day, datetime.min.time(), TZ).replace(hour=deadline):
                continue
            complete = bool(read(root, f'data/fx/completed/{obs}.json'))
            collected = (root / f'data/fx/measurements/{obs}.json').exists()
            checks[f'4:{obs}'] = ('Задача 4 — курс RUB/KZT', complete,
                f'{day}, замер {hour}:00, попытки до {deadline}:00',
                'доставка в Telegram' if collected else 'сбор данных')
    for tour in (51584,51691,83542,103689):
        path = 'data/seats' + ('' if tour == 51584 else f'/{tour}')
        state = read(root, path + '/state.json')
        last = state.get('last_success')
        ok = bool(last) and now - datetime.fromisoformat(last) <= timedelta(minutes=90)
        checks[f'2:{tour}'] = (f'Задача 2 — места 0–6, экскурсия {tour}', ok,
            'последняя успешная проверка: ' + (last or 'не найдена'), 'сбор данных')
    last = read(root, 'data/closures/completed.json').get('completed_at')
    ok = bool(last) and timedelta(0) <= now - datetime.fromisoformat(last) <= timedelta(minutes=90)
    checks['5:closures'] = ('Задача 5 — закрытие мест конкурентов', ok,
        'последняя успешная проверка всех семи экскурсий: ' + (last or 'не найдена'), 'сбор данных и отправка')
    # Report deadline violations even when collection recovered before this check.
    for key, check in list(checks.items()):
        if key.startswith(('2:', '5:')) or not check[1]:
            continue
        day = datetime.fromisoformat(key[2:12]).replace(tzinfo=TZ)
        if key.startswith('1:'):
            receipt = read(root, f'data/telegram/{key[2:]}.json')
            stamp, deadline = receipt.get('sent_at'), day.replace(hour=13)
        elif key.startswith('3:'):
            receipt = read(root, f'data/calendar_history/joint/telegram/{key[2:]}.json')
            stamp = receipt.get('sent_at')
            deadline = day.replace(hour=22 if 'T20:' in key else 13)
        else:
            receipt = read(root, f'data/fx/completed/{key[2:]}.json')
            stamp = receipt.get('completed_at', receipt.get('checked_at'))
            deadline = day.replace(hour=22 if 'T20:' in key else 13)
        if stamp and datetime.fromisoformat(stamp) > deadline:
            at = datetime.fromisoformat(stamp).astimezone(TZ).strftime('%d-%m-%Y %H:%M')
            checks[key + ':late'] = (check[0], False,
                check[2] + f'. Фактически завершено: {at} (Шымкент).', 'пропуск срока')
    return checks


def notifications(checks, state, now):
    result = []
    for key, (title, ok, interval, stage) in checks.items():
        # Frequent tasks 2 and 5 use only the separate Healthchecks chat for service alerts.
        # Keep tracking state, including old failures, without sending recovery duplicates.
        if key.startswith(('2:', '5:')):
            state[key] = 'ok' if ok else 'failed'
            continue
        previous = state.get(key)
        if key.endswith(':late'):
            if previous != 'failed' and state.get(key[:-5]) not in ('failed', 'ok'):
                result.append(f'{title}\nЦелевой интервал пропущен. Задача выполнена с опозданием.\n{interval}')
            state[key] = 'failed'
        elif not ok and previous != 'failed':
            result.append(f'{title}\nПроблема: {stage} не завершён в целевой интервал с учётом повторных попыток.\n{interval}\nВремя — Шымкент.')
            state[key] = 'failed'
        elif ok and previous == 'failed':
            result.append(f'{title}\nРабота восстановлена. Проверка и отправка завершены.\n{interval}')
            state[key] = 'ok'
    return result


def api(path, body=None, method=None):
    request = Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                 'Accept': 'application/vnd.github+json'}, method=method)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    branch = 'monitor-health-state'
    try:
        api('/git/ref/heads/' + branch)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        head = api('/git/ref/heads/main')['object']['sha']
        api('/git/refs', {'ref': 'refs/heads/' + branch, 'sha': head}, 'POST')
    path = '/contents/data/health-state.json'
    try:
        file = api(path + '?ref=' + branch)
        state = json.loads(base64.b64decode(file['content']))
    except HTTPError as exc:
        if exc.code != 404:
            raise
        file, state = {}, {}
    now = datetime.now(TZ)
    checks = assess(ROOT, now)
    # Do not raise retrospective alarms for dates before this control was enabled.
    if 'started_on' not in state:
        state['started_on'] = now.date().isoformat()
    checks = {k: v for k, v in checks.items()
              if k.startswith(('2:', '5:')) or k[2:12] >= state['started_on']}
    messages = notifications(checks, state.setdefault('tasks', {}), now)
    for message in messages:
        send_message(os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID'], message)
    body = {'message': 'Save deadline watchdog state', 'branch': branch,
            'content': base64.b64encode(json.dumps(state, ensure_ascii=False).encode()).decode()}
    if file:
        body['sha'] = file['sha']
    api(path, body, 'PUT')
    print(f'Health checks: {len(checks)}; notifications: {len(messages)}')


if __name__ == '__main__':
    main()

