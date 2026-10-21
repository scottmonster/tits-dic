"""Exercise the shortcut client over a real Unix socket, without audio hardware."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ControlTests(unittest.TestCase):
    def run_client(self, args, responses):
        messages, errors = [], []
        with tempfile.TemporaryDirectory() as directory, \
             socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            path = Path(directory) / "ptt.sock"
            server.bind(str(path))
            server.listen()
            server.settimeout(3)
            def reply():
                try:
                    for response in responses:
                        connection, _ = server.accept()
                        with connection:
                            connection.settimeout(3)
                            with connection.makefile("rb") as reader:
                                messages.append(reader.readline().decode().strip())
                            connection.sendall(json.dumps(response).encode() + b"\n")
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=reply, daemon=True)
            worker.start()
            env = dict(os.environ, TITSDIC_RUN_ID="control-test", TITSDIC_PYTHON=sys.executable)
            result = subprocess.run([str(ROOT / "bin/tap-to-talk.sh"), *args,
                                     "--socket", str(path)], env=env, text=True,
                                    capture_output=True, timeout=5)
            worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
        return result, messages

    def test_default_shortcut_sends_toggle_directly(self):
        # With no action, only --socket cannot be passed to the shell wrapper;
        # test its normal no-argument default via XDG_RUNTIME_DIR instead.
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "titsdic"
            runtime.mkdir()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(runtime / "ptt.sock"))
                server.listen()
                env = dict(os.environ, XDG_RUNTIME_DIR=directory, TITSDIC_PYTHON=sys.executable)
                process = subprocess.Popen([str(ROOT / "bin/tap-to-talk.sh")], env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.addCleanup(lambda: process.kill() if process.poll() is None else None)
                server.settimeout(3)
                connection, _ = server.accept()
                with connection, connection.makefile("rb") as reader:
                    message = reader.readline().decode().strip()
                    payload = json.loads(message) if message.startswith("{") else {"command": message}
                    self.assertEqual(payload["command"], "toggle")
                    connection.sendall(b'{"ok": true, "state": "recording"}\n')
                stdout, stderr = process.communicate(timeout=3)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(json.loads(stdout)["state"], "recording")

    def test_provisional_start_uses_distinct_protocol_and_owner(self):
        result, messages = self.run_client(["start", "--provisional", "--owner", "ours"],
                                           [{"ok": True, "state": "recording"}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(messages[0]),
                         {"command": "prepare", "run_id": "control-test", "owner": "ours"})

    def test_old_daemon_rejects_provisional_capture_without_unsafe_fallback(self):
        result, messages = self.run_client(["start", "--provisional", "--owner", "ours"],
                                           [{"ok": False, "error": "Unknown PTT command"}])
        self.assertEqual(result.returncode, 1)
        self.assertIn("restart", json.loads(result.stdout)["error"])
        self.assertEqual(len(messages), 1)

    def test_owned_cancel_never_downgrades_to_unscoped_cancel(self):
        result, messages = self.run_client(["cancel", "--owner", "ours"],
                                           [{"ok": False, "error": "Unknown PTT command"}])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(messages[0])["command"], "cancel_owned")
        self.assertEqual(len(messages), 1)

    def test_ordinary_controls_keep_legacy_raw_command_compatibility(self):
        result, messages = self.run_client(["toggle"],
                                           [{"ok": False, "error": "Unknown PTT command"},
                                            {"ok": True, "state": "recording"}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(messages[0])["command"], "toggle")
        self.assertEqual(messages[1], "toggle")
