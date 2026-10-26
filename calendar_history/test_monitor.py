import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch
from calendar_history.monitor import build_rows, run
from seats.monitor import normalize


def snap(day, counts):
    return {'date_from': day, 'date_to': '2027-04-26', 'checked_at': day+'T09:00:00+05:00',
            'slots': [{'key': d+'T05:45', 'date': d, 'time': '05:45', 'available': n} for d,n in counts.items()]}


class HistoryTests(unittest.TestCase):
    def test_new_tail_baseline_zero_increases_and_missing(self):
        rows = build_rows([snap('2026-10-26', {'2027-04-25': 12}),
                           snap('2026-10-27', {'2027-04-25': 6, '2027-04-26': 19}),
                           snap('2026-10-28', {'2027-04-25': 0, '2027-04-26': 21}),
                           snap('2026-10-29', {})])
        self.assertEqual(rows[1]['daily_decrease'], 6)
        self.assertEqual(rows[2]['initial'], 19)
        self.assertIsNone(rows[2]['daily_decrease'])
        self.assertEqual(rows[3]['remaining'], 0)
        self.assertEqual(rows[3]['total_decrease'], 12)
        self.assertEqual(rows[4]['daily_decrease'], -2)
        self.assertIsNone(rows[5]['remaining'])
        self.assertIsNone(rows[5]['daily_decrease'])

    def test_gap_is_not_called_daily_sales(self):
        rows = build_rows([snap('2026-10-26', {'2027-04-25': 12}), snap('2026-10-28', {'2027-04-25': 9})])
        self.assertIsNone(rows[-1]['daily_decrease'])
        self.assertEqual(rows[-1]['total_decrease'], 3)

    def test_full_calendar_includes_last_day(self):
        payload = {'timezone': 'Asia/Almaty', 'begin': '2026-10-27', 'end': '2027-04-26',
                   'schedule': {'2027-04-26': [{'type':'slot','time':'05:45','available_persons':12}]}}
        result = normalize(payload, datetime.fromisoformat('2026-10-26T09:00:00+05:00'), full_calendar=True)
        self.assertEqual(result['date_to'], '2027-04-26')
        self.assertEqual(len(result['slots']), 1)
        self.assertEqual(len(normalize(payload, datetime.fromisoformat('2026-10-26T09:00:00+05:00'))['slots']), 0)

    def test_retry_preserves_first_snapshot_and_daily_delivery(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'123:test','TELEGRAM_CHAT_ID':'456','GITHUB_EVENT_NAME':'workflow_dispatch'}):
            collector = Mock(return_value=snap('2026-10-26', {'2027-04-25':12}))
            sender = Mock(side_effect=RuntimeError('send failed'))
            now = datetime.fromisoformat('2026-10-26T09:00:00+05:00')
            with self.assertRaises(RuntimeError):
                run(Path(tmp), now, collector, sender)
            self.assertTrue((Path(tmp)/'daily/2026-10-26.json').exists())
            sender = Mock()
            run(Path(tmp), now, collector, sender)
            run(Path(tmp), now, collector, sender)
            self.assertEqual(collector.call_count, 1)
            self.assertEqual(sender.call_count, 1)
