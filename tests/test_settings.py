"""Configuration precedence and validation without loading an ASR model."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dictation
import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.directory))
        env.start()
        self.addCleanup(env.stop)
        self.path = settings.default_path()

    def write(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text)

    def test_missing_default_uses_builtin_defaults_but_explicit_file_is_required(self):
        with patch("dictation.serve", return_value=0) as serve:
            dictation.main(["serve"])
            self.assertEqual(serve.call_args.args[0].buffer_length, 1.25)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dictation.main(["serve", "--config", str(self.path)])
            self.assertEqual(serve.call_count, 1)

    def test_init_is_private_and_does_not_overwrite_settings(self):
        self.assertEqual(settings.initialize(), self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(settings.load()["ptt"]["buffer_length"], 1.25)
        self.write('[ptt]\nbuffer_length = 2\n')
        settings.initialize()
        self.assertEqual(settings.load()["ptt"]["buffer_length"], 2)

    def test_daemon_uses_config_and_explicit_cli_options_win(self):
        self.write('[dictation]\nthreads = 2\ndevice = 7\npause_ms = 450\n'
                   '[ptt]\nbuffer_length = 2\noutput_mode = "final"\nchunk_words = 0\n')
        with patch("dictation.serve", return_value=0) as serve:
            dictation.main(["serve"])
            args = serve.call_args.args[0]
            self.assertEqual((args.threads, args.device, args.pause_ms), (2, "7", 450))
            self.assertEqual((args.buffer_length, args.output_mode, args.chunk_words), (2, "final", 0))
            dictation.main(["serve", "--buffer-length", "0", "--threads", "6",
                            "--output-mode", "sentences", "--device", "another input"])
            args = serve.call_args.args[0]
            self.assertEqual((args.buffer_length, args.threads, args.output_mode, args.device),
                             (0, 6, "sentences", "another input"))

    def test_listen_uses_common_settings_and_ignores_ptt_only_options(self):
        self.write('[dictation]\nthreads = 2\ndevice = "USB mic"\n'
                   '[ptt]\nbuffer_length = 3\n')
        with patch.dict("sys.modules", {"npstream": unittest.mock.Mock()}) as modules:
            modules["npstream"].main.return_value = 0
            dictation.main(["listen", "--dry-run"])
            options = modules["npstream"].main.call_args.args[0]
        self.assertEqual(options[options.index("--threads") + 1], "2")
        self.assertEqual(options[options.index("--device") + 1], "USB mic")
        self.assertNotIn("--buffer-length", options)

    def test_invalid_settings_fail_before_capture_and_no_config_bypasses_them(self):
        for content in ('[ptt]\nbuffer_length = -1', '[ptt]\nbuffer_length = nan',
                        '[ptt]\nbuffer_length = inf', '[ptt]\noutput_mode = "typo"',
                        '[ptt]\nchunk_words = true', '[dictation]\nthreads = "4"',
                        '[dictation]\npause_ms = 0', '[dictation]\ndevice = []',
                        '[ptt]\nbuffer_lenght = 2', '[unknown]\nx = 1', 'bad = ['):
            with self.subTest(content=content):
                self.write(content)
                with patch("dictation.serve", return_value=0) as serve:
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        dictation.main(["serve"])
                    serve.assert_not_called()
                    dictation.main(["serve", "--no-config"])
                    self.assertEqual(serve.call_count, 1)

    def test_custom_file_and_control_commands_do_not_depend_on_default_config(self):
        self.write('invalid TOML')
        custom = self.directory / "custom.toml"
        custom.write_text('[ptt]\nbuffer_length = 4\n')
        with patch("dictation.serve", return_value=0) as serve:
            dictation.main(["serve", "--config", str(custom)])
            self.assertEqual(serve.call_args.args[0].buffer_length, 4)
        with patch("dictation.request", return_value={"ok": True}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dictation.main(["command", "cancel"]), 0)


if __name__ == "__main__":
    unittest.main()
