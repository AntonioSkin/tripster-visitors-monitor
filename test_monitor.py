import unittest
from monitor import parse_api_visitors, total, make_report

class CounterTests(unittest.TestCase):
    def test_excursion_not_guide(self):
        self.assertEqual(parse_api_visitors({"id": 51192, "visitors_count": 2121,
            "guide": {"visitors_count": 3688}}, 51192), 2121)

    def test_missing_or_invalid_not_zero(self):
        for value in (None, True, -1, 2.5, "2121"):
            with self.assertRaises(ValueError):
                parse_api_visitors({"id": 51192, "visitors_count": value}, 51192)

    def test_wrong_excursion(self):
        with self.assertRaises(ValueError):
            parse_api_visitors({"id": 54971, "visitors_count": 401}, 51192)

    def test_explicit_zero(self):
        self.assertEqual(parse_api_visitors({"id": 1, "visitors_count": 0}, 1), 0)

    def test_partial_total(self):
        self.assertIsNone(total([{"status": "ok", "visitors": 12}, {"status": "error", "visitors": None}]))

    def test_report_delta_and_negative_correction(self):
        def snap(value):
            return {"checked_at": "2026-10-07T15:30:00+05:00", "results": [
                {"id": 1, "name": "Тур", "url": "https://example.com", "status": "ok", "visitors": value}]}
        self.assertIn("-1", make_report(snap(11), snap(12)))
        self.assertIn("| **Итого** | **11** | **—** |", make_report(snap(11), None))

if __name__ == "__main__":
    unittest.main()
