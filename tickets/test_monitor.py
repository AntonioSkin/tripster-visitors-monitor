import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from tickets.monitor import identify, plan, run, secure_document


def row(qr='private-qr', scanned='Нет'):
    return ['Tour', '01.11.2026', 'private-order', 'Adult', qr, '', scanned]


class TicketTests(unittest.TestCase):
    def test_https_upgrade_preserves_existing_security_policy(self):
        route = Mock()
        route.fetch.return_value.headers = {'content-security-policy': "default-src 'self'", 'other': 'kept'}
        secure_document(route)
        headers = route.fulfill.call_args.kwargs['headers']
        self.assertEqual(headers['content-security-policy'], "default-src 'self'; upgrade-insecure-requests")
        self.assertEqual(headers['other'], 'kept')

    def test_baseline_then_new_and_old_status_change(self):
        seen, new = plan(None, [row()])
        self.assertEqual(new, [])
        seen, new = plan({'version': 1, 'seen': list(seen)}, [row(scanned='Да'), row('new-qr')])
        self.assertEqual(new, [row('new-qr')])
        _, new = plan({'version': 1, 'seen': list(seen)}, [row()])
        self.assertEqual(new, [])

    def test_duplicate_missing_id_and_corrupt_state_fail_closed(self):
        for previous, rows in [(None, [row(), row()]), (None, [row('')]),
                               ({'version': 1, 'seen': ['bad']}, [row()]), (None, [])]:
            with self.assertRaises(RuntimeError):
                plan(previous, rows)

    @patch.dict(os.environ, {'ZAKAZBILETOV_EMAIL': 'test', 'ZAKAZBILETOV_PASSWORD': 'test'})
    def test_failed_delivery_is_not_acknowledged_and_public_files_have_no_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run(root, lambda *_: [row()])
            before = (root / 'data/tickets/state.json').read_text()
            def failed(_):
                raise RuntimeError('delivery failed')
            with self.assertRaises(RuntimeError):
                run(root, lambda *_: [row(), row('second-private-qr')], failed)
            self.assertEqual((root / 'data/tickets/state.json').read_text(), before)
            delivered = []
            run(root, lambda *_: [row(), row('second-private-qr')], delivered.extend)
            self.assertEqual(len(delivered), 1)
            public = ''.join(p.read_text() for p in (root / 'data/tickets').iterdir())
            self.assertNotIn('private', public)
            self.assertTrue(json.loads((root / 'data/tickets/completed.json').read_text())['alert_sent'])


if __name__ == '__main__':
    unittest.main()
