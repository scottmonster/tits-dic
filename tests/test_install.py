"""Installer integration tests: private home, fake services/runtime, no sudo or hardware."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

FAKE_TOOL = r'''
import json, os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['INSTALL_TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\n')
state = Path(os.environ['INSTALL_TEST_STATE'])
if name == 'uv':
    if args[0] == 'venv':
        directory = Path(args[-1]) / 'bin'
        directory.mkdir(parents=True)
        (directory / 'python').symlink_to(os.environ['INSTALL_TEST_PYTHON'])
elif name == 'systemctl':
    if 'is-active' in args:
        sys.exit(0 if state.exists() else 3)
    if 'restart' in args:
        state.write_text('idle')
elif name in ('sudo', 'curl'):
    sys.exit('Unexpected system/network mutation in installer test')
elif name == 'python':
    if args[0] == '-':
        source = sys.stdin.read()
        if 'PhononModel' in source:
            if os.environ.get('INSTALL_TEST_FAIL_MODEL'):
                sys.exit('Model download failed')
        else:
            sys.argv = ['-', *args[1:]]
            exec(compile(source, '<installer>', 'exec'))
    elif args[0] == '-c':
        os.execv(sys.executable, [sys.executable, *args])
    else:
        action = args[2]
        if action == 'status':
            if not state.exists():
                sys.exit(1)
            print(json.dumps({'ok': True, 'state': state.read_text()}))
        elif action == 'shutdown':
            state.unlink(missing_ok=True)
        else:
            sys.exit('Unexpected daemon action: ' + action)
'''


@unittest.skipIf(os.geteuid() == 0, 'Installer intentionally refuses root')
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='titsdic-install-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.home = self.directory / 'home'
        self.home.mkdir()
        self.checkout = self.directory / 'checkout with spaces % $'
        self.checkout.mkdir()
        for file in ('install.sh', 'requirements-dictation.txt', '.gitignore', 'bin/ptt.sh', 'bin/dictate'):
            target = self.checkout / file
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / file, target)
        (self.checkout / 'dictation.py').touch()
        self.fake_bin = self.directory / 'fake-bin'
        self.fake_bin.mkdir()
        fake = self.fake_bin / 'fake-tool'
        fake.write_text(f'#!{sys.executable}\n' + FAKE_TOOL)
        fake.chmod(0o755)
        for name in ('uv', 'systemctl', 'sudo', 'curl', 'python', 'ffmpeg', 'ydotool', 'ydotoold'):
            (self.fake_bin / name).symlink_to(fake)
        self.log = self.directory / 'calls.jsonl'
        self.state = self.directory / 'state'
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / '.config'),
                        XDG_DATA_HOME=str(self.home / '.local/share'),
                        PATH=f'{self.fake_bin}:/usr/bin:/bin',
                        INSTALL_TEST_LOG=str(self.log),
                        INSTALL_TEST_STATE=str(self.state),
                        INSTALL_TEST_PYTHON=str(self.fake_bin / 'python'))
        self.env.pop('TITSDIC_PYTHON', None)

    def install(self, *options, streamed=False, expect=0):
        command = ['bash', '-s', '--'] if streamed else ['bash', str(self.checkout / 'install.sh')]
        result = subprocess.run([*command, '--skip-system-packages', '--skip-typing-setup', *options],
                                input=(ROOT / 'install.sh').read_text() if streamed else '',
                                text=True, capture_output=True, env=self.env, cwd=self.directory,
                                timeout=20)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_local_install_starts_service_and_rerun_preserves_dropins(self):
        self.install()
        unit_dir = self.home / '.config/systemd/user'
        unit = (unit_dir / 'titsdic.service').read_text()
        self.assertIn('Restart=always', unit)
        self.assertIn('WantedBy=default.target', unit)
        self.assertIn('checkout with spaces %% $', unit)
        self.assertIn(['systemctl', '--user', 'enable', 'titsdic.service'], self.calls())
        self.assertIn(['systemctl', '--user', 'restart', 'titsdic.service'], self.calls())
        override = unit_dir / 'titsdic.service.d/override.conf'
        override.parent.mkdir()
        override.write_text('[Service]\nEnvironment=CUSTOM=value\n')
        self.install()
        self.assertEqual(override.read_text(), '[Service]\nEnvironment=CUSTOM=value\n')
        result = subprocess.run([self.home / '.local/bin/titsdic-ptt', 'status'],
                                capture_output=True, text=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'idle')
        self.assertFalse((self.home / '.config/autostart').exists())

    def test_no_start_enables_autostart_without_restarting(self):
        self.install('--no-start')
        self.assertIn(['systemctl', '--user', 'enable', 'titsdic.service'], self.calls())
        self.assertFalse(any('restart' in call for call in self.calls()))
        self.assertFalse(self.state.exists())

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze is unavailable')
    def test_generated_unit_passes_systemd_verification(self):
        check = subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True)
        if check.returncode:
            self.skipTest('No systemd user session for unit verification')
        self.install('--no-start')
        unit = self.home / '.config/systemd/user/titsdic.service'
        result = subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_model_failure_does_not_enable_service_or_report_success(self):
        self.env['INSTALL_TEST_FAIL_MODEL'] = '1'
        result = self.install(expect=1)
        self.assertIn('Model download failed', result.stderr)
        self.assertNotIn('Autostart enabled', result.stdout)
        self.assertFalse((self.home / '.config/systemd/user/titsdic.service').exists())
        self.assertFalse(any('enable' in call for call in self.calls()))

    def test_busy_daemon_prevents_dependency_changes(self):
        self.install('--no-start')
        self.state.write_text('recording')
        self.log.write_text('')
        result = self.install(expect=1)
        self.assertIn('Dictation is busy', result.stderr)
        self.assertFalse(any(call[0] == 'uv' for call in self.calls()))
        self.assertEqual(self.state.read_text(), 'recording')

    def test_streamed_install_requires_repository(self):
        result = self.install(streamed=True, expect=1)
        self.assertIn('pass --repo', result.stderr)

    def test_streamed_clone_uses_default_branch_and_refuses_dirty_update(self):
        def git(*args):
            return subprocess.run(['git', '-C', str(self.checkout), *args], check=True,
                                  text=True, capture_output=True, env=self.env)
        git('init', '-b', 'master')
        git('add', '.')
        git('-c', 'user.name=Installer Test', '-c', 'user.email=installer@example.invalid',
            'commit', '-m', 'Fixture')
        options = ('--repo', str(self.checkout), '--no-start')
        self.install(*options, streamed=True)
        destination = self.home / '.local/share/titsdic'
        self.assertTrue((destination / '.venv-phonon/bin/python').exists())
        self.install(*options, streamed=True)
        (destination / 'dictation.py').write_text('# local change\n')
        result = self.install(*options, streamed=True, expect=1)
        self.assertIn('local changes', result.stderr)
        self.assertEqual((destination / 'dictation.py').read_text(), '# local change\n')


if __name__ == '__main__':
    unittest.main()
