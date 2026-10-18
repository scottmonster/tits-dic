"""Publication safeguards exercised only in temporary local repositories."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CHECKER = Path(__file__).resolve().parents[1] / 'scripts/check-publication.py'


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='titsdic-publication-test-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git('init', '-b', 'master')
        (self.repo / '.gitignore').write_text('.env\n*.log\n/data/\n')
        (self.repo / 'app.py').write_text('print("example")\n')
        self.git('add', '.')
        self.commit()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, capture_output=True,
                              text=True, check=True)

    def commit(self):
        return self.git('-c', 'user.name=Publication Test',
                        '-c', 'user.email=publication@example.invalid',
                        'commit', '-m', 'Fixture')

    def check(self, *args, expected=0, data=None):
        result = subprocess.run([sys.executable, str(CHECKER), *args], cwd=self.repo,
                                capture_output=True, text=True, input=data, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_clean_staged_snapshot_and_history_pass(self):
        self.check('--staged')
        self.check('HEAD')

    def test_removed_private_file_is_still_caught_in_history(self):
        (self.repo / '.env').write_text('LOCAL_SETTING=example\n')
        self.git('add', '-f', '.env')
        self.check('--staged', expected=1)
        self.commit()
        self.git('rm', '.env')
        self.commit()
        self.check('--staged')
        result = self.check('HEAD', expected=1)
        self.assertIn('excluded/private path: .env', result.stderr)

    def test_oversized_file_is_rejected(self):
        with (self.repo / 'oversized.dat').open('wb') as stream:
            stream.truncate(10 * 1024 * 1024 + 1)
        self.git('add', 'oversized.dat')
        result = self.check('--staged', expected=1)
        self.assertIn('file over 10 MiB: oversized.dat', result.stderr)

    def test_secret_marker_is_rejected_without_printing_value(self):
        fake_token = 'gh' + 'p_' + 'A' * 30
        (self.repo / 'settings.txt').write_text(fake_token)
        self.git('add', 'settings.txt')
        result = self.check('--staged', expected=1)
        self.assertIn('GitHub token: settings.txt', result.stderr)
        self.assertNotIn(fake_token, result.stdout + result.stderr)

    def test_pre_push_hook_checks_history_and_blocks_backup_refs(self):
        self.check('--install-hook')
        hook = self.repo / '.git/hooks/pre-push'
        self.assertTrue(os.access(hook, os.X_OK))
        oid = self.git('rev-parse', 'HEAD').stdout.strip()
        zero = '0' * 40
        result = subprocess.run([str(hook)], cwd=self.repo,
                                input=f'refs/heads/master {oid} refs/heads/master {zero}\n',
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.check('--pre-push', expected=1,
                   data=f'refs/heads/local-only/backup {oid} refs/heads/backup {zero}\n')
        result = self.check('--install-hook', expected=1)
        self.assertIn('already exists', result.stderr)


if __name__ == '__main__':
    unittest.main()
