#!/usr/bin/env python3
"""Create a local troubleshooting report without opening the mic or typing."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import diagnostics
from dictation import default_socket, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    date = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = args.output or ROOT / f'build/diagnostics/report-{date}.txt'
    lines = [f'Dictation diagnostic report, UTC {date}', f'Platform: {platform.platform()}',
             f'Python: {sys.version}', f'Python executable: {sys.executable}',
             f'Log directory: {diagnostics.log_directory()}', '']
    try:
        status = request(default_socket(), 'status')
        if os.environ.get('TITSDIC_LOG_TEXT') == '0': status.pop('last_text', None)
        lines += ['Current daemon status:', json.dumps(status, indent=2)]
    except Exception as exc:
        lines += [f'Daemon status unavailable: {type(exc).__name__}: {exc}']
    for package in ('fermion-research', 'torch', 'sounddevice', 'numpy', 'webrtcvad'):
        try: lines.append(f'{package}: {importlib.metadata.version(package)}')
        except importlib.metadata.PackageNotFoundError: lines.append(f'{package}: not installed')
    for command in (['git','rev-parse','HEAD'], ['pactl','get-default-sink'],
                    ['pactl','get-default-source'], ['pactl','list','short','sinks'],
                    ['systemctl','--user','is-active','ydotoold.service']):
        try:
            r = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=3)
            lines += ['', '$ '+' '.join(command), f'exit={r.returncode}', r.stdout, r.stderr]
        except (OSError, subprocess.TimeoutExpired) as exc:
            lines.append(f'{command}: {exc}')
    runtime = default_socket().parent
    paths = [ROOT/'build/sound-key-test/timing.log', runtime/'ptt.log']
    paths += sorted(diagnostics.log_directory().glob('*.jsonl*'), key=lambda p:p.stat().st_mtime, reverse=True)[:8]
    for path in paths:
        lines += ['', f'=== Last 200 lines: {path} ===']
        try:
            with path.open('rb') as stream:
                # Bound memory and report size even when an older console log is large.
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell()-256*1024))
                lines += stream.read().decode(errors='replace').splitlines()[-200:]
        except OSError as exc: lines.append(str(exc))
    output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(output, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream: stream.write('\n'.join(lines)+'\n')
    print(output)


if __name__ == '__main__': main()
