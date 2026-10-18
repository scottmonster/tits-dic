"""Exercise troubleshooting evidence without a model, microphone, or keyboard."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import diagnostics as diag
import dictation
import npstream as p


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, TITSDIC_LOG_DIR=self.temp.name)
        env.start()
        self.addCleanup(env.stop)
        diag.close()
        self.path = Path(diag.configure('test'))
        self.addCleanup(diag.close)

    def records(self):
        diag.flush()
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_failure_records_traceback_and_correlated_timing(self):
        try:
            raise RuntimeError('decoder failed')
        except RuntimeError as exc:
            diag.event('asr_failed', exc=exc, session_id='session', run_id='hotkey', duration_ms=123)
        record = self.records()[0]
        self.assertEqual(record['error_type'], 'RuntimeError')
        self.assertIn('raise RuntimeError', record['traceback'])
        self.assertEqual(record['session_id'], 'session')
        self.assertEqual(record['run_id'], 'hotkey')
        self.assertEqual(record['duration_ms'], 123)
        self.assertIn('time_utc', record)
        self.assertGreater(record['monotonic_ns'], 0)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_typing_failure_preserves_exit_code_and_stderr(self):
        failure = subprocess.CalledProcessError(7, ['ydotool'], stderr='socket unavailable')
        with patch.object(p.subprocess, 'run', side_effect=failure):
            with self.assertRaises(subprocess.CalledProcessError):
                p.type_with_ydotool('test', metrics=p.Metrics())
        record = self.records()[-1]
        self.assertEqual(record['event'], 'typing_failed')
        self.assertEqual(record['returncode'], 7)
        self.assertEqual(record['stderr'], 'socket unavailable')
        self.assertIn('CalledProcessError', record['traceback'])

    def test_failed_session_keeps_ids_and_finishes_in_idle(self):
        args = SimpleNamespace(model='phonon-2', threads=4, pause_ms=300,
                               device=None, file=None, dry_run=True, buffer_length=0)
        with patch.object(p, 'Model'), patch.object(p, 'start_mic_stream', return_value=Mock()), \
             patch.object(p, 'collect_one_utterance', side_effect=RuntimeError('inference failed')), \
             patch.object(p, 'type_with_ydotool') as typing, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            session = dictation.LiveSession(args)
            started = session.start(run_id='hotkey-test')
            session.worker.join(timeout=2)
            self.assertFalse(session.worker.is_alive())
            self.assertEqual(session.state, 'idle')
            typing.assert_not_called()
        records = self.records()
        failure = next(r for r in records if r['event'] == 'session_failed')
        final = next(r for r in records if r['event'] == 'session_finished')
        for record in (failure, final):
            self.assertEqual(record['session_id'], started['session_id'])
            self.assertEqual(record['run_id'], 'hotkey-test')
        self.assertIn('inference failed', failure['traceback'])
        self.assertIn('inference failed', final['error'])
        self.assertIn('final_asr_delay_ms', final)

    def test_capture_health_reports_backlog_and_overflow_counters(self):
        metrics = p.Metrics()
        metrics.capture_status_counts['input overflow'] = 2
        with patch.object(p, 'audio_q') as frames:
            frames.qsize.return_value = 15
            metrics.health_tick(force=True)
        record = self.records()[0]
        self.assertEqual(record['queue_peak_frames'], 15)
        self.assertEqual(record['capture_status_counts'], {'input overflow': 2})

    def test_structured_text_can_be_omitted(self):
        with patch.dict(os.environ, TITSDIC_LOG_TEXT='0'):
            self.assertEqual(diag.text_fields('private text'), {'text_chars': 12})


if __name__ == '__main__':
    unittest.main()
