"""Installer integration tests: private home, fake services/runtime, no sudo or hardware."""
import json
import os
from pathlib import Path
import shutil
import shlex
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
    if 'disable' in args:
        state.unlink(missing_ok=True)
elif name == 'dpkg-query':
    if args[-1] in os.environ.get('INSTALL_TEST_MISSING_PACKAGES', '').split():
        sys.exit(1)
    print('installed', end='')
elif name == 'sudo':
    if not os.environ.get('INSTALL_TEST_ALLOW_APT') or args[0] != 'apt-get':
        sys.exit('Unexpected system mutation in installer test')
    if args[1] == 'update':
        sys.exit(int(os.environ.get('INSTALL_TEST_APT_UPDATE_STATUS', '0')))
elif name == 'curl':
    sys.exit('Unexpected system/network mutation in installer test')
elif name == 'python':
    if Path(args[0]).name == 'settings.py':
        os.execv(sys.executable, [sys.executable, *args])
    elif args[0] == '-':
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
        elif action in ('toggle', 'start', 'stop'):
            if not state.exists():
                sys.exit('Daemon is unavailable')
            current = state.read_text()
            if action == 'toggle':
                action = 'stop' if current == 'recording' else 'start'
            if action == 'start' and current != 'idle':
                print(json.dumps({'ok': False, 'error': 'busy'}))
                sys.exit(1)
            state.write_text('recording' if action == 'start' else 'idle')
            print(json.dumps({'ok': True, 'state': state.read_text()}))
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
        for file in ('install.sh', 'requirements-dictation.txt', '.gitignore',
                     'bin/tap-to-talk.sh', 'bin/ptt.sh', 'bin/hold-to-talk.sh', 'bin/dictate',
                     'settings.py', 'dictation_control.py', 'scripts/manage-install.py',
                     'scripts/shell_completion.py', 'completions/bash/titsdic',
                     'completions/zsh/_titsdic'):
            target = self.checkout / file
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / file, target)
        (self.checkout / 'dictation.py').touch()
        self.fake_bin = self.directory / 'fake-bin'
        self.fake_bin.mkdir()
        fake = self.fake_bin / 'fake-tool'
        fake.write_text(f'#!{sys.executable}\n' + FAKE_TOOL)
        fake.chmod(0o755)
        for name in ('uv', 'systemctl', 'sudo', 'curl', 'python', 'ffmpeg', 'ydotool', 'ydotoold',
                     'apt-get', 'apt-cache', 'dpkg-query'):
            (self.fake_bin / name).symlink_to(fake)
        self.log = self.directory / 'calls.jsonl'
        self.state = self.directory / 'state'
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / '.config'),
                        XDG_DATA_HOME=str(self.home / '.local/share'),
                        XDG_RUNTIME_DIR=str(self.home / '.runtime'),
                        PATH=f'{self.fake_bin}:/usr/bin:/bin',
                        INSTALL_TEST_LOG=str(self.log),
                        INSTALL_TEST_STATE=str(self.state),
                        INSTALL_TEST_PYTHON=str(self.fake_bin / 'python'))
        self.env.pop('TITSDIC_PYTHON', None)
        self.env['SHELL'] = '/bin/bash'
        for key in ('ZDOTDIR', 'BASH_COMPLETION_USER_DIR', 'FPATH'):
            self.env.pop(key, None)

    def install(self, *options, streamed=False, expect=0, system_packages=False):
        command = ['bash', '-s', '--'] if streamed else ['bash', str(self.checkout / 'install.sh')]
        package_options = [] if system_packages else ['--skip-system-packages']
        result = subprocess.run([*command, *package_options, '--skip-typing-setup', *options],
                                input=(ROOT / 'install.sh').read_text() if streamed else '',
                                text=True, capture_output=True, env=self.env, cwd=self.directory,
                                timeout=20)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def ptt(self, *args, checkout=None, expect=0):
        result = subprocess.run(['bash', str((checkout or self.checkout) / 'bin/tap-to-talk.sh'), *args],
                                capture_output=True, text=True, env=self.env,
                                cwd=self.directory, timeout=20)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.checkout), *args], check=True,
                              text=True, capture_output=True, env=self.env)

    def commit(self, message):
        self.git('add', '.')
        self.git('-c', 'user.name=Installer Test', '-c', 'user.email=installer@example.invalid',
                 'commit', '-m', message)

    def test_installed_dependencies_skip_apt_even_when_repositories_are_broken(self):
        self.env['INSTALL_TEST_APT_UPDATE_STATUS'] = '100'
        result = self.install('--no-start', system_packages=True)
        self.assertIn('already installed; skipping APT', result.stdout)
        self.assertTrue(any(call[0] == 'dpkg-query' for call in self.calls()))
        self.assertFalse(any(call[0] in ('sudo', 'apt-get') for call in self.calls()))

    def test_only_missing_packages_are_installed(self):
        self.env['INSTALL_TEST_MISSING_PACKAGES'] = 'libportaudio2 libsndfile1'
        self.env['INSTALL_TEST_ALLOW_APT'] = '1'
        self.install('--no-start', system_packages=True)
        apt_calls = [call for call in self.calls() if call[0] == 'sudo']
        self.assertEqual(apt_calls, [
            ['sudo', 'apt-get', 'update'],
            ['sudo', 'apt-get', 'install', '-y', 'libportaudio2', 'libsndfile1'],
        ])

    def test_missing_dependencies_and_failed_apt_refresh_stop_installation(self):
        self.env['INSTALL_TEST_MISSING_PACKAGES'] = 'libportaudio2'
        self.env['INSTALL_TEST_ALLOW_APT'] = '1'
        self.env['INSTALL_TEST_APT_UPDATE_STATUS'] = '100'
        result = self.install(system_packages=True, expect=1)
        self.assertIn('libportaudio2', result.stdout)
        self.assertIn('APT refresh failed', result.stderr)
        self.assertFalse(any(call[0] == 'uv' or 'install' in call for call in self.calls()))
        self.assertFalse((self.home / '.config/systemd/user/titsdic.service').exists())

    def test_local_install_starts_service_and_rerun_preserves_dropins(self):
        self.install()
        unit_dir = self.home / '.config/systemd/user'
        unit = (unit_dir / 'titsdic.service').read_text()
        self.assertIn('Restart=always', unit)
        self.assertIn('WantedBy=default.target', unit)
        self.assertIn('checkout with spaces %% $', unit)
        self.assertIn('/bin/tap-to-talk.sh" serve', unit)
        self.assertIn(['systemctl', '--user', 'enable', 'titsdic.service'], self.calls())
        self.assertIn(['systemctl', '--user', 'restart', 'titsdic.service'], self.calls())
        override = unit_dir / 'titsdic.service.d/override.conf'
        override.parent.mkdir()
        override.write_text('[Service]\nEnvironment=CUSTOM=value\n')
        config = self.home / '.config/titsdic/config.toml'
        config.write_text('[ptt]\nbuffer_length = 2\n')
        self.install()
        self.assertEqual(override.read_text(), '[Service]\nEnvironment=CUSTOM=value\n')
        self.assertEqual(config.read_text(), '[ptt]\nbuffer_length = 2\n')
        result = subprocess.run([self.home / '.local/bin/titsdic-ptt', 'status'],
                                capture_output=True, text=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'idle')
        result = subprocess.run([self.home / '.local/bin/titsdic-htt', '--help'],
                                capture_output=True, text=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('release stops capture', result.stdout)
        self.assertFalse((self.home / '.config/autostart').exists())

    def test_shortcut_defaults_to_toggle_and_keeps_explicit_start_stop(self):
        self.install()
        launcher = self.home / '.local/bin/titsdic-ptt'
        for args, expected in [((), 'recording'), ((), 'idle'),
                               (('start',), 'recording'), (('stop',), 'idle'),
                               (('toggle',), 'recording'), ((), 'idle')]:
            with self.subTest(args=args, expected=expected):
                result = subprocess.run([launcher, *args], capture_output=True,
                                        text=True, env=self.env, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['state'], expected)
        # A tap during final processing must not open another recording.
        self.state.write_text('processing')
        self.assertIn('busy', self.ptt(expect=1).stdout)
        self.assertEqual(self.state.read_text(), 'processing')

    def test_old_control_path_still_works_and_uninstalls_legacy_launcher(self):
        self.install()
        launcher = self.home / '.local/bin/titsdic-ptt'
        target = shlex.quote(str(self.checkout / 'bin/ptt.sh'))
        launcher.write_text(f'#!/usr/bin/env bash\nexec {target} "$@"\n')
        result = subprocess.run([launcher, 'status'], capture_output=True,
                                text=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'idle')
        self.ptt('uninstall')
        self.assertFalse(launcher.exists())

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

    def test_streamed_install_defaults_to_official_repository_and_master(self):
        self.git('init', '-b', 'master')
        self.commit('Fixture')
        # The remote HEAD differs: the installer must still select master.
        self.git('checkout', '-b', 'preview')
        official = 'https://github.com/scottmonster/tits-dic.git'
        git_config = self.directory / 'gitconfig'
        subprocess.run(['git', 'config', '--file', str(git_config),
                        f'url.{self.checkout}.insteadOf', official], check=True)
        self.env['GIT_CONFIG_GLOBAL'] = str(git_config)
        self.env['GIT_CONFIG_NOSYSTEM'] = '1'
        self.install('--no-start', streamed=True)
        destination = self.home / '.local/share/titsdic'
        result = subprocess.run(['git', '-C', str(destination), 'branch', '--show-current'],
                                check=True, capture_output=True, text=True, env=self.env)
        self.assertEqual(result.stdout.strip(), 'master')
        result = subprocess.run(['git', '-C', str(destination), 'config', '--get', 'remote.origin.url'],
                                check=True, capture_output=True, text=True, env=self.env)
        self.assertEqual(result.stdout.strip(), official)
        self.install('--no-start', streamed=True)  # Updates the same default checkout.
        self.ptt('uninstall', checkout=destination)
        custom = self.directory / 'custom install'
        self.install('--no-start', '--install-dir', str(custom), streamed=True)
        self.assertTrue((custom / '.venv-phonon/bin/python').exists())

    def test_streamed_install_keeps_repository_and_revision_overrides(self):
        self.git('init', '-b', 'master')
        self.commit('Fixture')
        self.git('branch', 'preview')
        self.install('--repo', str(self.checkout), '--ref', 'preview', '--no-start', streamed=True)
        destination = self.home / '.local/share/titsdic'
        result = subprocess.run(['git', '-C', str(destination), 'branch', '--show-current'],
                                check=True, capture_output=True, text=True, env=self.env)
        self.assertEqual(result.stdout.strip(), 'preview')

    def test_streamed_clone_uses_master_and_refuses_dirty_update(self):
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

    def test_uninstall_removes_service_and_launchers_preserving_user_files(self):
        self.install()
        unit_dir = self.home / '.config/systemd/user'
        override = unit_dir / 'titsdic.service.d/override.conf'
        override.parent.mkdir()
        override.write_text('[Service]\nEnvironment=CUSTOM=value\n')
        self.ptt('uninstall')
        self.assertFalse((unit_dir / 'titsdic.service').exists())
        self.assertFalse((self.home / '.local/bin/titsdic').exists())
        self.assertFalse((self.home / '.local/bin/titsdic-ptt').exists())
        self.assertFalse((self.home / '.local/bin/titsdic-htt').exists())
        self.assertFalse(self.state.exists())
        self.assertTrue(override.exists())
        self.assertTrue((self.home / '.config/titsdic/config.toml').exists())
        self.assertTrue((self.checkout / '.venv-phonon/bin/python').exists())
        self.assertIn(['systemctl', '--user', 'disable', '--now', 'titsdic.service'], self.calls())
        self.assertFalse(any(call[0] == 'sudo' for call in self.calls()))
        self.ptt('uninstall')  # Safe to repeat directly from the preserved checkout.

    def test_uninstall_works_without_the_python_environment(self):
        self.install('--no-start')
        shutil.rmtree(self.checkout / '.venv-phonon')
        self.ptt('uninstall')
        self.assertFalse((self.home / '.config/systemd/user/titsdic.service').exists())

    def test_uninstall_preserves_service_when_completion_was_edited(self):
        self.install()
        completion = self.home / '.local/share/bash-completion/completions/titsdic'
        completion.write_text('# user customization\n')
        self.assertIn('Completion file was changed', self.ptt('uninstall', expect=1).stderr)
        self.assertTrue(self.state.exists())
        self.assertTrue((self.home / '.local/bin/titsdic').exists())
        self.assertEqual(completion.read_text(), '# user customization\n')

    def test_uninstall_refuses_busy_dictation_or_unrelated_launchers(self):
        self.install()
        self.state.write_text('recording')
        self.assertIn('busy', self.ptt('uninstall', expect=1).stderr)
        self.state.write_text('idle')
        launcher = self.home / '.local/bin/titsdic'
        launcher.write_text('#!/bin/sh\necho another program\n')
        self.assertIn('another installation', self.ptt('uninstall', expect=1).stderr)
        self.assertTrue(self.state.exists())
        self.assertTrue((self.home / '.config/systemd/user/titsdic.service').exists())
        self.assertEqual(launcher.read_text(), '#!/bin/sh\necho another program\n')

    def test_uninstall_refuses_a_service_from_another_checkout(self):
        self.install('--no-start')
        unit = self.home / '.config/systemd/user/titsdic.service'
        unit.write_text('[Service]\nWorkingDirectory=/another/checkout\n')
        self.assertIn('another installation', self.ptt('uninstall', expect=1).stderr)
        self.assertTrue(unit.exists())
        self.assertFalse(any('disable' in call for call in self.calls()))

    def test_uninstall_preserves_unrelated_hold_to_talk_launcher(self):
        self.install('--no-start')
        launcher = self.home / '.local/bin/titsdic-htt'
        launcher.write_text('#!/bin/sh\necho another program\n')
        self.assertIn('another installation', self.ptt('uninstall', expect=1).stderr)
        self.assertTrue((self.home / '.config/systemd/user/titsdic.service').exists())
        self.assertEqual(launcher.read_text(), '#!/bin/sh\necho another program\n')

    def test_titsdic_update_is_available_without_dictation_environment(self):
        self.install('--no-start')
        shutil.rmtree(self.checkout / '.venv-phonon')
        result = subprocess.run([self.home / '.local/bin/titsdic', 'update', '--help'],
                                capture_output=True, text=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--skip-system-packages', result.stdout)

    def test_update_fetches_master_and_runs_new_installer_preserving_config(self):
        self.git('init', '-b', 'master')
        self.commit('Fixture')
        self.install('--repo', str(self.checkout), '--no-start', streamed=True)
        destination = self.home / '.local/share/titsdic'
        config = self.home / '.config/titsdic/config.toml'
        config.write_text('[ptt]\nbuffer_length = 3\n')
        installer = self.checkout / 'install.sh'
        installer.write_text(installer.read_text() + "\nprintf 'Updated installer ran.\\n'\n")
        self.commit('New installer')
        completion_rc = self.home / 'bash update rc'
        result = subprocess.run([self.home / '.local/bin/titsdic', 'update',
                                 '--skip-system-packages', '--skip-typing-setup', '--no-start',
                                 '--completion-shell', 'bash', '--completion-rc', str(completion_rc)],
                                capture_output=True, text=True, env=self.env,
                                cwd=self.directory, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Updated installer ran.', result.stdout)
        self.assertEqual((destination / 'install.sh').read_text(), installer.read_text())
        self.assertEqual(config.read_text(), '[ptt]\nbuffer_length = 3\n')
        self.assertIn('titsdic completion', completion_rc.read_text())

    def test_update_refuses_dirty_checkout_and_local_only_branch(self):
        self.git('init', '-b', 'master')
        self.commit('Fixture')
        self.install('--repo', str(self.checkout), '--no-start', streamed=True)
        destination = self.home / '.local/share/titsdic'
        local = destination / 'dictation.py'
        local.write_text('# local change\n')
        self.assertIn('local changes', self.ptt('update', checkout=destination, expect=1).stderr)
        self.assertEqual(local.read_text(), '# local change\n')
        subprocess.run(['git', '-C', str(destination), 'checkout', '-b', 'local-only/private'],
                       check=True, capture_output=True)
        self.assertIn('master branch', self.ptt('update', checkout=destination, expect=1).stderr)


if __name__ == '__main__':
    unittest.main()
