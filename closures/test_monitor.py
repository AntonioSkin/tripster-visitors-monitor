import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from calendar_history.combined import GROUPS
from closures.monitor import EXPERIENCES, alert_text, prepare, read, run_all, run_check
from monitor import TZ


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 10, 29, 12, tzinfo=TZ)
        self.tour = EXPERIENCES[0]
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'test', 'TELEGRAM_CHAT_ID': 'test'}).start()

    def snapshot(self, counts, now=None, keys=None):
        now = now or self.now
        keys = keys or [f'2026-10-30T0{6 + i}:00' for i in range(len(counts))]
        return {'experience_id': self.tour['id'], 'checked_at': now.isoformat(),
                'date_from': now.date().isoformat(), 'date_to': (now.date() + timedelta(days=29)).isoformat(),
                'timezone': 'Asia/Almaty', 'slots': [
                    {'key': key, 'date': key[:10], 'time': key[11:], 'available': count}
                    for key, count in zip(keys, counts)]}

    def test_exact_task_three_membership(self):
        expected = [(group['name'], tour['id']) for group in GROUPS for tour in group['tours']]
        self.assertEqual([(tour['guide'], tour['id']) for tour in EXPERIENCES], expected)
        self.assertTrue(all(tour['title'] for tour in EXPERIENCES))

    def test_baseline_then_independent_times_zero_dedup_and_reopening(self):
        state, events = prepare(self.snapshot([12, 0]), {})
        self.assertEqual(events, [])  # A pre-existing zero is not a newly observed closure.
        state, events = prepare(self.snapshot([0, 0], self.now + timedelta(minutes=30)), state)
        self.assertEqual([event['previous'] for event in events], [12])
        first_id = events[0]['event_id']
        state, events = prepare(self.snapshot([0, 0], self.now + timedelta(hours=1)), state)
        self.assertEqual(events, [])
        state, events = prepare(self.snapshot([5, 0], self.now + timedelta(hours=2)), state)
        self.assertEqual(events, [])
        state, events = prepare(self.snapshot([0, 0], self.now + timedelta(hours=3)), state)
        self.assertEqual(events[0]['previous'], 5)
        self.assertNotEqual(events[0]['event_id'], first_id)
        text = alert_text(events, self.tour)
        for value in ('Задача 5', 'Юлия', '51192', '30.10.2026', '06:00', '5 → 0', self.tour['title']):
            self.assertIn(value, text)

    def test_positive_decrease_or_missing_departure_is_not_zero(self):
        state, _ = prepare(self.snapshot([12, 6]), {})
        state, events = prepare(self.snapshot([7], self.now + timedelta(minutes=30)), state)
        self.assertEqual(events, [])
        self.assertEqual(state['slots']['2026-10-30T07:00']['available'], 6)
        state, events = prepare(self.snapshot([], self.now + timedelta(hours=1)), state)
        self.assertEqual(events, [])
        state, events = prepare(self.snapshot([0], self.now + timedelta(hours=2), ['2026-10-30T07:00']), state)
        self.assertEqual(events[0]['previous'], 6)

    def test_rolling_window_adds_new_baselines_and_prunes_past_departures(self):
        state, _ = prepare(self.snapshot([12, 0], keys=['2026-10-29T15:00', '2026-11-27T06:00']), {})
        tomorrow = self.now + timedelta(days=1)
        state, events = prepare(self.snapshot([0, 0], tomorrow,
                                ['2026-11-27T06:00', '2026-11-28T06:00']), state)
        self.assertEqual(events, [])
        self.assertNotIn('2026-10-29T15:00', state['slots'])
        self.assertIn('2026-11-28T06:00', state['slots'])

    def test_invalid_or_old_collection_does_not_replace_baseline(self):
        baseline = self.snapshot([12])
        run_check(self.tour, self.root, lambda: baseline, now_fn=lambda: self.now)
        before = read(self.root / 'state.json')
        for bad in (self.snapshot([None]), self.snapshot([-1]),
                    self.snapshot([0], self.now - timedelta(minutes=1))):
            with self.assertRaises(RuntimeError):
                run_check(self.tour, self.root, lambda: bad, now_fn=lambda: self.now)
            self.assertEqual(read(self.root / 'state.json'), before)
            self.assertFalse(read(self.root / 'status.json')['ok'])

    def test_unsent_event_survives_next_zero_snapshot_then_is_not_sent_again(self):
        sender = Mock(side_effect=RuntimeError('delivery failed'))
        baseline, zero = self.snapshot([6]), self.snapshot([0], self.now + timedelta(minutes=30))
        run_check(self.tour, self.root, lambda: baseline, send_fn=sender, now_fn=lambda: self.now)
        sender.assert_not_called()
        with self.assertRaises(RuntimeError):
            run_check(self.tour, self.root, lambda: zero, send_fn=sender, now_fn=lambda: self.now)
        state = read(self.root / 'state.json')
        self.assertEqual(len(state['pending']), 1)
        self.assertEqual(state['last_success'], baseline['checked_at'])
        self.assertEqual(read(self.root / 'status.json')['stage'], 'delivery')
        sender = Mock()
        run_check(self.tour, self.root, lambda: zero, send_fn=sender, now_fn=lambda: self.now)
        sender.assert_called_once()
        self.assertEqual(read(self.root / 'state.json')['pending'], [])
        self.assertEqual(read(self.root / 'state.json')['last_success'], zero['checked_at'])
        self.assertEqual(len(read(self.root / 'alerts/2026-10-29.json')), 1)
        run_check(self.tour, self.root, lambda: zero, send_fn=sender, now_fn=lambda: self.now)
        self.assertEqual(sender.call_count, 1)

    def test_all_tours_continue_after_failure_and_no_complete_receipt_is_created(self):
        def check(**kwargs):
            if kwargs['experience']['id'] == 51192:
                raise RuntimeError('collection failed')
        checker, sender = Mock(side_effect=check), Mock()
        with self.assertRaises(RuntimeError):
            run_all(self.root, check_fn=checker, send_fn=sender, now_fn=lambda: self.now)
        self.assertEqual(checker.call_count, 7)
        self.assertFalse((self.root / 'completed.json').exists())
        self.assertEqual(read(self.root / 'status.json')['failed'], [51192])
        sender.assert_not_called()

    def test_success_without_changes_only_sends_setup_once_and_keeps_a_complete_receipt(self):
        checker, sender = Mock(), Mock()
        for _ in range(2):
            run_all(self.root, check_fn=checker, send_fn=sender, now_fn=lambda: self.now)
        self.assertEqual(checker.call_count, 14)
        sender.assert_called_once()
        complete = read(self.root / 'completed.json')
        self.assertTrue(complete['ok'])
        self.assertEqual(set(complete['tours']), {tour['id'] for tour in EXPERIENCES})


if __name__ == '__main__':
    unittest.main()
