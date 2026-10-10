import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from delivery_policy import delivery_allowed
import telegram_report as tg


class MorningTests(unittest.TestCase):
    def test_window_boundaries_and_utc_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stamp, expected in [
                ("2026-10-11T07:59:59+05:00", False),
                ("2026-10-11T08:00:00+05:00", True),
                ("2026-10-11T09:59:59+05:00", True),
                ("2026-10-11T10:00:00+05:00", False),
                ("2026-10-11T03:30:00+00:00", True),
                ("2026-10-11T15:37:00+05:00", False)]:
                self.assertEqual(delivery_allowed("schedule", datetime.fromisoformat(stamp), root)[0], expected)

    def test_receipt_suppresses_other_attempts_and_resets_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = root / "data/telegram/2026-10-11.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text("{}")
            self.assertFalse(delivery_allowed("schedule", datetime.fromisoformat("2026-10-11T09:00:00+05:00"), root)[0])
            self.assertTrue(delivery_allowed("schedule", datetime.fromisoformat("2026-10-12T09:00:00+05:00"), root)[0])

    def test_push_never_sends_and_manual_can_run_outside_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime.fromisoformat("2026-10-11T15:00:00+05:00")
            self.assertFalse(delivery_allowed("push", now, Path(tmp))[0])
            self.assertTrue(delivery_allowed("workflow_dispatch", now, Path(tmp))[0])


if __name__ == "__main__":
    unittest.main()
