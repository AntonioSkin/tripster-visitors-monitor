"""Cheap backup wakeups dispatch the original task workflows, using their locks."""
import json
import os
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from monitor import ROOT, TZ
from delivery_policy import delivery_allowed
from calendar_history.combined import due_observation
from fx.monitor import observation
from health.external import read, seat_stamps, closure_stamps, CALENDAR_TOURS

WORKFLOWS = {'1': 'daily.yml', '2': 'seats.yml', '3': 'calendar-history.yml', '4': 'fx.yml', '5': 'closures.yml'}


def due_tasks(root, now):
    result = []
    if delivery_allowed('schedule', now, root)[0]:
        result.append('1')
    recent = seat_stamps(root)
    statuses = [read(root, ('data/seats' if tour == 51584 else f'data/seats/{tour}') + '/status.json')
                for tour in (51584, 51691, 83542, 103689)]
    if (not all(recent) or not all(timedelta(0) <= now - value < timedelta(minutes=25) for value in recent)
            or not all(s.get('ok') is True for s in statuses)):
        result.append('2')
    if due_observation(now, root / 'data/calendar_history/joint') is not None:
        result.append('3')
    key = observation(now)
    if key is not None and not (root / f'data/fx/completed/{key}.json').exists():
        result.append('4')
    recent = closure_stamps(root)
    statuses = [read(root, f'data/closures/{tour}/status.json') for tour in CALENDAR_TOURS]
    complete = read(root, 'data/closures/completed.json')
    if (not all(recent) or not all(timedelta(0) <= now - value < timedelta(minutes=25) for value in recent)
            or not all(s.get('ok') is True for s in statuses)
            or read(root, 'data/closures/status.json').get('ok') is not True
            or set(complete.get('tours', [])) != set(CALENDAR_TOURS)):
        result.append('5')
    return result


def api(path, body=None):
    base = 'https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY']
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
               'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'}
    req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                  headers=headers, method='POST' if body is not None else 'GET')
    try:
        with urlopen(req, timeout=20) as response:
            data = response.read()
        return json.loads(data) if data else {}
    except HTTPError as exc:
        raise RuntimeError(f'Backup dispatch GitHub HTTP {exc.code}') from None
    except (URLError, TimeoutError):
        raise RuntimeError('Backup dispatch could not reach GitHub') from None


def dispatch(tasks, api_fn=api):
    failed = []
    for task in tasks:
        workflow = WORKFLOWS[task]
        try:
            runs = api_fn(f'/actions/workflows/{workflow}/runs?branch=main&per_page=50')['workflow_runs']
            if any(run['status'] != 'completed' for run in runs):
                print(f'Task {task}: an existing run is queued or active; backup skipped')
                continue
            api_fn(f'/actions/workflows/{workflow}/dispatches', {'ref': 'main'})
            print(f'Task {task}: backup dispatched through its normal workflow and concurrency group')
        except (RuntimeError, KeyError) as exc:
            failed.append(task)
            print(f'Task {task}: backup dispatch failed')
    if failed:
        raise RuntimeError('Backup dispatch failed for tasks ' + ', '.join(failed))


def main():
    tasks = due_tasks(ROOT, datetime.now(TZ))
    dispatch(tasks)
    if not tasks:
        print('All current observations are complete; no backup work needed')


if __name__ == '__main__':
    main()

