import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from delivery_policy import delivery_allowed


class DeliveryTests(unittest.TestCase):
    def test_delayed_runs_are_not_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            for stamp, expected in [
                ("2026-10-14T07:59:59+05:00", False),
                ("2026-10-14T08:00:00+05:00", True),
                ("2026-10-14T10:00:00+05:00", True),
                ("2026-10-14T13:00:00+05:00", True),
                ("2026-10-14T15:37:00+05:00", True),
                ("2026-10-14T22:00:00+05:00", True),
                ("2026-10-14T03:30:00+00:00", True)]:
                self.assertEqual(delivery_allowed("schedule", datetime.fromisoformat(stamp), Path(tmp))[0], expected)

    def test_daily_receipt_and_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = root / "data/telegram/2026-10-14.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text("{}")
            self.assertFalse(delivery_allowed("schedule", datetime.fromisoformat("2026-10-14T15:00:00+05:00"), root)[0])
            self.assertTrue(delivery_allowed("schedule", datetime.fromisoformat("2026-10-15T09:00:00+05:00"), root)[0])

    def test_only_explicit_push_collects(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime.fromisoformat("2026-10-14T22:00:00+05:00")
            with patch.dict(os.environ, {"COLLECT_NOW": "false"}):
                self.assertFalse(delivery_allowed("push", now, Path(tmp))[0])
            with patch.dict(os.environ, {"COLLECT_NOW": "true"}):
                self.assertTrue(delivery_allowed("push", now, Path(tmp))[0])
            self.assertTrue(delivery_allowed("workflow_dispatch", now, Path(tmp))[0])


if __name__ == "__main__":
    unittest.main()
