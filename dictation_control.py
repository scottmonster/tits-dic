"""Small, standard-library-only client for the already-loaded dictation daemon."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys


def default_socket():
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/titsdic-{os.getuid()}"))
    return runtime / "titsdic" / "ptt.sock"


def request(path, command, *, metadata=True, owner=None, provisional=False):
    scoped = owner is not None or provisional
    wire_command = "prepare" if provisional else command
    if owner is not None and command in {"stop", "cancel"}:
        wire_command += "_owned"
    payload = {"command": wire_command}
    if metadata and os.environ.get("TITSDIC_RUN_ID"):
        payload["run_id"] = os.environ["TITSDIC_RUN_ID"]
    if owner is not None:
        payload["owner"] = owner
    # Preserve compatibility with old daemons for ordinary controls only.
    message = json.dumps(payload) if scoped or len(payload) > 1 else wire_command
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(path))
        connection.sendall(message.encode() + b"\n")
        with connection.makefile("rb") as reader:
            result = json.loads(reader.readline(1024 * 1024))
    if not scoped and metadata and isinstance(payload, dict) and len(payload) > 1 \
            and result.get("error") == "Unknown PTT command":
        return request(path, command, metadata=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "toggle", "cancel", "status", "shutdown", "confirm"))
    parser.add_argument("--socket", type=Path, default=default_socket())
    parser.add_argument("--owner", help="Only control the session started with this token")
    parser.add_argument("--provisional", action="store_true",
                        help="Capture now; wait for confirm before recognizing or typing")
    args = parser.parse_args(argv)
    if args.provisional and (args.action != "start" or not args.owner):
        parser.error("--provisional requires start and --owner")
    if args.action == "confirm" and not args.owner:
        parser.error("confirm requires --owner")
    try:
        result = request(args.socket, args.action, owner=args.owner, provisional=args.provisional)
    except (OSError, ValueError) as exc:
        print(f"PTT unavailable: {exc}. Run bin/tap-to-talk.sh daemon first.", file=sys.stderr)
        return 1
    if args.provisional and result.get("error") == "Unknown PTT command":
        result["error"] = "Daemon needs a restart for early capture: systemctl --user restart titsdic.service"
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
