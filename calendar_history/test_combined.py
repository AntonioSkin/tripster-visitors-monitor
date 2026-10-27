import unittest
from calendar_history.combined import merge_rows, report_text, GROUPS
from calendar_history.monitor import build_rows


def history(before, after):
    return build_rows([
        {'date_from': day, 'slots': [
            {'key': '2026-10-29T' + time, 'available': value}
            for time, value in slots]}
        for day, slots in [('2026-10-27', before), ('2026-10-28', after)]
    ])


class JointTests(unittest.TestCase):
    def test_closed_time_and_guide_totals(self):
        yulia = merge_rows([history([('08:00',12),('09:00',12)], [('08:00',7)])], '2026-10-28')
        dmitry = merge_rows([history([('08:00',12)], [('08:00',9)])], '2026-10-28')
        text = report_text({'yulia': yulia, 'dmitry': dmitry})
        self.assertIn('Юлия — продано 5 мест', text)
        self.assertIn('Дмитрий — продано 3 места', text)
        self.assertIn('Всего — продано 8 мест', text)
        self.assertEqual(text.count('29.10.2026:'), 1)

    def test_closure_only_and_new_time(self):
        rows = history([('08:00',12),('09:00',12)], [('08:00',12),('10:00',12)])
        self.assertEqual(merge_rows([rows], '2026-10-28')[0]['daily'], 0)

    def test_initial_does_not_invent_total(self):
        rows = merge_rows([history([('08:00',12)], [('08:00',10)])], '2026-10-28')
        text = report_text({'yulia': rows, 'dmitry': []})
        self.assertIn('Дмитрий — нет сравнения', text)
        self.assertNotIn('Всего —', text)

    def test_configuration(self):
        self.assertEqual([t['id'] for t in GROUPS[1]['tours']], [57434,85543,55575,54971])


class TwiceDailyTests(unittest.TestCase):
    def test_schedule_and_receipts(self):
        from datetime import datetime, timezone
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from calendar_history.combined import due_observation
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            morning = datetime(2026, 10, 28, 6, tzinfo=timezone.utc)
            evening = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
            self.assertIsNone(due_observation(morning.replace(hour=5, minute=59), directory))
            self.assertEqual(due_observation(morning, directory), '2026-10-28T11:00')
            (directory / 'telegram').mkdir()
            (directory / 'telegram/2026-10-28T11:00.json').write_text('{}')
            self.assertIsNone(due_observation(morning, directory))
            self.assertEqual(due_observation(evening, directory), '2026-10-28T20:00')
            (directory / 'telegram/2026-10-28T20:00.json').write_text('{}')
            self.assertIsNone(due_observation(evening, directory))

    def test_previous_measurement_not_previous_day(self):
        from calendar_history.combined import interval_rows
        snapshots = []
        for observed, counts in [('2026-10-27T11:00', [12, 12]),
                                 ('2026-10-27T20:00', [7]),
                                 ('2026-10-28T11:00', [5])]:
            snapshots.append({
                'date_from': observed[:10], 'checked_at': observed + ':00+05:00',
                'observation_id': observed,
                'slots': [{'key': '2026-10-29T0' + str(8+i) + ':00', 'available': count}
                          for i, count in enumerate(counts)]
            })
        rows = interval_rows(snapshots)
        self.assertEqual(merge_rows([rows], '2026-10-27T20:00')[0]['daily'], 5)
        self.assertEqual(merge_rows([rows], '2026-10-28T11:00')[0]['daily'], 2)

    def test_legacy_report_counts_as_morning_only(self):
        from datetime import datetime
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from calendar_history.combined import due_observation, TZ
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'telegram').mkdir()
            (directory / 'telegram/2026-10-27.json').write_text('{}')
            self.assertIsNone(due_observation(datetime(2026,10,27,14,tzinfo=TZ), directory))
            self.assertEqual(due_observation(datetime(2026,10,27,20,tzinfo=TZ), directory), '2026-10-27T20:00')
