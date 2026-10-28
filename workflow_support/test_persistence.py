import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow_support.persistence import DataConflict, apply_bundle, export_bundle, git, read_bundle


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / 'origin.git'
        git(self.root, 'init', '--bare', '--initial-branch=main', str(self.origin))
        self.seed = self.root / 'seed'
        self.seed.mkdir()
        git(self.seed, 'init', '--initial-branch=main')
        self.configure(self.seed)
        self.write(self.seed, 'data/seats/state.json', 'old seats')
        self.write(self.seed, 'data/fx/state.json', 'old rate')
        git(self.seed, 'add', '.')
        git(self.seed, 'commit', '-m', 'seed')
        git(self.seed, 'remote', 'add', 'origin', str(self.origin))
        git(self.seed, 'push', 'origin', 'main')
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}).start()

    def configure(self, directory):
        git(directory, 'config', 'user.name', 'Test')
        git(directory, 'config', 'user.email', 'test@example.invalid')

    def clone(self, name):
        directory = self.root / name
        git(self.root, 'clone', '--quiet', str(self.origin), str(directory))
        self.configure(directory)
        return directory

    def write(self, root, name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def bundle(self, collector, task, name):
        output = self.root / name
        count = export_bundle(collector, task, output, '123')
        self.assertGreater(count, 0)
        return output

    def test_two_collectors_from_same_head_preserve_both_results(self):
        seats, fx = self.clone('seats'), self.clone('fx')
        self.write(seats, 'data/seats/state.json', 'new seats')
        self.write(fx, 'data/fx/state.json', 'new rate')
        first, second = self.bundle(seats, '2', 'bundle2'), self.bundle(fx, '4', 'bundle4')
        apply_bundle(self.clone('writer2'), '2', first, '123')
        apply_bundle(self.clone('writer4'), '4', second, '123')
        final = self.clone('final')
        self.assertEqual((final / 'data/seats/state.json').read_text(), 'new seats')
        self.assertEqual((final / 'data/fx/state.json').read_text(), 'new rate')

    def test_retry_after_another_commit_keeps_its_changes(self):
        collector, writer, editor = self.clone('collector'), self.clone('writer'), self.clone('editor')
        self.write(collector, 'data/seats/state.json', 'seat observation')
        package = self.bundle(collector, '2', 'bundle')
        original_git, intercepted = git, []

        def race(directory, *args, **kwargs):
            if args[0] == 'push' and not intercepted:
                intercepted.append(True)
                self.write(editor, 'data/fx/state.json', 'concurrent quote')
                original_git(editor, 'add', '.')
                original_git(editor, 'commit', '-m', 'another task')
                original_git(editor, 'push', 'origin', 'main')
            return original_git(directory, *args, **kwargs)

        with patch('workflow_support.persistence.git', side_effect=race), patch('workflow_support.persistence.time.sleep'):
            apply_bundle(writer, '2', package, '123')
        final = self.clone('final')
        self.assertEqual((final / 'data/seats/state.json').read_text(), 'seat observation')
        self.assertEqual((final / 'data/fx/state.json').read_text(), 'concurrent quote')

    def test_repeating_writer_does_not_create_commit_or_resend(self):
        collector = self.clone('collector')
        self.write(collector, 'data/seats/state.json', 'new seats')
        package = self.bundle(collector, '2', 'bundle')
        writer = self.clone('writer')
        apply_bundle(writer, '2', package, '123')
        before = git(writer, 'rev-parse', 'HEAD').stdout
        self.assertEqual(apply_bundle(writer, '2', package, '123'), 0)
        self.assertEqual(git(writer, 'rev-parse', 'HEAD').stdout, before)

    def test_newer_same_task_data_is_never_overwritten(self):
        collector, editor = self.clone('collector'), self.clone('editor')
        self.write(collector, 'data/seats/state.json', 'older pending observation')
        package = self.bundle(collector, '2', 'bundle')
        self.write(editor, 'data/seats/state.json', 'newer observation')
        git(editor, 'add', '.')
        git(editor, 'commit', '-m', 'newer')
        git(editor, 'push', 'origin', 'main')
        with self.assertRaises(DataConflict):
            apply_bundle(self.clone('writer'), '2', package, '123')
        self.assertEqual((self.clone('final') / 'data/seats/state.json').read_text(), 'newer observation')

    def test_export_never_includes_another_task_or_code(self):
        collector = self.clone('collector')
        self.write(collector, 'data/seats/state.json', 'new seats')
        self.write(collector, 'data/fx/state.json', 'unrelated rate')
        self.write(collector, 'secrets.env', 'private')
        package = self.bundle(collector, '2', 'bundle')
        records = read_bundle(package, '2', '123')
        self.assertEqual([r[0] for r in records], ['data/seats/state.json'])
        self.assertFalse((package / 'files/secrets.env').exists())
        with self.assertRaises(ValueError):
            read_bundle(package, '4', '123')
        with self.assertRaises(ValueError):
            read_bundle(package, '2', '456')

    def test_tampered_file_is_not_saved(self):
        collector = self.clone('collector')
        self.write(collector, 'data/seats/state.json', 'new seats')
        package = self.bundle(collector, '2', 'bundle')
        (package / 'files/data/seats/state.json').write_text('corrupt')
        with self.assertRaises(ValueError):
            apply_bundle(self.clone('writer'), '2', package, '123')

    def test_empty_collection_has_no_bundle(self):
        package = self.root / 'empty'
        self.assertEqual(export_bundle(self.clone('collector'), '2', package, '123'), 0)
        self.assertFalse(package.exists())

    def test_reset_guard_protects_a_normal_working_directory(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            with self.assertRaises(RuntimeError):
                apply_bundle(self.seed, '2', self.root / 'absent', '123')


if __name__ == '__main__':
    unittest.main()
