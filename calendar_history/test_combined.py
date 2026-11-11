import unittest
import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from calendar_history.combined import merge_rows, report_text, GROUPS
from calendar_history.monitor import build_rows
from calendar_history import combined


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
        self.assertIn('Юлия — продано 5 мест (всего —)', text)
        self.assertIn('Дмитрий — продано 3 места (всего 3)', text)
        self.assertNotIn('Всего —', text)
        self.assertEqual(text.count('29.10.2026:'), 1)

    def test_closure_only_and_new_time(self):
        rows = history([('08:00',12),('09:00',12)], [('08:00',12),('10:00',12)])
        self.assertEqual(merge_rows([rows], '2026-10-28')[0]['daily'], 0)

    def test_initial_does_not_invent_total(self):
        rows = merge_rows([history([('08:00',12)], [('08:00',10)])], '2026-10-28')
        text = report_text({'yulia': rows, 'dmitry': []})
        self.assertIn('Дмитрий — нет сравнения (всего —)', text)
        self.assertNotIn('Всего —', text)

    def test_each_guide_keeps_total_since_first_observation(self):
        from calendar_history.combined import interval_rows
        observations = ('2026-11-01T11:00', '2026-11-01T20:00', '2026-11-02T11:00')

        def latest(counts):
            snapshots = [
                {'date_from': observed[:10], 'checked_at': observed + ':00+05:00',
                 'observation_id': observed,
                 'slots': [{'key': '2026-12-04T08:00', 'available': count}]}
                for observed, count in zip(observations, counts)
            ]
            return merge_rows([interval_rows(snapshots)], observations[-1])

        for counts, cumulative in (([12, 12, 12], 0), ([12, 9, 9], 3)):
            with self.subTest(dmitry_counts=counts):
                text = report_text({'yulia': latest([12, 10, 8]), 'dmitry': latest(counts)})
                self.assertIn('Юлия — продано 2 места (всего 4)', text)
                self.assertIn(f'Дмитрий — продано 0 мест (всего {cumulative})', text)
                self.assertNotIn('Всего —', text)

    def test_added_places_reduce_the_same_guide_total(self):
        yulia = merge_rows([history([('08:00',12)], [('08:00',14)])], '2026-10-28')
        dmitry = merge_rows([history([('08:00',12)], [('08:00',12)])], '2026-10-28')
        text = report_text({'yulia': yulia, 'dmitry': dmitry})
        self.assertIn('Юлия — добавлено 2 места (всего -2)', text)
        self.assertIn('Дмитрий — продано 0 мест (всего 0)', text)

    def test_configuration(self):
        self.assertEqual([t['id'] for t in GROUPS[1]['tours']], [57434,85543,55575,54971])


class HistoricalScheduleTests(unittest.TestCase):
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


class ThreeHourlyTests(unittest.TestCase):
    def test_all_slots_in_local_time_and_utc(self):
        from datetime import datetime, timezone
        from calendar_history.combined import observation_id, TZ
        for hour in (8, 11, 14, 17, 20, 23):
            at = datetime(2026, 11, 3, hour, tzinfo=TZ)
            expected = f'2026-11-03T{hour:02}:00'
            with self.subTest(hour=hour):
                self.assertEqual(observation_id(at), expected)
                self.assertEqual(observation_id(at.astimezone(timezone.utc)), expected)
                self.assertEqual(observation_id(at.replace(minute=59, second=59)), expected)
        self.assertIsNone(observation_id(datetime(2026, 11, 3, 7, 59, 59, tzinfo=TZ)))
        self.assertIsNone(observation_id(datetime(2026, 11, 4, 0, tzinfo=TZ)))

    def test_one_delivery_per_slot_and_next_slot_is_due(self):
        from datetime import datetime
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from calendar_history.combined import due_observation, TZ
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'telegram').mkdir()
            for hour in (8, 11, 14, 17, 20, 23):
                at = datetime(2026, 11, 3, hour, tzinfo=TZ)
                obs = f'2026-11-03T{hour:02}:00'
                self.assertEqual(due_observation(at, directory), obs)
                (directory / 'telegram' / f'{obs}.json').write_text('{}')
                self.assertIsNone(due_observation(at.replace(minute=40), directory))

    def test_rollout_does_not_invent_earlier_missed_slots(self):
        from datetime import datetime
        from calendar_history.combined import observation_id, TZ
        self.assertIsNone(observation_id(datetime(2026, 11, 2, 8, tzinfo=TZ)))
        self.assertEqual(observation_id(datetime(2026, 11, 2, 17, tzinfo=TZ)), '2026-11-02T11:00')
        self.assertEqual(observation_id(datetime(2026, 11, 2, 23, tzinfo=TZ)), '2026-11-02T23:00')


class QuietCompletionTests(unittest.TestCase):
    def run_case(self, *, baseline=True, changed=False, fail_send=False, fail_collect=False):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); directory=root/'data/calendar_history/joint'
            now=datetime.fromisoformat('2026-11-11T17:45:00+05:00')
            for group in GROUPS:
                for tour in group['tours']:
                    base=(root/'data/calendar_history'/str(tour['id']) if group['key']=='yulia'
                          else root/'data/calendar_history/dmitry/tours'/str(tour['id']))
                    if baseline:
                        path=base/'measurements/2026-11-11T14:00.json';path.parent.mkdir(parents=True)
                        path.write_text(json.dumps({'date_from':'2026-11-11','checked_at':'2026-11-11T14:05:00+05:00',
                            'observation_id':'2026-11-11T14:00','slots':[{'key':'2026-12-04T08:00','available':12}]}))
            def collect(tour, **kwargs):
                if fail_collect and tour['id']==51192:
                    raise RuntimeError('test collection failure')
                return {'experience_id':tour['id'],'date_from':'2026-11-11','date_to':'2027-04-26',
                    'checked_at':now.isoformat(),'slots':[{'key':'2026-12-04T08:00',
                    'available':10 if changed and tour['id']==51192 else 12}]}
            collector=Mock(side_effect=collect);sender=Mock()
            receipt=directory/'telegram/2026-11-11T17:00.json'
            with patch.object(combined,'ROOT',root),patch.object(combined,'DIRECTORY',directory),\
                    patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test'} if changed else {},clear=True):
                if fail_send or fail_collect:
                    if fail_send: sender.side_effect=RuntimeError('test delivery failure')
                    with self.assertRaises(RuntimeError):
                        combined.run(now,collector,sender)
                    self.assertFalse(receipt.exists())
                    if fail_collect:
                        sender.assert_not_called()
                        return
                    sender.reset_mock(side_effect=True)
                combined.run(now,collector,sender)
                combined.run(now,collector,sender)
            self.assertEqual(collector.call_count,7)
            evidence=json.loads(receipt.read_text())
            self.assertEqual(evidence['observation_id'],'2026-11-11T17:00')
            self.assertEqual(len(set(evidence['tours'])),7)
            self.assertTrue(evidence['delivery_complete'])
            self.assertEqual(evidence['changes_detected'],changed)
            self.assertEqual(evidence['delivery_required'],changed)
            self.assertTrue(json.loads((directory/'status.json').read_text())['ok'])
            self.assertTrue((root/'data/calendar_history/yulia/summary.csv').exists())
            if changed:
                sender.assert_called_once()
                self.assertIn('Юлия — продано 2 места',sender.call_args.args[2])
                self.assertNotIn('проверка выполнена',sender.call_args.args[2])
                self.assertEqual(evidence['notification'],'sent')
                self.assertEqual(evidence['sent_at'],evidence['completed_at'])
            else:
                sender.assert_not_called()
                self.assertEqual(evidence['notification'],'suppressed_no_changes')
                self.assertNotIn('sent_at',evidence)

    def test_unchanged_observation_is_saved_silently_once(self):
        self.run_case()

    def test_initial_baseline_does_not_send_a_status_message(self):
        self.run_case(baseline=False)

    def test_changes_still_send_the_original_guide_report_once(self):
        self.run_case(changed=True)

    def test_delivery_failure_keeps_immutable_measurements_for_retry(self):
        self.run_case(changed=True,fail_send=True)

    def test_partial_collection_cannot_be_certified_as_quiet_success(self):
        self.run_case(fail_collect=True)

