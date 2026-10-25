import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from seats.monitor import normalize, changed_low_slots, next_notifications, run_check

NOW = datetime.fromisoformat("2026-10-25T10:00:00+05:00")


def payload(count=6):
    return {"timezone": "Asia/Aqtau", "begin": "2026-10-26", "end": "2027-01-01",
            "defaults": {"available_persons": 12},
            "schedule": {"2026-10-26": [{"type": "slot", "time": "05:45",
                                        "available_persons": count, "booked_persons": 7}]}}


class SeatsTests(unittest.TestCase):
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
