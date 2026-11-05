"""Rebuild task 3 tables from saved snapshots without collecting or sending."""
import json

from calendar_history.combined import GROUPS, interval_rows, save_group_tables
from calendar_history.monitor import ROOT, write_csv


def rebuild_tables(root=ROOT):
    # Read all inputs before replacing any table. Original snapshots and
    # Telegram receipts are never modified by this migration.
    prepared = []
    for group in GROUPS:
        group_directory = root / 'data/calendar_history' / group['key']
        tours = []
        for tour in group['tours']:
            directory = (root / 'data/calendar_history' / str(tour['id'])
                         if group['key'] == 'yulia'
                         else group_directory / 'tours' / str(tour['id']))
            paths = sorted((directory / 'daily').glob('*.json'))
            paths += sorted((directory / 'measurements').glob('*.json'))
            if not paths:
                raise ValueError(f"No saved task 3 observations for tour {tour['id']}")
            snapshots = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
            tours.append((directory, interval_rows(snapshots)))
        prepared.append((group_directory, tours))

    for group_directory, tours in prepared:
        for directory, rows in tours:
            write_csv(directory / 'history.csv', rows)
            latest = {(r['departure'], r['time']): r for r in rows}
            write_csv(directory / 'summary.csv', list(latest.values()))
        save_group_tables(group_directory, [rows for _, rows in tours])
    print('Task 3: rebuilt saved history and summary tables for both guides and all seven tours.')


if __name__ == '__main__':
    rebuild_tables()
