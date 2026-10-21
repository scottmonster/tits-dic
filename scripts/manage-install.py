#!/usr/bin/env python3
"""Update or unregister this checkout without deleting user data."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args, capture=False, check=True):
    return subprocess.run(args, cwd=ROOT, check=check, text=True,
                          capture_output=capture)


def status():
    result = run("bash", str(ROOT / "bin/tap-to-talk.sh"), "status", capture=True, check=False)
    if result.returncode:
        return None
    return json.loads(result.stdout)


def assert_idle():
    current = status()
    if current and current.get("state") != "idle":
        raise ValueError("Dictation is busy. Finish/cancel it and rerun this command.")
    return current


def update(options):
    if run("git", "rev-parse", "--show-toplevel", capture=True).stdout.strip() != str(ROOT):
        raise ValueError("Update requires a Git checkout at the installation directory.")
    branch = run("git", "branch", "--show-current", capture=True).stdout.strip()
    if branch != "master":
        raise ValueError("Update requires the master branch. Switch branches yourself before updating.")
    if run("git", "status", "--porcelain", capture=True).stdout.strip():
        raise ValueError("Checkout has local changes. Commit/stash them before updating.")
    run("git", "remote", "get-url", "origin", capture=True)
    assert_idle()
    run("git", "fetch", "origin", "master")
    if run("git", "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD", check=False).returncode:
        raise ValueError("Local master has commits outside origin/master; resolve them before updating.")
    run("git", "merge", "--ff-only", "FETCH_HEAD")
    # Run the newly fetched installer so changes to installation are applied too.
    result = run("bash", str(ROOT / "install.sh"), *options, check=False)
    return result.returncode


def owned_launcher(path, target):
    if path.is_symlink():
        return path.resolve() == target
    if not path.is_file():
        return False
    try:
        return shlex.split(path.read_text(), comments=True) == ["exec", str(target), "$@"]
    except (ValueError, UnicodeError):
        return False


def uninstall():
    import shell_completion
    # Validate every tracked completion before stopping/removing anything.
    completion_record = shell_completion.check_removal()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    unit = config_home / "systemd/user/titsdic.service"
    launchers = [(Path.home() / ".local/bin/titsdic", (ROOT / "bin/dictate",)),
                 (Path.home() / ".local/bin/titsdic-ptt",
                  (ROOT / "bin/tap-to-talk.sh", ROOT / "bin/ptt.sh")),
                 (Path.home() / ".local/bin/titsdic-htt", (ROOT / "bin/hold-to-talk.sh",))]
    unit_exists = unit.exists() or unit.is_symlink()
    if unit_exists:
        expected = "WorkingDirectory=" + str(ROOT).replace("%", "%%")
        if unit.is_symlink() or expected not in unit.read_text().splitlines():
            raise ValueError(f"Service belongs to another installation; leaving it in place: {unit}")
    owned = []
    for path, targets in launchers:
        if path.exists() or path.is_symlink():
            if not any(owned_launcher(path, target) for target in targets):
                raise ValueError(f"Launcher belongs to another installation; leaving it in place: {path}")
            owned.append(path)
    assert_idle()
    if unit_exists:
        run("systemctl", "--user", "disable", "--now", "titsdic.service")
    # A daemon started manually is outside systemd. Shut down only this checkout's
    # process; another checkout can use the same per-user socket.
    current = status()
    if current:
        pid = current.get("daemon_pid")
        if type(pid) is not int or pid <= 0:
            raise ValueError("Cannot identify the running daemon; stop it before uninstalling.")
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if os.fsencode(ROOT / "dictation.py") not in command:
            raise ValueError("The running daemon belongs to another checkout; stop it before uninstalling.")
        run("bash", str(ROOT / "bin/tap-to-talk.sh"), "shutdown")
    if unit_exists:
        unit.unlink()
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "reset-failed", "titsdic.service", capture=True, check=False)
    for path in owned:
        path.unlink()
    shell_completion.remove(completion_record)
    print("Uninstalled the dictation service, command launchers, and shell completion.")
    print(f"Kept checkout and Python environment: {ROOT}")
    print(f"Kept settings and service overrides: {config_home}")
    print("Kept logs, cached models, system packages, and shared ydotool/uinput setup.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    update_parser = commands.add_parser("update", help="Fetch master and rerun its installer")
    update_parser.add_argument("--skip-system-packages", action="store_true")
    update_parser.add_argument("--skip-typing-setup", action="store_true")
    update_parser.add_argument("--no-start", action="store_true")
    update_parser.add_argument("--completion-shell", choices=("auto", "bash", "zsh", "both", "none"))
    update_parser.add_argument("--completion-rc", type=Path)
    commands.add_parser("uninstall", help="Remove service and launchers; preserve files/settings")
    args = parser.parse_args(argv)
    try:
        if args.command == "uninstall":
            return uninstall()
        options = ["--" + key.replace("_", "-") for key in
                   ("skip_system_packages", "skip_typing_setup", "no_start") if getattr(args, key)]
        for key in ("completion_shell", "completion_rc"):
            if getattr(args, key) is not None:
                options.extend(["--" + key.replace("_", "-"), str(getattr(args, key))])
        return update(options)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"{args.command.capitalize()} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
