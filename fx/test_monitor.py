import unittest
from fx.monitor import changed, parse_quote

class FxTests(unittest.TestCase):
    def test_threshold(self):
        for a,b in [('5.65','5.59'),('5.65','5.71'),('5.99','6.00'),('5.65','6.65')]:
            self.assertTrue(changed(a,b))
        for a,b in [('5.65','5.69'),('5.60','5.6999'),('5.65','5.65')]:
            self.assertFalse(changed(a,b))
    def test_main_quote_only(self):
        import json
        def page(source='RUB', target='KZT'):
            data = {'props': {'pageProps': {'model': {'rate': {
                'value': 5.66629, 'sourceCurrency': {'code': source}, 'targetCurrency': {'code': target}
            }}}}}
            return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'
        self.assertEqual(parse_quote(page()), '5.66629')
        with self.assertRaises(ValueError):
            parse_quote(page('KZT','RUB'))
        with self.assertRaises(ValueError):
            parse_quote('Access denied')
