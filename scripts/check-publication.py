#!/usr/bin/env python3
"""Check staged files or complete branch history without printing secret values."""
import argparse
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys

MAX_BYTES = 10 * 1024 * 1024
SECRET_PATTERNS = {
    'private key': rb'-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----',
    'GitHub token': rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b',
    'API token': rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{25,}\b',
    'AWS access key': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'credential in URL': rb'https?://[^\s/@:]+:[^\s/@]+@',
}


def git(*args, data=None, acceptable=(0,)):
    result = subprocess.run(['git', *args], input=data, capture_output=True)
    if result.returncode not in acceptable:
        raise RuntimeError(result.stderr.decode(errors='replace').strip())
    return result.stdout


def private_path(name):
    path = PurePosixPath(name)
    parts = path.parts
    if any(part in {'.ssh', 'secrets', '.vscode', '.idea', 'ign'} or
           part.endswith(('.ign', '.aign')) for part in parts):
        return True
    if any((part == '.env' or part.startswith('.env.')) and part != '.env.example'
           for part in parts):
        return True
    if parts and parts[0] in {'data', 'hot_mic', 'plan-research', 'typing'}:
        return True
    if parts[:2] == ('tst', 'data') and len(parts) > 2:
        return parts[2] not in {'scripts', 'README.md'}
    if name in {'res/sound-test/start.wav', 'res/sound-test/end.wav'}:
        return False
    return path.suffix.lower() in {
        '.wav', '.flac', '.mp3', '.m4a', '.ogg', '.mp4', '.webm', '.log',
        '.jsonl', '.pem', '.key', '.bin', '.gguf', '.safetensors', '.onnx',
        '.pt', '.pth', '.zip', '.tgz', '.gz', '.tar',
    }


def entries(revision=None, staged=False):
    found = set()
    if staged:
        records = git('ls-files', '--stage', '-z').split(b'\0')
        for record in filter(None, records):
            metadata, name = record.split(b'\t', 1)
            _, oid, stage = metadata.split()
            if stage != b'0':
                raise RuntimeError('Resolve index conflicts before publication.')
            size = int(git('cat-file', '-s', oid.decode()).strip())
            found.add((name.decode(errors='surrogateescape'), oid.decode(), size))
    else:
        commit = git('rev-parse', '--verify', f'{revision}^{{commit}}').decode().strip()
        for ancestor in git('rev-list', commit).decode().splitlines():
            for record in filter(None, git('ls-tree', '-r', '-l', '-z', ancestor).split(b'\0')):
                metadata, name = record.split(b'\t', 1)
                _, kind, oid, size = metadata.split()
                if kind != b'blob':
                    raise RuntimeError('Submodules need a separate publication review.')
                found.add((name.decode(errors='surrogateescape'), oid.decode(), int(size)))
    return sorted(found)


def check(revision=None, staged=False):
    files = entries(revision, staged)
    paths = sorted({name for name, _, _ in files})
    ignored = set()
    if paths:
        raw = b'\0'.join(os.fsencode(name) for name in paths) + b'\0'
        ignored = {os.fsdecode(name) for name in git(
            'check-ignore', '--no-index', '-z', '--stdin', data=raw, acceptable=(0, 1)
        ).split(b'\0') if name}
    errors = set()
    scanned = {}
    for name, oid, size in files:
        if private_path(name) or name in ignored:
            errors.add(f'excluded/private path: {name}')
            continue
        if size > MAX_BYTES:
            errors.add(f'file over 10 MiB: {name}')
            continue
        if oid not in scanned:
            content = git('cat-file', 'blob', oid)
            scanned[oid] = [label for label, pattern in SECRET_PATTERNS.items()
                            if re.search(pattern, content)]
        for label in scanned[oid]:
            errors.add(f'{label}: {name} (value suppressed)')
    if errors:
        print('Publication check failed:', file=sys.stderr)
        for error in sorted(errors):
            print(f'  {error}', file=sys.stderr)
        return False
    label = 'staged snapshot' if staged else f'history of {revision}'
    print(f'Publication check passed: {label}, {len(files)} file versions.')
    return True


def install_hook():
    hook = Path(os.fsdecode(git('rev-parse', '--git-path', 'hooks/pre-push')).strip()).resolve()
    guard = Path(os.fsdecode(git('rev-parse', '--git-common-dir')).strip()).resolve() / 'publication-check.py'
    if hook.exists() or hook.is_symlink():
        raise RuntimeError(f'A pre-push hook already exists; preserve/integrate it manually: {hook}')
    hook.parent.mkdir(parents=True, exist_ok=True)
    guard.write_bytes(Path(__file__).read_bytes())
    # Keep the guard in .git so switching to a local backup cannot remove it.
    body = f'#!/bin/sh\nexec python3 {shlex.quote(str(guard))} --pre-push\n'
    descriptor = os.open(hook, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(body)
    print(f'Installed pre-push publication check: {hook}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('revision', nargs='?', default='HEAD')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--staged', action='store_true')
    modes.add_argument('--install-hook', action='store_true')
    modes.add_argument('--pre-push', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.install_hook:
        install_hook()
        return 0
    if args.pre_push:
        for line in sys.stdin:
            local_ref, local_oid, _, _ = line.split()
            if not local_oid.strip('0'):  # Deleting a remote ref uploads no history.
                continue
            if local_ref.startswith('refs/heads/local-only/'):
                print(f'Refusing to push local backup: {local_ref}', file=sys.stderr)
                return 1
            if not check(local_oid):
                return 1
        return 0
    return 0 if check(args.revision, args.staged) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'Publication check failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
