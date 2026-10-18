"""Installed console entry point for CPU Phonon-2 keyboard dictation."""
import os
from pathlib import Path
import sys


def main() -> int:
    launcher = Path(__file__).resolve().parents[2] / "bin/dictate"
    if not launcher.is_file():
        print(f"Dictation launcher not found: {launcher}", file=sys.stderr)
        return 1
    os.execv(str(launcher), [str(launcher), *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
