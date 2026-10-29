import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from health.external import CALENDAR_TOURS, TZ, completion, first_deadline, report, request, setup


class ExternalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 10, 28, 11, 30, tzinfo=TZ)
        self.at = self.now - timedelta(minutes=2)
        self.obs = '2026-10-28T11:00'

    def save(self, path, value):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value))

    def fx(self, alert=False):
        value = {'rate': '5.65901', 'checked_at': self.at.isoformat(), 'alert_sent': alert}
        self.save(f'data/fx/measurements/{self.obs}.json', value)
        self.save(f'data/fx/completed/{self.obs}.json', value)

    def test_fx_no_change_is_success_but_collection_without_completion_is_not(self):
        self.fx()
        self.assertEqual(completion('4', self.root, self.now), self.at)
        (self.root / f'data/fx/completed/{self.obs}.json').unlink()
        self.assertIsNone(completion('4', self.root, self.now))

    def test_morning_and_yesterday_cannot_mask_missing_evening_run(self):
        self.fx()
        self.assertIsNone(completion('4', self.root, self.now.replace(hour=20)))
        self.assertIsNone(completion('4', self.root, self.now + timedelta(days=1)))
        self.assertIsNone(completion('4', self.root, self.now.replace(hour=10)))

    def test_visitors_need_exact_tour_set_and_delivery(self):
        self.save('excursions.json', [{'id': 1}, {'id': 2}])
        value = {'checked_at': self.at.isoformat(), 'results': [
            {'id': 1, 'status': 'ok', 'visitors': 20}, {'id': 2, 'status': 'ok', 'visitors': 0}]}
        self.save('data/daily/2026-10-28.json', value)
        self.assertIsNone(completion('1', self.root, self.now))
        self.save('data/telegram/2026-10-28.json', {'sent_at': self.at.isoformat()})
        self.assertEqual(completion('1', self.root, self.now), self.at)
        value['results'][1]['id'] = 1
        self.save('data/daily/2026-10-28.json', value)
        self.assertIsNone(completion('1', self.root, self.now))

    def test_seats_require_all_four_recent_successes(self):
        for i in (51584, 51691, 83542, 103689):
            base = 'data/seats' if i == 51584 else f'data/seats/{i}'
            self.save(base + '/state.json', {'last_success': self.at.isoformat()})
            self.save(base + '/status.json', {'ok': True})
        self.assertEqual(completion('2', self.root, self.now), self.at)
        self.save('data/seats/103689/status.json', {'ok': False})
        self.assertIsNone(completion('2', self.root, self.now))
        self.save('data/seats/103689/status.json', {'ok': True})
        self.assertIsNone(completion('2', self.root, self.now + timedelta(minutes=21)))

    def closures(self):
        for tour in CALENDAR_TOURS:
            self.save(f'data/closures/{tour}/state.json', {
                'last_success': self.at.isoformat(), 'last_observed': self.at.isoformat(), 'pending': []})
            self.save(f'data/closures/{tour}/status.json', {'ok': True, 'checked_at': self.at.isoformat()})
        value = {'ok': True, 'tours': list(CALENDAR_TOURS), 'completed_at': self.at.isoformat()}
        self.save('data/closures/completed.json', value)
        self.save('data/closures/status.json', value)

    def test_closures_require_all_seven_recent_successes_and_empty_outboxes(self):
        self.closures()
        self.assertEqual(completion('5', self.root, self.now), self.at)
        self.save('data/closures/54971/state.json', {
            'last_success': self.at.isoformat(), 'last_observed': self.at.isoformat(), 'pending': [{'event_id': 'unsent'}]})
        self.assertIsNone(completion('5', self.root, self.now))
        self.closures()
        self.save('data/closures/54971/status.json', {'ok': False})
        self.assertIsNone(completion('5', self.root, self.now))
        self.closures()
        self.assertIsNone(completion('5', self.root, self.now + timedelta(minutes=21)))
        self.assertEqual(first_deadline('5', self.root, self.now), self.at + timedelta(minutes=90))

    def test_partial_closure_run_cannot_reuse_an_older_complete_receipt(self):
        self.closures()
        fresh = self.at + timedelta(minutes=1)
        self.save('data/closures/54971/state.json', {
            'last_success': fresh.isoformat(), 'last_observed': fresh.isoformat(), 'pending': []})
        self.save('data/closures/54971/status.json', {'ok': True, 'checked_at': fresh.isoformat()})
        self.assertIsNone(completion('5', self.root, self.now))

    def test_calendar_needs_all_seven_snapshots_and_joint_delivery(self):
        self.save(f'data/calendar_history/joint/telegram/{self.obs}.json', {
            'sent_at': self.at.isoformat(), 'observation_id': self.obs, 'tours': list(CALENDAR_TOURS)})
        for tour, base in CALENDAR_TOURS.items():
            self.save(f'{base}/measurements/{self.obs}.json', {
                'experience_id': tour, 'observation_id': self.obs, 'date_from': '2026-10-28',
                'checked_at': self.at.isoformat(), 'slots': []})
        self.assertEqual(completion('3', self.root, self.now), self.at)
        (self.root / CALENDAR_TOURS[54971] / f'measurements/{self.obs}.json').unlink()
        self.assertIsNone(completion('3', self.root, self.now))

    def test_noop_cannot_extend_external_deadline(self):
        self.fx()
        check = {'last_ping': self.now.isoformat(), 'grace': 7200, 'status': 'up'}
        with patch('health.external.ping') as ping:
            self.assertFalse(report('4', check, 'test', self.root, self.now))
        ping.assert_not_called()

    def test_first_cycle_expires_at_retry_deadline_without_false_success(self):
        checks = []

        def api(url, key=None, body=None, **kwargs):
            if url.endswith('channels/'):
                return {'channels': [{'kind': 'telegram', 'id': 'test-channel'}]}
            if body and 'slug' in body:
                check = dict(body, uuid='00000000-0000-0000-0000-000000000001',
                             ping_url='https://hc-ping.com/00000000-0000-0000-0000-000000000001',
                             status='new', last_ping=None, started=False)
                checks.append(check)
                return check
            if body:
                checks[-1].update(body)
                return checks[-1]
            return {'checks': checks}

        def ping(check, suffix='', message=''):
            self.assertEqual(suffix, '/start')
            check['started'] = True

        with patch('health.external.request', side_effect=api), patch('health.external.ping', side_effect=ping) as send:
            setup('test', self.root, self.now)
        self.assertEqual(send.call_count, 5)
        self.assertEqual(checks[2]['grace'], 90 * 60)
        self.assertEqual(first_deadline('3', self.root, self.now).hour, 13)
        self.assertEqual(first_deadline('5', self.root, self.now), self.now + timedelta(minutes=90))

    def test_real_success_restores_normal_grace_after_initial_start(self):
        self.fx()
        check = {'last_ping': self.now.isoformat(), 'grace': 5400, 'status': 'up', 'started': True}
        with patch('health.external.update', return_value=check) as update, patch('health.external.ping') as send:
            self.assertTrue(report('4', check, 'test', self.root, self.now))
        update.assert_called_once_with('test', check, {'grace': 7200})
        send.assert_called_once()

    def test_request_errors_never_expose_api_or_ping_credentials(self):
        error = HTTPError('https://example.invalid/SECRET', 401, 'SECRET', {}, None)
        with patch('health.external.urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                request('https://example.invalid/SECRET', 'SECRET')
        self.assertNotIn('SECRET', str(caught.exception))


if __name__ == '__main__':
    unittest.main()

