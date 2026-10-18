"""Timing/playback wiring with a fake key monitor and players; no sound or key injection."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]

MONITOR = '''#!/usr/bin/python3
import os, pathlib, sys, time
log=pathlib.Path(os.environ['TEST_EVENTS'])
def note(text):
 with log.open('a') as f: f.write(text+'\\n')
def now(): return time.time_ns()//1000
note('snapshot')
if os.environ['TEST_MODE']=='none':sys.exit(2)
t=now()
print(f'HELD\\t{t}\\t{t}\\t{t}\\tKEY_LEFTCTRL + KEY_SPACE',flush=True)
if os.environ['TEST_MODE']!='early':
 while not pathlib.Path(os.environ['TEST_GATE']).exists():time.sleep(.001)
note('release')
print(f'RELEASED\\t{now()}',flush=True)
if os.environ['TEST_MODE']=='slow-close':
 time.sleep(.1)
 note('closed')
'''
PLAYER = '''#!/usr/bin/python3
import json, os, pathlib, sys, time
with open(os.environ['TEST_EVENTS'],'a') as f:f.write(pathlib.Path(sys.argv[-1]).name+'\\n')
with open(os.environ['TEST_PLAYER_ARGS'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
pathlib.Path(os.environ['TEST_GATE']).touch()
if os.environ.get('TEST_SLOW_PLAYER') and pathlib.Path(sys.argv[-1]).name=='start.wav':
 time.sleep(.15)
 with open(os.environ['TEST_EVENTS'],'a') as f:f.write('start-finished\\n')
'''
PTT = '''#!/usr/bin/python3
import json, os, sys
with open(os.environ['TEST_EVENTS'],'a') as f:f.write('ptt-'+sys.argv[1]+'\\n')
if sys.argv[1]=='start' and os.environ.get('TEST_BUSY'):
 print(json.dumps({'ok':False,'error':'busy'}))
 sys.exit(1)
print(json.dumps({'ok':True,'state':'recording' if sys.argv[1]=='start' else 'processing'}))
'''


class SoundTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for name, code in [('python3', MONITOR), ('player', PLAYER), ('paplay', PLAYER), ('ptt', PTT)]:
            path = self.directory / name
            path.write_text(code)
            path.chmod(0o755)
        self.log = self.directory / 'timing.log'
        self.env = dict(os.environ, PATH=str(self.directory)+os.pathsep+os.environ['PATH'],
                        TEST_MODE='normal', TEST_EVENTS=str(self.directory/'events'),
                        TEST_GATE=str(self.directory/'release'),
                        TEST_PLAYER_ARGS=str(self.directory/'args'),
                        SOUND_PLAYER=str(self.directory/'player'),
                        PTT_COMMAND=str(self.directory/'ptt'),
                        SOUND_TONE_CACHE_DIR=str(self.directory/'tones'))

    def run_script(self, *options, dictation=False):
        mode = [] if dictation else ['--sound-only']
        return subprocess.run([str(ROOT/'bin/sound-test.sh'), '--log', str(self.log), *mode, *options],
                              env=self.env, capture_output=True, text=True, timeout=5)

    def test_timings_separate_startup_playback_and_release(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.log.read_text()
        for marker in ['script entered', 'monitor launch/imports', 'keyboard device scan',
                       'start playback requested', 'start player runtime',
                       'key release observed', 'release observed -> exit request', 'script exiting']:
            self.assertIn(marker, text)
        self.assertRegex(text, r'\+\d+\.\d{3} ms')
        self.assertEqual((self.directory/'events').read_text().splitlines(),
                         ['snapshot', 'start.wav', 'release', 'end.wav'])

    def test_release_during_startup_is_not_lost(self):
        self.env['TEST_MODE'] = 'early'
        result = self.run_script('--release', 'any')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.directory/'events').read_text().splitlines(),
                         ['snapshot', 'release', 'start.wav', 'end.wav'])

    def test_exit_sound_does_not_wait_for_slow_device_cleanup(self):
        self.env['TEST_MODE'] = 'slow-close'
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = (self.directory/'events').read_text().splitlines()
        self.assertLess(events.index('end.wav'), events.index('closed'))

    def test_no_held_keys_records_failure_without_playing(self):
        self.env['TEST_MODE'] = 'none'
        result = self.run_script()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('key snapshot failed', self.log.read_text())
        self.assertEqual((self.directory/'events').read_text(), 'snapshot\n')

    def test_paplay_requests_small_buffer_and_selected_device(self):
        self.env.pop('SOUND_PLAYER')
        result = self.run_script('--device', 'test-wired-sink')
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in (self.directory/'args').read_text().splitlines():
            args = json.loads(line)
            self.assertIn('--latency-msec=20', args)
            self.assertIn('--process-time-msec=5', args)
            self.assertIn('--device=test-wired-sink', args)

    def test_tones_are_ready_without_running_ffmpeg(self):
        for name in ['start', 'end']:
            with wave.open(str(ROOT/f'res/sound-test/{name}.wav')) as sound:
                self.assertEqual(sound.getframerate(), 48000)
                self.assertEqual(sound.getnchannels(), 1)
                self.assertAlmostEqual(sound.getnframes()/sound.getframerate(), .18)

    def test_default_tones_are_half_amplitude(self):
        import array
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ['start', 'end']:
            with wave.open(str(ROOT/f'res/sound-test/{name}.wav')) as original, \
                 wave.open(str(self.directory/f'tones/50/{name}.wav')) as scaled:
                a = array.array('h', original.readframes(original.getnframes()))
                b = array.array('h', scaled.readframes(scaled.getnframes()))
                self.assertEqual(list(b), [round(sample*.5) for sample in a])

    def test_volume_setting_can_be_overridden(self):
        import array
        self.env['TONE_VOLUME_PERCENT'] = '25'
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        with wave.open(str(ROOT/'res/sound-test/start.wav')) as original, \
             wave.open(str(self.directory/'tones/25/start.wav')) as scaled:
            a = array.array('h', original.readframes(original.getnframes()))
            b = array.array('h', scaled.readframes(scaled.getnframes()))
            self.assertEqual(list(b), [round(sample*.25) for sample in a])

    def test_interruption_cancels_only_the_session_this_script_started(self):
        import signal
        import time
        self.env['TEST_MODE'] = 'hold'
        # Withhold the key-release gate even though the mock start tone finishes.
        monitor = self.directory/'python3'
        monitor.write_text(MONITOR.replace("while not pathlib.Path(os.environ['TEST_GATE']).exists():time.sleep(.001)",
                                          'while True:time.sleep(.01)'))
        process = subprocess.Popen([str(ROOT/'bin/sound-test.sh'), '--log', str(self.log)],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        for _ in range(200):
            events = self.directory/'events'
            if events.exists() and 'start.wav' in events.read_text(): break
            time.sleep(.005)
        else:
            self.fail('Dictation did not start')
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 143, stderr)
        events = (self.directory/'events').read_text().splitlines()
        self.assertIn('ptt-cancel', events)
        self.assertNotIn('ptt-stop', events)

    def test_dictation_starts_before_cue_and_stops_before_slow_playback_finishes(self):
        self.env['TEST_SLOW_PLAYER'] = '1'
        result = self.run_script(dictation=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = (self.directory/'events').read_text().splitlines()
        self.assertLess(events.index('ptt-start'), events.index('start.wav'))
        self.assertLess(events.index('release'), events.index('ptt-stop'))
        self.assertLess(events.index('ptt-stop'), events.index('start-finished'))
        self.assertLess(events.index('ptt-stop'), events.index('end.wav'))
        self.assertNotIn('ptt-cancel', events)

    def test_shortcut_released_during_setup_does_not_start_late_capture(self):
        self.env['TEST_MODE'] = 'early'
        result = self.run_script(dictation=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('ptt-start', (self.directory/'events').read_text())

    def test_busy_daemon_does_not_stop_someone_elses_recording(self):
        self.env['TEST_BUSY'] = '1'
        result = self.run_script(dictation=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        events = (self.directory/'events').read_text().splitlines()
        self.assertIn('ptt-start', events)
        self.assertNotIn('ptt-stop', events)
        self.assertNotIn('ptt-cancel', events)
        self.assertNotIn('start.wav', events)


if __name__ == '__main__':
    unittest.main()
