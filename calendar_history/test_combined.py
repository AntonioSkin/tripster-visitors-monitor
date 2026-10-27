import unittest
from calendar_history.combined import merge_rows, report_text, GROUPS
from calendar_history.monitor import build_rows


def history(before, after):
    return build_rows([
        {'date_from': day, 'slots': [
            {'key': '2026-10-29T' + time, 'available': value}
            for time, value in slots]}
        for day, slots in [('2026-10-27', before), ('2026-10-28', after)]
    ])


class JointTests(unittest.TestCase):
    def test_closed_time_and_guide_totals(self):
        yulia = merge_rows([history([('08:00',12),('09:00',12)], [('08:00',7)])], '2026-10-28')
        dmitry = merge_rows([history([('08:00',12)], [('08:00',9)])], '2026-10-28')
        text = report_text({'yulia': yulia, 'dmitry': dmitry})
        self.assertIn('Юлия — продано 5 мест', text)
        self.assertIn('Дмитрий — продано 3 места', text)
        self.assertIn('Всего — продано 8 мест', text)
        self.assertEqual(text.count('29.10.2026:'), 1)

    def test_closure_only_and_new_time(self):
        rows = history([('08:00',12),('09:00',12)], [('08:00',12),('10:00',12)])
        self.assertEqual(merge_rows([rows], '2026-10-28')[0]['daily'], 0)

    def test_initial_does_not_invent_total(self):
        rows = merge_rows([history([('08:00',12)], [('08:00',10)])], '2026-10-28')
        text = report_text({'yulia': rows, 'dmitry': []})
        self.assertIn('Дмитрий — нет сравнения', text)
        self.assertNotIn('Всего —', text)

    def test_configuration(self):
        self.assertEqual([t['id'] for t in GROUPS[1]['tours']], [57434,85543,55575,54971])
