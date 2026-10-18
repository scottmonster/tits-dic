"""Regressions for file dictation; no microphone or keyboard access."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import npstream as pipeline


class AudioTests(unittest.TestCase):
    def test_futo_audio_context_tracks_clip_length_and_clamps(self):
        self.assertEqual(pipeline.whisper_audio_context(7 * 16000, "acft.base.q8.bin"), 382)
        self.assertEqual(pipeline.whisper_audio_context(100, "acft.base.q8.bin"), 160)
        self.assertEqual(pipeline.whisper_audio_context(40 * 16000, "acft.base.q8.bin"), 1500)
        self.assertEqual(pipeline.whisper_audio_context(7 * 16000, "tiny.en.q8.bin"), 0)

    def test_empty_audio_never_reaches_whisper(self):
        with patch.object(pipeline, "model", create=True) as model:
            self.assertEqual(pipeline.process(np.array([], dtype=np.float32)), "")
            self.assertEqual(pipeline.process(np.array([], dtype=np.float32), return_segments=True), [])
            model.transcribe.assert_not_called()
        self.assertEqual(len(pipeline.normalize(np.array([]))), 0)

    def test_partial_frame_is_not_lost(self):
        self.assertEqual(pipeline.read_exact(io.BytesIO(b"12345"), 8), b"12345")
        sample = np.ones(100, dtype=np.float32).tobytes()
        frame = pipeline.read_next_frame(SimpleNamespace(stdout=io.BytesIO(sample)))
        self.assertEqual(len(frame), pipeline.FRAME_SAMPLES)
        np.testing.assert_array_equal(frame[:100], 1)
        np.testing.assert_array_equal(frame[100:], 0)

    def test_silence_is_not_speech(self):
        self.assertFalse(pipeline.is_speech_frame_f32(np.zeros(pipeline.FRAME_SAMPLES)))

    def test_detector_gain_does_not_mutate_audio(self):
        frame = np.full(pipeline.FRAME_SAMPLES, 0.0002, dtype=np.float32)
        original = frame.copy()
        pipeline.is_speech_frame_f32(frame)
        np.testing.assert_array_equal(frame, original)

    def test_file_mode_import_does_not_initialize_sounddevice(self):
        result = subprocess.run(
            [sys.executable, "-c", "import npstream, sys; assert 'sounddevice' not in sys.modules"],
            cwd=Path(pipeline.__file__).parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.ones(pipeline.FRAME_SAMPLES, dtype=np.float32)

    def test_first_speech_frame_is_kept_once(self):
        collector = pipeline.SpeechCollector()
        collector.push(self.frame, True)
        self.assertEqual(len(collector.finish()), pipeline.FRAME_SAMPLES)

    def test_long_pause_is_removed_and_later_speech_kept(self):
        collector = pipeline.SpeechCollector()
        collector.push(self.frame, True)
        for _ in range(100):
            collector.push(self.frame * 0, False)
        collector.push(self.frame * 2, True)
        audio = collector.finish()
        expected_frames = 2 + 2 * pipeline.START_PADDING_FRAMES
        self.assertEqual(len(audio), expected_frames * pipeline.FRAME_SAMPLES)
        np.testing.assert_array_equal(audio[-pipeline.FRAME_SAMPLES:], 2)

    def test_short_pause_is_preserved(self):
        collector = pipeline.SpeechCollector()
        collector.push(self.frame, True)
        for _ in range(3):
            collector.push(self.frame * 0, False)
        collector.push(self.frame, True)
        self.assertEqual(len(collector.finish()), 5 * pipeline.FRAME_SAMPLES)

    def test_all_silence_produces_empty_audio(self):
        collector = pipeline.SpeechCollector()
        for _ in range(100):
            collector.push(self.frame * 0, False)
        self.assertEqual(len(collector.finish()), 0)

    def test_finish_does_not_append_padding_twice(self):
        collector = pipeline.SpeechCollector()
        collector.push(self.frame, True)
        collector.push(self.frame * 0, False)
        self.assertEqual(len(collector.finish()), len(collector.finish()))


class ContextTests(unittest.TestCase):
    def test_short_sentences_settle_for_live_output_without_publishing_previews(self):
        settled = []
        decoder = pipeline.PauseDecoder(on_stable_text=settled.append)
        with patch.object(pipeline, "process", side_effect=["First sentence.", "An unfinished tail"]):
            decoder.push(np.ones(pipeline.TARGET_SR, dtype=np.float32), True)
            self.assertEqual(settled, [])
            decoder.push(np.zeros(decoder.PAUSE_SAMPLES, dtype=np.float32), False)
            self.assertEqual(settled, ["First sentence."])
            decoder.push(np.ones(pipeline.TARGET_SR, dtype=np.float32), True)
            self.assertEqual(decoder.finish(), "First sentence. An unfinished tail")
        self.assertEqual(settled, ["First sentence."])

    def test_revised_preview_is_not_sent_until_settled(self):
        settled = []
        decoder = pipeline.PauseDecoder(on_stable_text=settled.append)
        with patch.object(pipeline, "process", side_effect=["Take the train.", "Take the plane."]), \
             contextlib.redirect_stdout(io.StringIO()):
            decoder.push(np.ones(7 * pipeline.TARGET_SR, dtype=np.float32), True)
            self.assertEqual(settled, [])
            decoder.push(np.zeros(decoder.PAUSE_SAMPLES, dtype=np.float32), False)
        self.assertEqual(settled, ["Take the plane."])

    def test_recognition_receives_unmodified_samples(self):
        audio = np.array([0.00001, -0.0002, 0.08], dtype=np.float32)
        model = Mock()
        model.transcribe.return_value = []
        with patch.object(pipeline, "ASR_PROFILE", "balanced"), \
             patch.object(pipeline, "model", model, create=True), \
             contextlib.redirect_stdout(io.StringIO()):
            pipeline.process(audio)
        np.testing.assert_array_equal(model.transcribe.call_args.args[0], audio)

    def test_vad_does_not_remove_samples_from_the_recognition_timeline(self):
        frames = [np.full(480, value, dtype=np.float32) for value in (0, .01, .0001, .02, 0)]
        with patch.object(pipeline, "PROGRESSIVE_TEXT", False), \
             patch.object(pipeline, "read_next_frame", side_effect=frames + [None]), \
             patch.object(pipeline, "is_speech_frame_f32", side_effect=[False, True, False, True, False]):
            audio, text = pipeline.collect_context_utterance(None)
        np.testing.assert_array_equal(audio, np.concatenate(frames))
        self.assertIsNone(text)

    def test_pause_replaces_the_revisable_hypothesis_without_duplicate_words(self):
        decoder = pipeline.PauseDecoder()
        speech = np.ones(7 * pipeline.TARGET_SR, dtype=np.float32)
        pause = np.zeros(decoder.PAUSE_SAMPLES, dtype=np.float32)
        with patch.object(pipeline, "process", side_effect=["We take the train.", "We take the plane."]) as decode, \
             contextlib.redirect_stdout(io.StringIO()):
            decoder.push(speech, True)
            decoder.push(pause, False)
            result = decoder.finish()
        self.assertEqual(result, "We take the plane.")
        self.assertEqual(decode.call_count, 2)
        np.testing.assert_array_equal(decode.call_args.args[0], np.concatenate([speech, pause]))

    def test_eof_reuses_a_decode_at_the_exact_preview_boundary(self):
        decoder = pipeline.PauseDecoder()
        with patch.object(pipeline, "process", return_value="Done.") as decode, \
             contextlib.redirect_stdout(io.StringIO()):
            decoder.push(np.ones(7 * pipeline.TARGET_SR, dtype=np.float32), True)
            self.assertEqual(decoder.finish(), "Done.")
        self.assertEqual(decode.call_count, 1)

    def test_uninterrupted_speech_uses_bounded_windows(self):
        decoder = pipeline.PauseDecoder()
        words = [SimpleNamespace(text="Speech.", t0=0, t1=100)]
        with patch.object(pipeline, "process", return_value=words) as decode, \
             contextlib.redirect_stdout(io.StringIO()):
            decoder.push(np.ones(decoder.MAX_SAMPLES, dtype=np.float32), True)
            decoder.push(np.ones(7 * pipeline.TARGET_SR, dtype=np.float32), True)
            decoder.finish()
        self.assertEqual([len(call.args[0]) / pipeline.TARGET_SR for call in decode.call_args_list], [28, 9])

    def test_non_speech_does_not_trigger_recognition(self):
        decoder = pipeline.PauseDecoder()
        with patch.object(pipeline, "process") as decode:
            decoder.push(np.zeros(8 * pipeline.TARGET_SR, dtype=np.float32), False)
            self.assertEqual(decoder.finish(), "")
        decode.assert_not_called()

    def test_speech_after_a_pause_keeps_its_onset(self):
        decoder = pipeline.PauseDecoder()
        first = np.ones(5 * pipeline.TARGET_SR, dtype=np.float32)
        pause = np.zeros(decoder.PAUSE_SAMPLES, dtype=np.float32)
        second = np.full(pipeline.TARGET_SR, .25, dtype=np.float32)
        with patch.object(pipeline, "process", side_effect=["First.", "Second."]) as decode:
            decoder.push(first, True)
            decoder.push(pause, False)
            decoder.push(second, True)
            self.assertEqual(decoder.finish(), "First. Second.")
        np.testing.assert_array_equal(decode.call_args.args[0], np.concatenate([pause[len(pause)//2:], second]))


class MergeTests(unittest.TestCase):
    def test_non_speech_markers_and_punctuation(self):
        self.assertEqual(pipeline.clean_text("Hello . [BLANK_AUDIO] world !"), "Hello. world!")

    def test_time_anchor_replaces_a_differently_recognized_overlap(self):
        state = {"processed_samples": 7 * pipeline.TARGET_SR, "text": "",
                 "words": [{"text": "a", "start": 4.8, "end": 5.0},
                           {"text": "Linux", "start": 5.7, "end": 6.2},
                           {"text": "lab.", "start": 6.2, "end": 6.8}]}
        segments = [SimpleNamespace(text="Linux", t0=70, t1=120),
                    SimpleNamespace(text="laptop", t0=120, t1=170),
                    SimpleNamespace(text="works.", t0=200, t1=240)]
        pipeline.merge_timed_segments(state, segments, audio_start=5)
        self.assertEqual(state["text"], "a Linux laptop works.")

    def test_unmatched_overlap_uses_audio_time(self):
        state = {"processed_samples": 7 * pipeline.TARGET_SR, "text": "",
                 "words": [{"text": "Keep", "start": 4, "end": 5},
                           {"text": "wrong", "start": 6.2, "end": 6.8}]}
        segments = [SimpleNamespace(text="other", t0=20, t1=60),
                    SimpleNamespace(text="correct.", t0=120, t1=180)]
        pipeline.merge_timed_segments(state, segments, audio_start=5)
        self.assertEqual(state["text"], "Keep correct.")

    def test_exact_chunk_boundary_is_not_decoded_again_at_eof(self):
        state = {"processed_samples": 0, "text": ""}
        audio = np.zeros(pipeline.PROGRESSIVE_CHUNK_SAMPLES, dtype=np.float32)
        with patch.object(pipeline, "process", return_value=[SimpleNamespace(text="Done.", t0=0, t1=100)]) as decode:
            pipeline.flush_progressive_chunks(state, audio)
            self.assertEqual(pipeline.finalize_progressive_chunks(state, audio), "Done.")
            self.assertEqual(decode.call_count, 1)


class MainTests(unittest.TestCase):
    def run_fake_file(self, mode, frames):
        model = Mock()
        model.transcribe.return_value = [SimpleNamespace(text="Hello.", t0=0, t1=100)]
        def feed(*args, **kwargs):
            for _ in range(frames):
                pipeline.audio_q.put(np.ones(pipeline.FRAME_SAMPLES, dtype=np.float32))
            pipeline.audio_q.put(None)
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            audio.touch()
            output = Path(directory) / "result.json"
            with patch.object(pipeline, "Model", return_value=model) as constructor, \
                 patch.object(pipeline, "play_file_into_audio_q", side_effect=feed), \
                 patch.object(pipeline, "is_speech_frame_f32", return_value=True), \
                 patch.object(pipeline, "start_mic_stream", side_effect=AssertionError("mic accessed")), \
                 patch.object(pipeline, "start_ffmpeg_filter", side_effect=AssertionError("filter enabled by default")), \
                 patch.object(pipeline, "type_with_ydotool", side_effect=AssertionError("typing accessed")), \
                 contextlib.redirect_stdout(io.StringIO()):
                status = pipeline.main([str(audio), "--offline", "--mode", mode,
                                        "--json-out", str(output)])
            self.assertEqual(status, 0)
            self.assertEqual(constructor.call_args.kwargs["context_params"], {"use_gpu": False, "flash_attn": False})
            return model, json.loads(output.read_text())

    def test_progressive_final_result_does_not_trigger_full_redecode(self):
        model, result = self.run_fake_file("progressive", 500)
        self.assertEqual(model.transcribe.call_count, 3)
        self.assertEqual(result["inference_calls"], 3)
        self.assertFalse(result["microphone"])
        self.assertFalse(result["typing"])

    def test_file_input_is_not_truncated_at_microphone_duration_limit(self):
        with patch.object(pipeline, "MAX_UTTERANCE_FRAMES", 2):
            model, result = self.run_fake_file("progressive", 500)
        self.assertEqual(model.transcribe.call_count, 3)
        self.assertEqual(result["retained_seconds"], 15)

    def test_complete_mode_decodes_once(self):
        model, result = self.run_fake_file("complete", 500)
        self.assertEqual(model.transcribe.call_count, 1)
        self.assertEqual(result["inference_calls"], 1)

    def test_empty_file_result_does_not_call_asr(self):
        model, result = self.run_fake_file("progressive", 0)
        model.transcribe.assert_not_called()
        self.assertEqual(result["text"], "")


if __name__ == "__main__":
    unittest.main()
