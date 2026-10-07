import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import telegram_report as tg


def snapshot(values):
    return {"date": "2026-10-07", "checked_at": "2026-10-07T16:21:25+05:00",
            "results": [{"id": i, "name": f"Экскурсия {i}", "visitors": v,
                         "status": "ok" if v is not None else "error"}
                        for i, v in values.items()]}


class TelegramTests(unittest.TestCase):
    def test_totals_and_corrections(self):
        text = tg.format_report(snapshot({1: 2121, 2: 10}), snapshot({1: 2120, 2: 12}))
        self.assertIn("Посетили: 2121 · изменение: +1", text)
        self.assertIn("Посетили: 10 · изменение: -2", text)
        self.assertIn("ИТОГО: 2131", text)
        self.assertIn("Изменение суммы: -1", text)

    def test_partial_never_fakes_total(self):
        text = tg.format_report(snapshot({1: 2121, 2: None}), snapshot({1: 2120, 2: 12}))
        self.assertIn("Ошибка сбора", text)
        self.assertIn("ИТОГО: Нет полного замера", text)

    def test_new_tour_invalidates_total_delta(self):
        text = tg.format_report(snapshot({1: 2121, 2: 0}), snapshot({1: 2120}))
        self.assertIn("Изменение суммы: —", text)

    def test_missing_baseline(self):
        self.assertIn("изменение: —", tg.format_report(snapshot({1: 2121}), None))

    def test_transport_payload(self):
        with patch.object(tg, "urlopen", return_value=io.BytesIO(b'{"ok":true}')) as opened:
            tg.send_message("123:token", "456", "Отчёт")
        request = opened.call_args.args[0]
        self.assertEqual(json.loads(request.data), {
            "chat_id": "456", "text": "Отчёт", "link_preview_options": {"is_disabled": True}})

    def test_token_not_in_failure(self):
        token = "123:secret"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        for exc in (HTTPError(url, 403, url, {}, None), OSError(url)):
            with patch.object(tg, "urlopen", side_effect=exc):
                with self.assertRaises(RuntimeError) as caught:
                    tg.send_message(token, "456", "Report")
                self.assertNotIn(token, str(caught.exception))

    def test_missing_configuration_never_sends(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(tg, "send_message") as send:
            self.assertEqual(tg.main(), 0)
            send.assert_not_called()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:token"}, clear=True):
            self.assertEqual(tg.main(), 1)


if __name__ == "__main__":
    unittest.main()
