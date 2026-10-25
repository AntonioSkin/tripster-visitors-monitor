import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from seats.monitor import normalize, changed_low_slots, next_notifications, run_check
from seats.monitor import EXPERIENCES, alert_text, run_all

NOW = datetime.fromisoformat("2026-10-25T10:00:00+05:00")


def payload(count=6):
    return {"timezone": "Asia/Aqtau", "begin": "2026-10-26", "end": "2027-01-01",
            "defaults": {"available_persons": 12},
            "schedule": {"2026-10-26": [{"type": "slot", "time": "05:45",
                                        "available_persons": count, "booked_persons": 7}]}}


class SeatsTests(unittest.TestCase):
    def test_tour_identity_and_message(self):
        for tour in EXPERIENCES:
            snap = normalize(payload(), NOW, tour)
            self.assertEqual(snap['experience_id'], tour['id'])
            self.assertIn(str(tour['id']), snap['source'])
            message = alert_text(snap, snap['slots'], tour)
            self.assertIn(tour['title'], message)
            self.assertIn(tour['guide'], message)

    def test_tours_have_separate_state_and_continue_after_failure(self):
        checker = Mock(side_effect=[RuntimeError('failed'), None, None, None])
        with self.assertRaises(RuntimeError):
            run_all(checker, Path('/tmp/seat-test'))
        self.assertEqual(checker.call_count, 4)
        paths = [call.kwargs['directory'] for call in checker.call_args_list]
        self.assertEqual(len(set(paths)), 4)
        self.assertEqual(paths[0], Path('/tmp/seat-test'))

    def test_remaining_is_not_capacity_minus_booked(self):
        snap = normalize(payload(8), NOW)
        self.assertEqual(snap["slots"][0]["available"], 8)
        self.assertEqual(changed_low_slots(snap, {}), [])

    def test_zero_six_seven_and_dedup(self):
        for count in (0, 6, 7):
            snap = normalize(payload(count), NOW)
            self.assertEqual(len(changed_low_slots(snap, {})), int(count < 7))
            state = {"notified": next_notifications(snap, {})}
            self.assertEqual(changed_low_slots(snap, state), [])

    def test_recovery_rearms_alert(self):
        low = normalize(payload(6), NOW)
        state = {"notified": next_notifications(low, {})}
        state = {"notified": next_notifications(normalize(payload(7), NOW), state)}
        self.assertEqual(len(changed_low_slots(low, state)), 1)

    def test_changed_low_count_alerts_again(self):
        first = normalize(payload(6), NOW)
        state = {"notified": next_notifications(first, {})}
        self.assertEqual(len(changed_low_slots(normalize(payload(5), NOW), state)), 1)

    def test_invalid_not_zero_and_defaults(self):
        for invalid in (None, True, -1, "6", 2.5):
            with self.assertRaises(ValueError):
                normalize(payload(invalid), NOW)
        data = payload()
        del data["schedule"]["2026-10-26"][0]["available_persons"]
        self.assertEqual(normalize(data, NOW)["slots"][0]["available"], 12)
        del data["defaults"]["available_persons"]
        with self.assertRaises(ValueError):
            normalize(data, NOW)

    def test_window_and_missing_date(self):
        data = payload()
        entry = data["schedule"]["2026-10-26"][0]
        data["schedule"]["2026-11-23"] = [entry]
        data["schedule"]["2026-11-24"] = [entry]
        snap = normalize(data, NOW)
        self.assertEqual(snap["date_to"], "2026-11-23")
        self.assertEqual(len(snap["slots"]), 2)
        self.assertIn("2026-10-27", snap["dates_without_slots"])

    def test_evening_calendar_begin_can_advance(self):
        data = payload(6)
        entry = data["schedule"].pop("2026-10-26")
        data["begin"] = "2026-10-27"
        data["schedule"]["2026-11-06"] = entry
        snap = normalize(data, NOW.replace(hour=20))
        self.assertEqual(changed_low_slots(snap, {})[0]["available"], 6)
        self.assertIn("2026-10-26", snap["dates_without_slots"])
        self.assertEqual(snap["date_to"], "2026-11-23")

    def test_short_coverage_rejected(self):
        data = payload()
        data["end"] = "2026-10-28"
        with self.assertRaises(ValueError):
            normalize(data, NOW)

    def test_send_failure_does_not_mark_alert_delivered(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,
                {"TELEGRAM_BOT_TOKEN": "123:test", "TELEGRAM_CHAT_ID": "456"}):
            directory = Path(tmp)
            sender = Mock(side_effect=RuntimeError("Network"))
            with self.assertRaises(RuntimeError):
                run_check(lambda: normalize(payload(6), NOW), sender, directory)
            self.assertFalse((directory / "state.json").exists())
            self.assertTrue((directory / "latest.json").exists())


if __name__ == "__main__":
    unittest.main()
