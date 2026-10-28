import unittest
import tempfile
import json
from pathlib import Path
from datetime import datetime
from health.check import assess, notifications, TZ

class HealthTests(unittest.TestCase):
    def test_deadlines_and_retry_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = assess(root, datetime(2026,10,27,12,59,tzinfo=TZ))
            self.assertNotIn('1:2026-10-27', before)
            self.assertNotIn('3:2026-10-27T11:00', before)
            after = assess(root, datetime(2026,10,27,13,tzinfo=TZ))
            self.assertFalse(after['1:2026-10-27'][1])
            self.assertFalse(after['3:2026-10-27T11:00'][1])
            self.assertNotIn('3:2026-10-27T20:00', after)
            late = assess(root, datetime(2026,10,27,22,tzinfo=TZ))
            self.assertIn('3:2026-10-27T20:00', late)

    def test_seat_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data/seats').mkdir(parents=True)
            (root / 'data/seats/state.json').write_text(json.dumps({'last_success':'2026-10-27T12:00:00+05:00'}))
            self.assertTrue(assess(root, datetime(2026,10,27,13,30,tzinfo=TZ))['2:51584'][1])
            self.assertFalse(assess(root, datetime(2026,10,27,13,31,tzinfo=TZ))['2:51584'][1])

    def test_no_duplicate_and_recovery(self):
        state = {}
        now = datetime(2026,10,27,22,tzinfo=TZ)
        failed = {'1:2026-10-27':('Task',False,'interval','сбор данных')}
        ok = {'1:2026-10-27':('Task',True,'interval','сбор данных')}
        self.assertEqual(len(notifications(failed,state,now)),1)
        self.assertEqual(notifications(failed,state,now),[])
        self.assertEqual(len(notifications(ok,state,now)),1)
        self.assertEqual(notifications(ok,state,now),[])

    def test_task_two_service_alerts_and_legacy_recovery_are_silent(self):
        now = datetime(2026,10,28,20,tzinfo=TZ)
        state = {f'2:{tour}': 'failed' for tour in (51584,51691,83542,103689)}
        for ok in (False, True, False):
            checks = {key: ('Задача 2', ok, 'interval', 'сбор данных') for key in state}
            self.assertEqual(notifications(checks, state, now), [])
            self.assertTrue(all(value == ('ok' if ok else 'failed') for value in state.values()))
