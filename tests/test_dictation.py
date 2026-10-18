"""PTT lifecycle tests with fake capture and keyboard; never access hardware."""
import contextlib
import io
import json
import os
import queue
import threading
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import dictation
import diagnostics as diag
import npstream as p
from asr_backend import DEFAULT_MODEL, load_model


class BackendTests(unittest.TestCase):
    def test_default_is_phonon_and_loads_without_whisper(self):
        self.assertEqual(DEFAULT_MODEL, "phonon-2")
        with patch("asr_backend.PhononModel") as phonon:
            self.assertIs(load_model(n_threads=3), phonon.return_value)
            phonon.assert_called_once_with(n_threads=3)

    def test_keyboard_reads_transcript_as_data_not_cli_options(self):
        with patch.object(p.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
            p.type_with_ydotool("--help; literal speech")
        call = run.call_args
        self.assertEqual(call.kwargs["input"], "--help; literal speech")
        self.assertEqual(call.args[0][-2:], ["--file", "-"])
        self.assertNotIn("--delay", call.args[0])
        self.assertIn("--key-delay", call.args[0])
        self.assertTrue(call.kwargs["check"])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(model="phonon-2", threads=4, pause_ms=300,
                                    dry_run=False, device="default input", file=None, buffer_length=0)
        self.stream = Mock()
        self.model = Mock()
        self.patches = [patch.object(p, "Model", return_value=self.model),
                        patch.object(p, "start_mic_stream", return_value=self.stream),
                        patch("user_feedback.notify"),
                        patch.object(p, "type_with_ydotool")]
        self.started = [item.start() for item in self.patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.typed = self.started[-1]
        self.session = dictation.LiveSession(self.args)
        self.addCleanup(self.session.shutdown)

    def collect_until_eof(self, *args, **kwargs):
        while p.audio_q.get() is not None:
            pass
        return [], "Hello from Phonon."

    def test_release_closes_capture_before_eof_and_types_once(self):
        with patch.object(p, "collect_one_utterance", side_effect=self.collect_until_eof), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.session.start()["state"], "recording")
            self.assertFalse(self.session.start()["ok"])
            self.assertFalse(self.typed.called)
            self.stream.stop.side_effect = lambda: self.assertTrue(p.audio_q.empty())
            self.session.stop()
            self.session.worker.join(timeout=2)
        self.assertFalse(self.session.worker.is_alive())
        self.stream.stop.assert_called_once()
        self.stream.close.assert_called_once()
        self.typed.assert_called_once()
        self.assertEqual(self.session.status()["state"], "idle")

    def test_model_is_reused_and_vad_reset_between_presses(self):
        with patch.object(p, "collect_one_utterance", side_effect=self.collect_until_eof), \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            first_vad = p.vad
            self.session.stop()
            self.session.worker.join(timeout=2)
            self.session.start()
            self.assertIsNot(p.vad, first_vad)
            self.assertIs(p.model, self.model)
            self.session.stop()
            self.session.worker.join(timeout=2)
        self.assertEqual(self.started[0].call_count, 1)
        self.assertEqual(self.typed.call_count, 2)

    def test_cancel_during_inference_suppresses_keyboard_output(self):
        decoding = threading.Event()
        finish = threading.Event()
        def collect(*args, **kwargs):
            self.collect_until_eof()
            decoding.set()
            finish.wait(timeout=2)
            return [], "Discard me."
        with patch.object(p, "collect_one_utterance", side_effect=collect), \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            self.session.stop()
            self.assertTrue(decoding.wait(timeout=2))
            self.assertFalse(self.session.start()["ok"])
            self.session.stop(cancel=True)
            finish.set()
            self.session.worker.join(timeout=2)
        self.typed.assert_not_called()

    def test_capture_failure_returns_to_idle(self):
        self.stream.start.side_effect = RuntimeError("No input device")
        with self.assertRaisesRegex(RuntimeError, "No input device"):
            self.session.start()
        self.assertEqual(self.session.state, "idle")
        self.stream.close.assert_called_once()
        self.typed.assert_not_called()

    def test_decoder_failure_does_not_type_and_allows_retry(self):
        with patch.object(p, "collect_one_utterance", side_effect=RuntimeError("Decoder failed")), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.session.start()
            self.session.worker.join(timeout=2)
        self.assertEqual(self.session.state, "idle")
        self.assertIn("Decoder failed", self.session.last_error)
        self.typed.assert_not_called()

    def test_empty_capture_notifies_once_and_does_not_type(self):
        with patch.object(p, "collect_one_utterance", return_value=([], "")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            self.session.worker.join(timeout=2)
        self.assertEqual(self.session.state, "idle")
        self.started[-2].assert_called_once()
        self.assertIn("No audio arrived", self.started[-2].call_args.args[1])
        self.typed.assert_not_called()

    def test_cancelled_empty_capture_does_not_notify(self):
        def collect(*args, **kwargs):
            self.collect_until_eof()
            return [], ""
        with patch.object(p, "collect_one_utterance", side_effect=collect), \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            self.session.stop(cancel=True)
            self.session.worker.join(timeout=2)
        self.started[-2].assert_not_called()
        self.typed.assert_not_called()

    def test_sentence_output_types_during_recording_and_only_tail_at_release(self):
        first_sent = threading.Event()
        self.typed.side_effect = lambda *args, **kwargs: first_sent.set()
        def collect(*args, **kwargs):
            kwargs["on_stable_text"]("First sentence. Last")
            while p.audio_q.get() is not None:
                pass
            return [], "First sentence. Last sentence."
        with patch.object(p, "collect_one_utterance", side_effect=collect), \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            self.assertTrue(first_sent.wait(2))
            self.assertEqual(self.session.state, "recording")
            self.assertEqual(self.typed.call_args.args[0], "First sentence.")
            self.session.stop()
            self.session.worker.join(timeout=2)
        self.assertFalse(self.session.worker.is_alive())
        self.assertEqual([call.args[0] for call in self.typed.call_args_list],
                         ["First sentence.", " Last sentence."])
        self.assertEqual(self.session.last_text, "First sentence. Last sentence.")

    def test_final_mode_keeps_all_output_until_release(self):
        self.session.output_mode = "final"
        with patch.object(p, "collect_one_utterance", side_effect=self.collect_until_eof) as collect, \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            self.typed.assert_not_called()
            self.session.stop()
            self.session.worker.join(timeout=2)
        self.assertNotIn("on_stable_text", collect.call_args.kwargs)
        self.typed.assert_called_once()


class BufferedSessionTests(unittest.TestCase):
    def setUp(self):
        self.stream = Mock()
        self.patches = [patch.object(p, "Model"),
                        patch.object(p, "start_mic_stream", return_value=self.stream),
                        patch.object(p, "type_with_ydotool"), patch("user_feedback.notify")]
        self.mocks = [item.start() for item in self.patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.args = SimpleNamespace(model="phonon-2", threads=4, pause_ms=300,
                                    dry_run=False, device=None, file=None, buffer_length=1.25)
        self.session = dictation.LiveSession(self.args)
        self.addCleanup(self.session.shutdown)
        self.callback = self.mocks[1].call_args.kwargs["callback"]

    def frame(self, value):
        self.callback(p.np.full((p.FRAME_SAMPLES, 1), value, dtype=p.np.float32),
                      p.FRAME_SAMPLES, None, "")

    def test_recent_idle_audio_precedes_live_audio_without_reopening(self):
        received = []
        def collect(*args, **kwargs):
            while True:
                frame = p.audio_q.get()
                if frame is None:
                    break
                received.append(float(frame[0]))
            return [], "Buffered speech."

        for i in range(50):
            self.frame(i)
        self.assertEqual(len(self.session.buffered_mic.frames), 42)
        with patch.object(p, "collect_one_utterance", side_effect=collect), \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            self.frame(50)
            self.frame(51)
            self.session.stop()
            self.frame(52)  # After release: buffered for next press, never after EOF.
            self.session.worker.join(timeout=2)
        self.assertFalse(self.session.worker.is_alive())
        self.assertEqual(received, list(range(8, 52)))
        self.assertEqual(self.session.metrics.input_samples, 44 * p.FRAME_SAMPLES)
        self.mocks[1].assert_called_once()
        self.stream.start.assert_called_once()
        self.stream.stop.assert_not_called()
        self.stream.close.assert_not_called()
        self.mocks[2].assert_called_once()

        received.clear()
        with patch.object(p, "collect_one_utterance", side_effect=collect), \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            self.session.stop()
            self.session.worker.join(timeout=2)
        self.assertEqual(received, [52])
        self.session.shutdown()
        self.stream.stop.assert_called_once()
        self.stream.close.assert_called_once()

    def test_cancellation_discards_transcript_and_recorded_audio(self):
        def collect(*args, **kwargs):
            while p.audio_q.get() is not None:
                pass
            return [], "Discard this."
        self.frame(1)
        with patch.object(p, "collect_one_utterance", side_effect=collect), \
             contextlib.redirect_stdout(io.StringIO()):
            self.session.start()
            self.frame(2)
            self.session.stop(cancel=True)
            self.session.worker.join(timeout=2)
        self.mocks[2].assert_not_called()
        self.assertEqual(len(self.session.buffered_mic.frames), 0)
        self.assertEqual(self.session.state, "idle")

    def test_file_input_does_not_open_buffer_capture(self):
        self.session.shutdown()
        self.mocks[1].reset_mock()
        self.args.file = dictation.ROOT / "unused.wav"
        file_session = dictation.LiveSession(self.args)
        self.addCleanup(file_session.shutdown)
        self.assertIsNone(file_session.buffered_mic)
        self.mocks[1].assert_not_called()

    def test_buffer_start_failure_closes_stream(self):
        self.session.shutdown()
        self.stream.reset_mock()
        self.stream.start.side_effect = RuntimeError("Microphone unavailable")
        with self.assertRaisesRegex(RuntimeError, "Microphone unavailable"):
            dictation.LiveSession(self.args)
        self.stream.close.assert_called_once()

    def test_stalled_microphone_does_not_replay_stale_speech(self):
        with patch("dictation.time.perf_counter", return_value=100):
            self.frame(1)
        target = queue.Queue()
        with patch("dictation.time.perf_counter", return_value=102):
            seconds = self.session.buffered_mic.attach(target, p.Metrics())
        self.assertEqual(seconds, 0)
        self.assertTrue(target.empty())
        self.session.buffered_mic.detach()


class EntryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, TITSDIC_LOG_DIR=temp.name)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(diag.close)

    def test_dictation_defaults_to_keyboard_and_phonon(self):
        with patch.object(p, "main", return_value=0) as main:
            self.assertEqual(dictation.main(["listen"]), 0)
        args = main.call_args.args[0]
        self.assertIn("--mic", args)
        self.assertIn("--type", args)
        self.assertIn("--until-silence", args)
        self.assertEqual(args[args.index("--model") + 1], "phonon-2")

    def test_dry_file_mode_never_requests_microphone_or_keyboard(self):
        with patch.object(p, "main", return_value=0) as main:
            dictation.main(["listen", "--file", "tst/data/short.wav", "--dry-run"])
        args = main.call_args.args[0]
        self.assertNotIn("--mic", args)
        self.assertNotIn("--type", args)

    def test_server_buffer_defaults_and_override(self):
        for options, expected in (([], 1.25), (["--buffer-length", "2"], 2),
                                  (["--buffer-length", "0"], 0)):
            with self.subTest(options=options), patch("dictation.serve", return_value=0) as serve:
                self.assertEqual(dictation.main(["serve", *options]), 0)
                self.assertEqual(serve.call_args.args[0].buffer_length, expected)

    def test_sentence_output_defaults_and_overrides(self):
        with patch("dictation.serve", return_value=0) as serve:
            dictation.main(["serve"])
            self.assertEqual(serve.call_args.args[0].output_mode, "sentences")
            self.assertEqual(serve.call_args.args[0].chunk_words, 40)
            dictation.main(["serve", "--output-mode", "final", "--chunk-words", "0"])
            self.assertEqual(serve.call_args.args[0].output_mode, "final")
            self.assertEqual(serve.call_args.args[0].chunk_words, 0)

    def test_negative_word_count_rejected(self):
        with patch("dictation.serve") as serve, contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit):
            dictation.main(["serve", "--chunk-words", "-1"])
        serve.assert_not_called()

    def test_invalid_buffer_lengths_are_rejected_before_capture(self):
        for value in ("-1", "nan", "inf"):
            with self.subTest(value=value), patch("dictation.serve") as serve, \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dictation.main(["serve", "--buffer-length", value])
            serve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
