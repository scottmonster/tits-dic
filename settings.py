"""User settings for the installed dictation runtime (standard library only)."""
import argparse
import json
import math
import os
from pathlib import Path
import tomllib


TEMPLATE = '''# Tell It Something Dictation
# Restart the service after editing: systemctl --user restart titsdic.service
# Command-line options override these settings.

[dictation]
model = "phonon-2"
threads = 4
pause_ms = 300
# device = "your microphone"  # A name or numeric input device ID; omit for default.

[ptt]
buffer_length = 1.25  # Seconds before pressing the shortcut; 0 disables the buffer.
output_mode = "sentences"  # "sentences" during pauses, or "final" after release.
chunk_words = 40  # Fallback for long text; 0 uses punctuation only.
'''


def default_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "titsdic/config.toml"


def load(path=None):
    """Validate the entire file, returning the defaults for each runtime mode."""
    explicit = path is not None
    path = Path(path).expanduser() if explicit else default_path()
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        if not explicit:
            return {"dictation": {}, "ptt": {}}
        raise ValueError(f"Config file does not exist: {path}") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Cannot read config {path}: {exc}") from exc
    allowed = {"dictation": {"model", "threads", "pause_ms", "device"},
               "ptt": {"buffer_length", "output_mode", "chunk_words"}}
    for section, values in data.items():
        if section not in allowed or not isinstance(values, dict):
            raise ValueError(f"Config {path}: expected [dictation] or [ptt], got {section!r}")
        for key, value in values.items():
            if key not in allowed[section]:
                raise ValueError(f"Config {path}: unknown setting {section}.{key}")
            valid = False
            if key in {"threads", "pause_ms", "chunk_words"}:
                minimum = {"threads": 1, "pause_ms": 30, "chunk_words": 0}[key]
                valid = type(value) is int and value >= minimum
            elif key == "buffer_length":
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            elif key == "output_mode":
                valid = value in ("sentences", "final")
            elif key == "model":
                valid = isinstance(value, str) and bool(value.strip())
            elif key == "device":
                valid = isinstance(value, str) or (type(value) is int and value >= 0)
                if valid:
                    values[key] = str(value)
            if not valid:
                raise ValueError(f"Config {path}: invalid value for {section}.{key}: {value!r}")
    return {section: data.get(section, {}) for section in allowed}


def initialize(path=None):
    path = Path(path).expanduser() if path is not None else default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path  # Installer reruns and config init never overwrite user settings.
    with os.fdopen(descriptor, "w") as stream:
        stream.write(TEMPLATE)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("path", "init", "show"), nargs="?", default="path")
    parser.add_argument("--config", type=Path, help="Use this file instead of the default config")
    args = parser.parse_args(argv)
    try:
        if args.action == "init":
            print(initialize(args.config))
        elif args.action == "show":
            print(json.dumps(load(args.config), indent=2))
        else:
            print(args.config.expanduser() if args.config else default_path())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
