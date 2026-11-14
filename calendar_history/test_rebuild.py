import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from calendar_history.combined import GROUPS
from calendar_history.rebuild import rebuild_tables


class RebuildTests(unittest.TestCase):
    def test_rebuild_restores_both_guides_and_preserves_snapshots_and_receipts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            originals = {}
            for group in GROUPS:
                for tour in group['tours']:
                    directory = (root / 'data/calendar_history' / str(tour['id'])
                                 if group['key'] == 'yulia'
                                 else root / 'data/calendar_history/dmitry/tours' / str(tour['id']))
                    for hour, counts in ((8, (12, 6)), (11, (8, 6)), (14, (None, None))):
                        observed = f'2026-11-05T{hour:02}:00'
                        snapshot = {'date_from': '2026-11-05', 'observation_id': observed,
                                    'checked_at': observed + ':00+05:00',
                                    'slots': [{'key': f'2026-12-04T{at}', 'available': count}
                                              for at, count in zip(('08:00', '09:00'), counts)
                                              if count is not None]}
                        path = directory / 'measurements' / (observed + '.json')
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps(snapshot), encoding='utf-8')
                        originals[path] = path.read_bytes()
                # Simulate an old summary whose cumulative total was lost.
                path = root / 'data/calendar_history' / group['key'] / 'summary.csv'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('Дата выезда,Чистое уменьшение с начала\n04-12-2026,—\n')
            receipt = root / 'data/calendar_history/joint/telegram/2026-11-05T14:00.json'
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text('{"sent_at":"2026-11-05T14:01:00+05:00"}')
            originals[receipt] = receipt.read_bytes()

            with patch('calendar_history.combined.collect') as collector, \
                    patch('calendar_history.combined.send_message') as sender:
                rebuild_tables(root)
                collector.assert_not_called()
                sender.assert_not_called()

            first = {}
            for group in GROUPS:
                directory = root / 'data/calendar_history' / group['key']
                for name in ('history.csv', 'summary.csv'):
                    path = directory / name
                    with path.open(encoding='utf-8-sig', newline='') as handle:
                        reader = csv.DictReader(handle)
                        rows = list(reader)
                        self.assertEqual(reader.fieldnames[-1], 'Конечный остаток')
                    row = rows[-1]
                    self.assertEqual(row['Чистое уменьшение с начала'], str(4 * len(group['tours'])))
                    self.assertEqual(row['Чистое уменьшение с прошлого замера'], '—')
                    expected = ' '.join(('' if i == 0 else '-') + str(count)
                                        for i in range(len(group['tours'])) for count in (8, 6))
                    self.assertEqual(row['Конечный остаток'], expected)
                    first[path] = path.read_bytes()
            for path, original in originals.items():
                self.assertEqual(path.read_bytes(), original)
            rebuild_tables(root)
            for path, original in first.items():
                self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
