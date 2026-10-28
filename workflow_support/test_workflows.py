"""Guard the cross-workflow locks and retryable persistence contract."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_task_locks_cover_collection_and_save_without_blocking_other_tasks(self):
        groups = []
        for name in ('daily', 'seats', 'calendar-history', 'fx'):
            text = (ROOT / f'.github/workflows/{name}.yml').read_text()
            group = re.search(r'^concurrency:\n  group: (.+)', text, re.M).group(1)
            groups.append(group)
            self.assertIn('  queue: max', text)
            self.assertIn('  cancel-in-progress: false', text)
            self.assertNotIn('git push', text)
            self.assertIn('uses: ./.github/workflows/save-monitor-results.yml', text)
            # The name must come from the original collection even when only
            # its writer job is rerun and github.run_attempt has advanced.
            self.assertRegex(text, r'artifact: \$\{\{ needs\.\w+\.outputs\.artifact \}\}')
        self.assertEqual(len(set(groups)), 4)
        writer = (ROOT / '.github/workflows/save-monitor-results.yml').read_text()
        self.assertIn('group: monitor-results-write', writer)
        self.assertNotIn('monitor-results-write', groups)
        self.assertIn('queue: max', writer)

    def test_backups_dispatch_instead_of_bypassing_task_locks(self):
        daily = (ROOT / '.github/workflows/daily.yml').read_text()
        self.assertNotIn('python -m seats.monitor', daily)
        self.assertNotIn('python -m calendar_history.monitor', daily)
        backup = (ROOT / '.github/workflows/backup-monitoring.yml').read_text()
        self.assertIn('python -m workflow_support.dispatch', backup)
        self.assertNotIn('TELEGRAM_BOT_TOKEN', backup)


if __name__ == '__main__':
    unittest.main()
