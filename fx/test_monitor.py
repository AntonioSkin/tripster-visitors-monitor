import unittest
from fx.monitor import changed, parse_quote

class FxTests(unittest.TestCase):
    def test_threshold(self):
        for a,b in [('5.65','5.59'),('5.65','5.71'),('5.99','6.00'),('5.65','6.65')]:
            self.assertTrue(changed(a,b))
        for a,b in [('5.65','5.69'),('5.60','5.6999'),('5.65','5.65')]:
            self.assertFalse(changed(a,b))
    def test_main_quote_only(self):
        self.assertEqual(parse_quote('RUB/KZT <div data-test="instrument-price-last">5.6577</div><div>5.5254</div>'),'5.6577')
        with self.assertRaises(ValueError):
            parse_quote('RUB/KZT <div>5.6577</div>')
        with self.assertRaises(ValueError):
            parse_quote('Access denied')
