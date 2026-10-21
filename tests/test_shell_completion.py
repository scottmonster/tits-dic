"""Exercise discovery and completion in real shells with isolated user configuration."""
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='titsdic-completion-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        (self.home / '.hushlogin').touch()
        self.checkout = self.base / 'checkout with spaces $'
        shutil.copytree(ROOT / 'completions', self.checkout / 'completions')
        script = self.checkout / 'scripts/shell_completion.py'
        script.parent.mkdir()
        shutil.copy2(ROOT / 'scripts/shell_completion.py', script)
        self.script = script
        self.env = dict(os.environ, HOME=str(self.home), SHELL='/bin/bash',
                        XDG_CONFIG_HOME=str(self.home / 'config'),
                        XDG_DATA_HOME=str(self.home / 'data'), TERM='xterm')
        for key in ('ZDOTDIR', 'FPATH', 'BASH_COMPLETION_USER_DIR', 'BASH_ENV', 'ENV'):
            self.env.pop(key, None)

    def manage(self, command='install', shell='bash', rc=None, expect=0):
        args = [sys.executable, str(self.script), command, '--shell', shell]
        if rc:
            args += ['--rc', str(rc)]
        result = subprocess.run(args, text=True, capture_output=True, env=self.env, timeout=25)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def bash(self, source, rc=None):
        args = ['bash', *(['--rcfile', str(rc)] if rc else []), '-ic', source]
        result = subprocess.run(args, text=True, capture_output=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def manifest(self):
        return json.loads((self.checkout / '.titsdic-completions.json').read_text())

    def zsh_startup(self, zdotdir):
        # This host sets ZDOTDIR in /etc/zsh/zshenv. Supply a redirect there too,
        # while also supporting hosts that read the ordinary ~/.zshenv.
        import shlex
        for path in (self.home / '.zshenv', self.home / 'config/zsh/.zshenv'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('export ZDOTDIR=' + shlex.quote(str(zdotdir)) + '\n')

    def test_bash_detects_runtime_directory_and_loads_all_commands_without_framework(self):
        rc = self.home / '.bashrc'
        original = 'export BASH_COMPLETION_USER_DIR="$HOME/custom completion root"\n'
        rc.write_text(original)
        profile = self.home / '.bash_login'
        profile.write_text('# custom login setup\n')
        self.manage()
        directory = self.home / 'custom completion root/completions'
        self.assertTrue(all((directory / name).is_file() for name in ('titsdic', 'titsdic-ptt', 'titsdic-htt')))
        self.assertIn('_titsdic_complete', self.bash('complete -p titsdic titsdic-ptt titsdic-htt'))
        # A login profile may never source .bashrc; it gets its own interactive block.
        self.assertIn('titsdic completion', profile.read_text())
        self.assertFalse((self.home / '.bash_profile').exists())
        self.manage()
        self.assertEqual(rc.read_text().count('# >>> titsdic completion'), 1)
        self.manage('uninstall')
        self.assertEqual(rc.read_text(), original)
        self.assertEqual(profile.read_text(), '# custom login setup\n')
        self.assertFalse((directory / 'titsdic').exists())
        self.manage('uninstall')

    def test_custom_bash_rc_symlink_and_completion_values_files_and_options(self):
        real_rc = self.home / 'config dir/bash custom rc'
        real_rc.parent.mkdir()
        original = '# no trailing newline'
        real_rc.write_text(original)
        rc = self.home / 'custom rc'
        rc.symlink_to(real_rc)
        self.manage(rc=rc)
        self.assertFalse((self.home / '.bashrc').exists())
        cases = [
            (['titsdic', 'up'], ['update']),
            (['titsdic', 'update', '--completion-shell', 'z'], ['zsh']),
            (['titsdic-ptt', 'serve', '--output-mode', 'sent'], ['sentences']),
            (['titsdic-htt', '--release', 'a'], ['all', 'any']),
            (['titsdic-ptt', 'conf'], ['confirm', 'config']),
            (['titsdic-ptt', 'config', 'sh'], ['show']),
            (['titsdic-ptt', 'update', '--completion-'], ['--completion-shell', '--completion-rc']),
            (['titsdic-ptt', 'serve', '--output-mode=sent'], ['--output-mode=sentences']),
            (['titsdic-ptt', 'serve', '--output-mode', '=', 'fin'], ['final']),
        ]
        import shlex
        for words, expected in cases:
            with self.subTest(words=words):
                code = ('COMP_WORDS=(' + ' '.join(map(shlex.quote, words)) + '); '
                        f'COMP_CWORD={len(words)-1}; _titsdic_complete; printf "%s\\n" "${{COMPREPLY[@]}}"')
                self.assertEqual(self.bash(code, rc).splitlines(), expected)
        recording = self.home / 'audio with spaces.wav'
        recording.touch()
        code = (f'COMP_WORDS=(titsdic --file {shlex.quote(str(self.home / "audio"))}); '
                'COMP_CWORD=2; _titsdic_complete; printf "%s\\n" "${COMPREPLY[@]}"')
        self.assertEqual(self.bash(code, rc).splitlines(), [str(recording)])
        self.manage('uninstall')
        self.assertTrue(rc.is_symlink())
        self.assertEqual(real_rc.read_text(), original)

    def test_bash_xdg_fallback_and_none_removes_prior_completion(self):
        self.manage()
        self.assertTrue((self.home / 'data/bash-completion/completions/titsdic').exists())
        self.manage(shell='none')
        self.assertFalse((self.home / '.bashrc').exists())
        self.assertFalse((self.checkout / '.titsdic-completions.json').exists())

    def test_profile_stays_valid_for_posix_shell_and_bash_login_loads_completion(self):
        profile = self.home / '.profile'
        profile.write_text('# display manager also reads this\n')
        self.manage()
        posix = subprocess.run(['sh', '-c', '. "$HOME/.profile"'], env=self.env,
                               text=True, capture_output=True, timeout=5)
        self.assertEqual((posix.returncode, posix.stderr), (0, ''))
        result = subprocess.run(['bash', '-lic', 'complete -p titsdic-ptt'], env=self.env,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('_titsdic_complete', result.stdout)

    def test_removal_uses_recorded_paths_after_environment_changes(self):
        self.manage()
        self.env['XDG_DATA_HOME'] = str(self.home / 'other data')
        self.env['BASH_COMPLETION_USER_DIR'] = str(self.home / 'other completions')
        self.manage('uninstall')
        self.assertFalse((self.home / 'data/bash-completion/completions/titsdic').exists())
        self.assertFalse((self.home / '.bashrc').exists())

    def test_auto_detects_calling_interactive_bash_custom_rc_over_login_shell(self):
        import shlex
        custom = self.home / 'session rc'
        custom.write_text('export BASH_COMPLETION_USER_DIR="$HOME/session completions"\n')
        self.env['SHELL'] = '/bin/zsh'
        output = self.bash(
            f'{shlex.quote(sys.executable)} {shlex.quote(str(self.script))} discover; :', custom)
        plan = json.loads(output)[0]
        self.assertEqual(plan['shell'], 'bash')
        self.assertEqual(plan['rc_files'], [str(custom)])
        self.assertEqual(plan['directory'], str(self.home / 'session completions/completions'))

    def test_installer_shell_is_not_mistaken_for_interactive_user_shell(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('completion_under_test', self.script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.invocation(['bash', 'install.sh']), (False, True, None))
        self.assertEqual(module.invocation(['zsh', '-lc', 'bash install.sh']), (False, True, None))
        self.assertEqual(module.invocation(['bash', '--rcfile', '/custom', '-i']), (True, False, '/custom'))
        self.assertEqual(module.invocation(['-zsh']), (False, False, None))

    def test_conflicting_or_changed_files_are_preserved(self):
        directory = self.home / 'data/bash-completion/completions'
        directory.mkdir(parents=True)
        conflict = directory / 'titsdic'
        conflict.write_text('# another completion\n')
        result = self.manage(expect=1)
        self.assertIn('untracked', result.stderr)
        self.assertEqual(conflict.read_text(), '# another completion\n')
        self.assertFalse((self.home / '.bashrc').exists())
        conflict.unlink()
        self.manage()
        conflict.write_text('# edited completion\n')
        self.assertIn('was changed', self.manage('uninstall', expect=1).stderr)
        self.assertTrue((self.home / '.bashrc').exists())
        self.assertEqual(conflict.read_text(), '# edited completion\n')

    def test_failed_discovery_does_not_install_to_assumed_defaults(self):
        (self.home / '.bashrc').write_text('echo private-startup-output\nexit 23\n')
        result = self.manage(expect=1)
        self.assertIn('exit 23', result.stderr)
        self.assertNotIn('private-startup-output', result.stdout + result.stderr)
        self.assertFalse((self.home / 'data/bash-completion').exists())
        self.assertFalse((self.checkout / '.titsdic-completions.json').exists())

    def test_changed_rc_block_is_preserved(self):
        self.manage()
        rc = self.home / '.bashrc'
        rc.write_text(rc.read_text().replace('    case ', '    # edited case '))
        self.assertIn('block was changed', self.manage('uninstall', expect=1).stderr)
        self.assertIn('    # edited case ', rc.read_text())

    @unittest.skipUnless(shutil.which('zsh'), 'Zsh is unavailable')
    def test_zsh_detects_zdotdir_and_existing_fpath_and_registers_after_compinit(self):
        zdotdir = self.home / 'zsh config'
        zdotdir.mkdir()
        functions = self.home / 'my functions'
        self.zsh_startup(zdotdir)
        original = ('fpath=("$HOME/my functions" $fpath)\n'
                    'autoload -Uz compinit; compinit\n')
        rc = zdotdir / '.zshrc'
        rc.write_text(original)
        plan = json.loads(self.manage('discover', 'zsh').stdout)[0]
        self.assertEqual(plan['directory'], str(functions))
        self.assertEqual(plan['rc_files'], [str(rc)])
        self.manage(shell='zsh')
        result = subprocess.run(['zsh', '-ic', 'print -r -- ${_comps[titsdic]} ${_comps[titsdic-ptt]} ${_comps[titsdic-htt]}'],
                                text=True, capture_output=True, env=self.env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '_titsdic _titsdic _titsdic')
        self.manage(shell='zsh')
        self.assertEqual(rc.read_text().count('# >>> titsdic completion'), 1)
        self.manage('uninstall')
        self.assertEqual(rc.read_text(), original)
        self.assertFalse((functions / '_titsdic').exists())
        self.assertFalse((self.home / '.zshrc').exists())

    @unittest.skipUnless(shutil.which('zsh'), 'Zsh is unavailable')
    def test_both_shells_and_zsh_fallback_avoid_plugin_fpath(self):
        zdotdir = self.home / 'zsh'
        zdotdir.mkdir()
        plugin = self.home / 'plugins/example/Completions'
        plugin.mkdir(parents=True)
        self.env['ZDOTDIR'] = str(zdotdir)
        self.zsh_startup(zdotdir)
        (zdotdir / '.zshrc').write_text('fpath=("$HOME/plugins/example/Completions" $fpath)\n')
        self.manage(shell='both')
        self.assertEqual(self.manifest()['shells'], ['bash', 'zsh'])
        self.assertTrue((zdotdir / 'completions/_titsdic').exists())
        self.assertFalse((plugin / '_titsdic').exists())
        self.manage('uninstall')
        self.assertFalse((self.home / '.bashrc').exists())
        self.assertFalse((zdotdir / 'completions/_titsdic').exists())

    @unittest.skipUnless(shutil.which('zsh'), 'Zsh is unavailable')
    def test_zsh_tab_completion_in_fresh_interactive_terminal(self):
        self.env['ZDOTDIR'] = str(self.home)
        self.zsh_startup(self.home)
        (self.home / '.zshrc').write_text(
            "PROMPT='READY> '\nRPROMPT=''\n"
            "_capture() { print -r -- \"CAPTURE:$BUFFER\"; BUFFER=''; zle reset-prompt; }\n"
            "zle -N _capture; bindkey '^X' _capture\n")
        self.manage(shell='zsh')
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(shutil.which('zsh'), ['zsh', '-d', '-i'], self.env)
        def read_until(marker):
            output = b''
            deadline = time.monotonic() + 8
            while marker not in output and time.monotonic() < deadline:
                readable, _, _ = select.select([fd], [], [], .1)
                if readable:
                    output += os.read(fd, 65536)
            self.assertIn(marker, output, output.decode(errors='replace'))
            return output
        try:
            read_until(b'READY> ')
            for text, expected in [
                (b'titsdic-ptt tog\t', b'CAPTURE:titsdic-ptt toggle '),
                (b'titsdic-ptt serve --output-mode sent\t', b'CAPTURE:titsdic-ptt serve --output-mode sentences '),
                (b'titsdic-htt --release an\t', b'CAPTURE:titsdic-htt --release any '),
                (b'titsdic update --completion-shell zs\t', b'CAPTURE:titsdic update --completion-shell zsh '),
            ]:
                os.write(fd, text + b'\x18')
                read_until(expected)
        finally:
            os.kill(pid, signal.SIGKILL)
            os.close(fd)
            os.waitpid(pid, 0)


if __name__ == '__main__':
    unittest.main()
