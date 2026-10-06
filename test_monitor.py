import unittest
from monitor import parse_visitors, total, make_report


class CounterTests(unittest.TestCase):
    def test_excursion_not_guide(self):
        self.assertEqual(parse_visitors("463 отзыва, 2121 посетил\nГид: 3680 посетили"), 2121)

    def test_spaces_and_repeated_mobile_markup(self):
        self.assertEqual(parse_visitors("463 отзыва, 12\u202f121 посетили\n463 отзыва, 12 121 посетили"), 12121)

    def test_reverse_order(self):
        self.assertEqual(parse_visitors("2121 посетил, 463 отзыва"), 2121)

    def test_ambiguous_or_missing_not_zero(self):
        for text in ("Гид: 3680 посетили", "Ошибка 403", "1 отзыв, 12 посетили 2 отзыва, 14 посетили"):
            with self.assertRaises(ValueError):
                parse_visitors(text)

    def test_explicit_zero(self):
        self.assertEqual(parse_visitors("0 отзывов, 0 посетили"), 0)

    def test_partial_total(self):
        self.assertIsNone(total([{"status": "ok", "visitors": 12}, {"status": "error", "visitors": None}]))

    def test_report_delta_and_negative_correction(self):
        def snap(value):
            return {"checked_at": "2026-10-06T08:15:00+05:00", "results": [
                {"id": 1, "name": "Тур", "url": "https://example.com", "status": "ok", "visitors": value}]}
        self.assertIn("-1", make_report(snap(11), snap(12)))
        self.assertIn("| **Итого** | **11** | **—** |", make_report(snap(11), None))


if __name__ == "__main__":
    unittest.main()
