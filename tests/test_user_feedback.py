"""Notifications use mocked D-Bus commands and never capture or inject input."""
import contextlib
import io
import subprocess
import unittest
from unittest.mock import Mock, patch

import npstream as p
import user_feedback as feedback


class FeedbackTests(unittest.TestCase):
    def test_no_speech_prompts_microphone_check_and_logs_reason(self):
        metrics = p.Metrics()
        metrics.input_samples = 16000 * 16
        metrics.event_logger = Mock()
        with patch.object(feedback, 'notify') as notify:
            feedback.no_text(metrics)
        notify.assert_called_once()
        self.assertIn("microphone's power", notify.call_args.args[1])
        self.assertEqual(metrics.event_logger.call_args.kwargs['reason'], 'no_speech')

    def test_empty_recognition_has_a_different_message(self):
        metrics = p.Metrics()
        metrics.input_samples, metrics.speech_frames = 16000, 20
        with patch.object(feedback, 'notify') as notify:
            feedback.no_text(metrics)
        self.assertIn('recognition returned no text', notify.call_args.args[1])

    def test_file_or_dry_run_records_empty_result_without_popup(self):
        with patch.object(feedback, 'notify') as notify:
            feedback.no_text(p.Metrics(), desktop=False)
        notify.assert_not_called()

    def test_notification_failures_do_not_break_dictation(self):
        emit = Mock()
        with patch.object(feedback.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gdbus', 2)), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(feedback.notify('No text', 'Check mic', emit=emit))
        self.assertEqual(emit.call_args.args[0], 'notification_failed')

    def test_notification_command_is_bounded_and_logs_success(self):
        emit = Mock()
        with patch.object(feedback.subprocess, 'run', return_value=Mock(stdout='(uint32 42,)')) as run:
            self.assertTrue(feedback.notify('No text', 'Check mic', emit=emit))
        self.assertEqual(run.call_args.kwargs['timeout'], 2)
        self.assertIn('org.freedesktop.Notifications.Notify', run.call_args.args[0])
        self.assertEqual(emit.call_args.args[0], 'notification_sent')


if __name__ == '__main__':
    unittest.main()
