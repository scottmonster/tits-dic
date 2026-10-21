#!/usr/bin/env python3
"""Discover, install, and remove Bash/Zsh completion without assuming dotfile locations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / '.titsdic-completions.json'
COMMANDS = ('titsdic', 'titsdic-ptt', 'titsdic-htt')
MARKER = '\x1etitsdic\0'
BLOCK_ID = hashlib.sha256(os.fsencode(ROOT)).hexdigest()[:16]
BEGIN = f'# >>> titsdic completion {BLOCK_ID} >>>'
END = f'# <<< titsdic completion {BLOCK_ID} <<<'


def absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or any(char in str(path) for char in '\n\r\0'):
        raise ValueError(f'Expected an absolute path without line breaks: {path}')
    return path


def invocation(args):
    """Distinguish an interactive shell from `bash install.sh` on the same tty."""
    explicit, scripted, rc = False, False, None
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in ('--rcfile', '--init-file', '-o', '+o'):
            if index + 1 < len(args):
                if arg.startswith('--'):
                    rc = args[index + 1]
                index += 2
                continue
        if arg == '--':
            scripted |= index + 1 < len(args)
            break
        if arg.startswith('--'):
            index += 1
            continue
        if arg.startswith('-') and arg != '-':
            explicit |= 'i' in arg[1:]
            if 'c' in arg[1:]:
                scripted = True
                break
        else:
            scripted = True
            break
        index += 1
    return explicit, scripted, rc


def shell_context():
    """Find the interactive caller, including Bash's per-session --rcfile."""
    pid = os.getppid()
    for _ in range(32):
        try:
            proc = Path(f'/proc/{pid}')
            args = [os.fsdecode(arg) for arg in (proc / 'cmdline').read_bytes().split(b'\0') if arg]
            name = Path(args[0]).name.lstrip('-') if args else ''
            explicit, scripted, rc = invocation(args)
            interactive = explicit
            try:
                terminal = os.readlink(proc / 'fd/0').startswith(('/dev/pts/', '/dev/tty'))
                interactive |= terminal and not scripted
            except OSError:
                pass
            if name in ('bash', 'zsh') and interactive:
                if name == 'bash' and rc:
                    rc = Path(rc).expanduser()
                    if not rc.is_absolute():
                        rc = Path(os.readlink(proc / 'cwd')) / rc
                return name, rc if name == 'bash' else None
            pid = int(next(line.split()[1] for line in (proc / 'status').read_text().splitlines()
                           if line.startswith('PPid:')))
            if pid <= 1:
                break
        except (OSError, ValueError, StopIteration):
            break
    shell = Path(os.environ.get('SHELL') or pwd.getpwuid(os.getuid()).pw_shell).name
    return shell, None


def probe(shell, rc=None):
    executable = shutil.which(shell)
    if not executable:
        raise ValueError(f'{shell} is unavailable; install it or use --completion-shell none.')
    if shell == 'zsh':
        script = ('printf "\\036titsdic\\0%s\\0%s\\0" "${ZDOTDIR:-$HOME}" '
                  '"${XDG_DATA_HOME:-$HOME/.local/share}"; printf "%s\\0" "${fpath[@]}"')
        command = [executable, '-lic', script]
    else:
        script = ('printf "\\036titsdic\\0%s\\0" '
                  '"${BASH_COMPLETION_USER_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion}"')
        command = [executable, *(['--rcfile', str(rc)] if rc else []), '-ic', script]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'{shell} startup timed out while detecting completion settings.') from exc
    if result.returncode or MARKER not in result.stdout:
        # Startup files can print arbitrary/private output; never echo them here.
        raise ValueError(f'Could not inspect interactive {shell} configuration (exit {result.returncode}). '
                         'Fix shell startup or use --completion-shell none.')
    return result.stdout.rsplit(MARKER, 1)[1].split('\0')[:-1]


def discover(selection='auto', rc=None):
    current, custom_bash_rc = shell_context()
    if selection == 'auto':
        if current not in ('bash', 'zsh'):
            raise ValueError(f'Automatic completion supports Bash/Zsh; detected {current}. '
                             'Choose --completion-shell bash, zsh, both, or none.')
        shells = [current]
    else:
        shells = ['bash', 'zsh'] if selection == 'both' else ([] if selection == 'none' else [selection])
    if rc and len(shells) != 1:
        raise ValueError('--completion-rc requires exactly one shell.')
    plans = []
    for shell in shells:
        bash_rc = absolute(rc) if rc and shell == 'bash' else custom_bash_rc
        values = probe(shell, bash_rc)
        if shell == 'bash':
            directory = absolute(values[0]) / 'completions'
            rc_files = [bash_rc or Path.home() / '.bashrc']
            if not bash_rc:
                # Bash login shells don't necessarily read .bashrc.
                profiles = [Path.home() / name for name in ('.bash_profile', '.bash_login', '.profile')]
                rc_files.append(next((path for path in profiles if path.exists()), profiles[0]))
            # .profile may also be read by a POSIX login shell/display manager.
            content = (f'if [ -n "${{BASH_VERSION:-}}" ] && [ -r {shlex.quote(str(directory / "titsdic"))} ]; then\n'
                       f'    case $- in *i*) . {shlex.quote(str(directory / "titsdic"))} ;; esac\nfi\n')
            files = [(directory / name, ROOT / 'completions/bash/titsdic') for name in COMMANDS]
        else:
            zdotdir, _data_home, *fpath = values
            directory = absolute(zdotdir) / 'completions'
            for entry in fpath:
                if not Path(entry).is_absolute():
                    continue  # Relative fpath entries depend on the current directory.
                candidate = absolute(entry)
                # Avoid managed framework/package trees even when user-writable.
                excluded = ('plugins', 'oh-my-zsh', '.oh-my-zsh', '.zprezto', '.zim',
                            '.linuxbrew', 'Cellar', 'node_modules')
                if any(part in excluded for part in candidate.parts):
                    continue
                parent = candidate
                while not parent.exists() and parent != parent.parent:
                    parent = parent.parent
                if (parent.is_dir() and parent.stat().st_uid == os.getuid()
                        and os.access(parent, os.W_OK)):
                    directory = candidate
                    break
            rc_files = [absolute(rc) if rc else absolute(zdotdir) / '.zshrc']
            quoted = shlex.quote(str(directory))
            # Register after existing frameworks/compinit too; no stale zcompdump
            # can hide newly installed commands. compinit is only needed if absent.
            content = (f'fpath=({quoted} ${{fpath:#' + quoted + '})\n'
                       'autoload -Uz compinit\n'
                       'if (( ! $+functions[compdef] )); then compinit; fi\n'
                       'autoload -Uz _titsdic\n'
                       'compdef _titsdic titsdic titsdic-ptt titsdic-htt\n')
            files = [(directory / '_titsdic', ROOT / 'completions/zsh/_titsdic')]
        plans.append({'shell': shell, 'directory': str(directory),
                      'rc_files': [str(absolute(path).resolve()) for path in rc_files],
                      'content': content, 'files': [(str(a), str(b)) for a, b in files]})
    return plans


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest():
    if not MANIFEST.exists():
        return {'version': 1, 'root': str(ROOT), 'files': [], 'blocks': []}
    data = json.loads(MANIFEST.read_text())
    if data.get('root') != str(ROOT) or data.get('version') != 1:
        raise ValueError(f'Completion record belongs to another installation: {MANIFEST}')
    return data


def check_removal(data=None):
    data = read_manifest() if data is None else data
    for record in data['files']:
        path = Path(record['path'])
        if path.is_symlink() or (path.exists() and (not path.is_file() or digest(path) != record['sha256'])):
            raise ValueError(f'Completion file was changed; leaving it in place: {path}')
    for record in data['blocks']:
        path = Path(record['path'])
        text = path.read_text() if path.exists() else ''
        if (BEGIN in text or END in text) and record['text'] not in text:
            raise ValueError(f'Completion block was changed; leaving it in place: {path}')
    return data


def remove(data=None):
    data = check_removal(data)
    for record in data['blocks']:
        path = Path(record['path'])
        if path.exists():
            text = path.read_text()
            if record['text'] in text:
                remaining = text.replace(record['text'], '', 1)
                if not remaining and record.get('created'):
                    path.unlink()
                else:
                    path.write_text(remaining)
    for record in data['files']:
        Path(record['path']).unlink(missing_ok=True)
    MANIFEST.unlink(missing_ok=True)


def prepare(selection='auto', rc=None):
    plans = discover(selection, rc)
    old = check_removal()
    owned = {record['path'] for record in old['files']}
    for plan in plans:
        for destination, source in plan['files']:
            path = Path(destination)
            if (path.exists() or path.is_symlink()) and destination not in owned:
                raise ValueError(f'Refusing to replace an untracked completion: {path}')
            if not Path(source).is_file():
                raise ValueError(f'Completion source is missing: {source}')
        for rc_file in plan['rc_files']:
            path = Path(rc_file)
            if path.exists() and not path.is_file():
                raise ValueError(f'Shell configuration is not a regular file: {path}')
    return plans, old


def install(selection='auto', rc=None):
    plans, old = prepare(selection, rc)
    remove(old)
    data = {'version': 1, 'root': str(ROOT), 'files': [], 'blocks': [],
            'shells': [plan['shell'] for plan in plans]}
    # Record operations before writing, so interrupted installs can be uninstalled.
    def save():
        temporary = MANIFEST.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, indent=2) + '\n')
        temporary.replace(MANIFEST)
    for plan in plans:
        for destination, source in plan['files']:
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            data['files'].append({'path': destination, 'sha256': digest(Path(source))})
            save()
            path.write_bytes(Path(source).read_bytes())
        for rc_file in dict.fromkeys(plan['rc_files']):
            path = Path(rc_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            created = not path.exists()
            before = path.read_text() if not created else ''
            block = ('\n' if before and not before.endswith('\n') else '') + BEGIN + '\n' + plan['content'] + END + '\n'
            data['blocks'].append({'path': rc_file, 'text': block, 'created': created})
            save()
            path.write_text(before + block)
        print(f'{plan["shell"].capitalize()} completion: {plan["directory"]}')
        print('Shell configuration: ' + ', '.join(plan['rc_files']))
    if plans:
        print('Open a new shell to load completion.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'discover', 'check-install', 'check', 'uninstall'))
    parser.add_argument('--shell', choices=('auto', 'bash', 'zsh', 'both', 'none'), default='auto')
    parser.add_argument('--rc', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'install':
            install(args.shell, args.rc)
        elif args.command == 'discover':
            print(json.dumps(discover(args.shell, args.rc), indent=2))
        elif args.command == 'check':
            check_removal()
        elif args.command == 'check-install':
            prepare(args.shell, args.rc)
        else:
            remove()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Completion {args.command} failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
