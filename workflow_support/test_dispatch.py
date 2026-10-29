import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from monitor import TZ
from workflow_support.dispatch import dispatch, due_tasks
from health.external import CALENDAR_TOURS


class DispatchTests(unittest.TestCase):
    def save(self, root, path, value):
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value))

    def test_before_morning_only_seats_need_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(due_tasks(Path(tmp), datetime(2026, 10, 29, 7, tzinfo=TZ)), ['2', '5'])

    def test_completed_observations_do_not_dispatch_more_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 10, 29, 11, 30, tzinfo=TZ)
            self.save(root, 'data/telegram/2026-10-29.json', {'sent_at': now.isoformat()})
            self.save(root, 'data/calendar_history/joint/telegram/2026-10-29T11:00.json', {'sent_at': now.isoformat()})
            self.save(root, 'data/fx/completed/2026-10-29T11:00.json', {'alert_sent': False})
            for tour in (51584, 51691, 83542, 103689):
                base = 'data/seats' if tour == 51584 else f'data/seats/{tour}'
                self.save(root, base + '/state.json', {'last_success': now.isoformat()})
                self.save(root, base + '/status.json', {'ok': True})
            for tour in CALENDAR_TOURS:
                self.save(root, f'data/closures/{tour}/state.json', {'last_success': now.isoformat()})
                self.save(root, f'data/closures/{tour}/status.json', {'ok': True})
            self.save(root, 'data/closures/completed.json', {'tours': list(CALENDAR_TOURS)})
            self.save(root, 'data/closures/status.json', {'ok': True})
            self.assertEqual(due_tasks(root, now), [])
            self.assertEqual(due_tasks(root, now.replace(hour=20)), ['2', '3', '4', '5'])
            self.save(root, 'data/closures/54971/status.json', {'ok': False})
            self.assertEqual(due_tasks(root, now), ['5'])

    def test_backup_uses_normal_workflow_and_skips_an_active_run(self):
        api = Mock(side_effect=[{'workflow_runs': [{'status': 'in_progress'}]}, {'workflow_runs': []}, {}])
        dispatch(['2', '4'], api)
        self.assertEqual(api.call_count, 3)
        self.assertEqual(api.call_args_list[-1].args,
                         ('/actions/workflows/fx.yml/dispatches', {'ref': 'main'}))

    def test_one_failed_dispatch_does_not_block_other_tasks(self):
        api = Mock(side_effect=[RuntimeError('HTTP 500'), {'workflow_runs': []}, {}])
        with self.assertRaisesRegex(RuntimeError, 'tasks 2'):
            dispatch(['2', '3'], api)
        self.assertEqual(api.call_args_list[-1].args[0], '/actions/workflows/calendar-history.yml/dispatches')


if __name__ == '__main__':
    unittest.main()

