# Tell It Something Dictation

CPU dictation with **Phonon-2** by default. Audio is resampled to 16 kHz mono;
the balanced pipeline preserves the waveform and uses pauses to choose speech
spans. Active-span previews can change internally. The PTT daemon types settled
sentences during recording and sends the remaining tail after release.
There is no manual correction stage yet.

## Install

Run as your normal desktop user in a logged-in Linux desktop session. The
installer supports Debian, Ubuntu, and Pop!_OS system packages. It uses `sudo`
for system packages and virtual keyboard access; the application runs as you.

From this checkout, including before publishing to GitHub:

```bash
bash install.sh
```

After committing and pushing the installer to
[`scottmonster/tits-dic`](https://github.com/scottmonster/tits-dic), install on
another machine with:

```bash
curl -fsSL https://raw.githubusercontent.com/scottmonster/tits-dic/HEAD/install.sh \
  | bash -s -- --repo https://github.com/scottmonster/tits-dic.git
```

`HEAD` uses the repository's default branch. To install a particular branch or
tag, replace `HEAD` in the download URL and pass the same revision with `--ref`.
For a private repository, clone it using your GitHub credentials and run
`bash install.sh` inside the checkout.

The installer:

- Installs FFmpeg, PortAudio, sound libraries, `ydotool`/`ydotoold`, and utilities.
- Installs `uv` if needed, then a separate Python 3.13 CPU environment with pinned
  dependencies in `.venv-phonon`. It downloads/caches Phonon-2 before starting.
- Installs a systemd **user service**, `titsdic.service`, and starts it now. It
  starts again **when you log in after reboot** and restarts if the daemon exits
  or crashes. No root application process or background login is required.
- Sets up virtual keyboard access via a `uinput` module/udev rule if needed and
  enables `ydotoold.service`, preserving an existing user unit. It does not
  configure shortcuts or access to physical keyboard devices.
- Adds `~/.local/bin/titsdic` (one-shot dictation) and
  `~/.local/bin/titsdic-ptt` (daemon control).

The downloaded install defaults to `~/.local/share/titsdic`; `--install-dir PATH`
changes that destination. Local `bash install.sh` installs the current checkout
in place, so keep it at that location. Ensure `~/.local/bin` is on your `PATH`,
or use the full command paths. Configure your own desktop shortcuts, for example
`~/.local/bin/titsdic-ptt start` and `~/.local/bin/titsdic-ptt stop`.

```bash
titsdic-ptt status
systemctl --user status titsdic.service
journalctl --user -u titsdic.service -f
systemctl --user restart titsdic.service
systemctl --user stop titsdic.service                 # stop until next login/start
systemctl --user disable --now titsdic.service        # also disable autostart
```

With the service enabled, `titsdic-ptt shutdown` causes systemd to restart the
daemon; use `systemctl --user stop` to keep it stopped. To change daemon options,
run `systemctl --user edit titsdic.service`, overriding `ExecStart` with an empty
line followed by the absolute launcher path and desired arguments, for example:

```ini
[Service]
ExecStart=
ExecStart="/home/YOU/.local/share/titsdic/bin/ptt.sh" serve --device "your input device name" --buffer-length 2
```

Then run `systemctl --user restart titsdic.service`. Installer reruns preserve
these drop-ins. If your typing service uses a custom socket, also add
`Environment=YDOTOOL_SOCKET=/absolute/path/to/socket` to that override.

Rerun the install command to update. Downloaded installs fast-forward only and
refuse to overwrite local changes; local installs use the current checkout.
Finish dictating before updating. `bash install.sh --help` lists options,
including `--no-start` (enable autostart without starting dictation now) and
`--skip-typing-setup` (manage `ydotoold`/uinput yourself).

For other Linux distributions with systemd, install `curl`, Git, Python 3,
FFmpeg, the PortAudio and libsndfile shared libraries, `ydotool`/`ydotoold`, and
optionally GLib's `gdbus` and PulseAudio's `paplay`/`pactl` (also work with
PipeWire). Then use `--skip-system-packages`. Automatic typing setup requires
`sudo`, `modprobe`, and `udevadm`; `uaccess` requires an active local desktop
session. Log out/in if the installer reports that device access is still
unavailable. The held-key sound helper has additional physical input access
requirements that you configure yourself.

## Dictate to the focused application

```bash
./bin/dictate
# Or the installed console entry point:
.venv/bin/titsdic
```

Speak into the system's default input. After 700 ms of detected silence, capture
ends and the completed transcript is typed into the focused application using
`ydotool`. Keep that application focused until typing completes. Ctrl+C cancels.
Keyboard output uses a 1 ms delay between keystrokes to help prevent dropped characters.
This one-shot command loads the model on each invocation; use the PTT daemon to
keep the model loaded between recordings.

Select an input by its sounddevice name or ID:

```bash
./bin/dictate --device 'your input device name'
```

## Push to talk

The installer starts the model process automatically at login. Without the
installed service, start it manually once per desktop session. The daemon keeps
the microphone open and a rolling **1.25-second buffer in memory**, so audio
spoken just before the shortcut reaches the daemon is included:

```bash
./bin/ptt.sh daemon
# Optional input selection:
# ./bin/ptt.sh daemon --device 'your input device name'
# Optional pre-recording buffer length, in seconds:
# ./bin/ptt.sh daemon --buffer-length 2
# Disable buffering and open the mic only on press:
# ./bin/ptt.sh daemon --buffer-length 0
# Optional: type everything only after release:
# ./bin/ptt.sh daemon --output-mode final
# Optional: change the long-text fallback (0 uses punctuation only):
# ./bin/ptt.sh daemon --chunk-words 40
```

Buffered audio is prepended to newly recorded audio when you press the key.
The buffer fills while idle and during processing; no recognition runs until a
press. Old idle audio is overwritten, and audio from an active recording is not
reused for the next press. The buffer rounds up to whole 30 ms capture frames
(1.26 seconds for the default). Immediately after startup, only the audio
captured so far is available. No audio buffer is written to `/tmp` or other files.
Speech within the buffer window before a press can appear in the transcript.

Use these commands in your desktop/keybinding tool, with the absolute repository
path if the binding does not run from this directory:

| Event | Command |
| --- | --- |
| PTT key pressed | `/absolute/path/to/tits-dic/bin/ptt.sh start` |
| PTT key released | `/absolute/path/to/tits-dic/bin/ptt.sh stop` |

Capture continues through pauses while the key is held. Release ends the
recording, drains queued audio, completes ASR, and types the remaining text. With
buffering enabled, the microphone stays open until daemon shutdown. A new press is
rejected while the previous transcript is being processed or typed. Keep the
target application focused throughout recording and until output ends.

The default `--output-mode sentences` sends completed sentences once recognition
settles a span at a natural pause (normally 300 ms, after at least one second of
audio). Revisable previews are never typed. Text without sentence punctuation
is sent in chunks of up to `--chunk-words` words (default 40), retaining the
unfinished tail until more text arrives or you release. Common abbreviations,
initials, decimal numbers, and closing quotes are handled conservatively.
Typing runs in its own worker so it does not block capture or recognition.
Uninterrupted speech needs a pause or release before it can be sent; the
word-count fallback also operates on settled text. Release sends only text not
already queued, though queued typing may still need time to finish.

`cancel` discards unsent chunks; a chunk already being typed may finish.
Use a shortcut without held Ctrl/Alt/Super modifiers (such as your current F12
binding), or use toggle mode with the shortcut released while speaking.
`--output-mode final` restores typing the complete transcript after release.

If your binding tool offers only a press action, bind `bin/ptt.sh toggle` to start
on the first press and finish on the second.

```bash
./bin/ptt.sh status    # state, last transcript/error, final ASR delay
./bin/ptt.sh cancel    # end recording and discard output still pending
./bin/ptt.sh shutdown # cancel pending output and exit the daemon
./bin/ptt.sh serve    # foreground alternative to daemon (Ctrl+C exits)
```

`cancel` cannot undo characters already typed. A repeated `start` never opens a
second capture stream. To change the daemon's device, buffer length, threads, or output mode,
shut it down and start it again with the desired options. Logs are at
`$XDG_RUNTIME_DIR/titsdic/ptt.log` (fallback: `/tmp/titsdic-$UID/titsdic/ptt.log`).
For the installed systemd service, use an override/restart as described above;
console logs go to `journalctl --user -u titsdic.service`.
Audio is held in memory and is not saved. The log contains recognized text.

Python also writes persistent JSON event logs to
`${XDG_STATE_HOME:-$HOME/.local/state}/titsdic/logs/ptt-PID.jsonl`.
One-shot dictation uses `dictate-PID.jsonl`; direct `npstream.py` runs use
`pipeline-PID.jsonl`. Set `TITSDIC_LOG_DIR` to choose another directory.
Each process rotates its log at 5 MiB and keeps two backups. Files are private
to your user. Old process logs remain available after a daemon restart.

Events include runtime/package versions, model loading, capture startup/close,
queue depth and microphone overflow/status counters, each decode window and ASR
duration, recognized text, final ASR delay, typing duration, and session cleanup.
Failures include full Python tracebacks; typing failures also include the
command's exit code and stderr. Capture health is logged at most once a second
from the worker, with no disk logging in the microphone callback. `session_id`
groups Python events; `run_id` links them to the hotkey timing log. The final
summary includes audio-end-to-transcript and audio-end-to-typing timings.

Live dictation shows a desktop notification after release if no text was
captured. If no speech was detected, it prompts you to check microphone power,
mute, and input selection. Speech with an empty recognition result gets a
different message; capture/processing errors also show an alert. Cancelled
sessions, file tests, and dry runs do not show these notifications. Delivery uses
the desktop notification service through `gdbus` (normally supplied by GLib).
Empty results and notification delivery/failures are included in the Python log.

After an issue, collect a local report with:

```bash
./bin/ptt.sh report
```

This writes `build/diagnostics/report-TIMESTAMP.txt` containing daemon status,
recent Python/console/hotkey logs, package versions, and audio/typing service
details. It never opens the microphone or types, and does not upload anything.
Logs and reports include dictated text. Restart an existing idle daemon after
updating Python code so that it loads the new diagnostics.

## Check without microphone or keyboard access

```bash
./bin/dictate --file /path/to/your-test.wav --dry-run --offline
./bin/ptt.sh daemon --file /path/to/your-test.wav --dry-run
./bin/ptt.sh start
./bin/ptt.sh status
./bin/ptt.sh shutdown
```

`--dry-run` disables typing. `--file` selects a recording instead of the mic and
bypasses microphone buffering.
PTT file input runs in real time and finishes at file EOF or `stop`, whichever
comes first. These commands intentionally never install a global key binding.

## Runtime

The launchers use `.venv-phonon/bin/python`, created by `install.sh` from
`requirements-dictation.txt`. They fall back to the older
`build/asr-compare/env-phonon/bin/python` environment if no installed runtime
exists. Override Python with `TITSDIC_PYTHON`. The CPU runtime is independent of
the experimental Whisper dependencies in `pyproject.toml`; `uv sync` is not
needed for installation. `ydotoold` must be running with access to uinput.

Direct file pipeline usage in that environment remains available:

```bash
.venv-phonon/bin/python npstream.py /path/to/your-test.wav
```

The default is Phonon-2, four CPU threads, no RNNoise filtering, and a 300 ms span
pause threshold. `--pause-ms 450` trades some delay for longer context.
`npstream.py` itself defaults to file input and printing, with explicit `--mic`
and `--type` flags. `bin/dictate` defaults to microphone input and keyboard output.
FUTO Whisper remains available with an explicit `--model models/ggml/acft.base.q8.bin`
and a Python environment containing `pywhispercpp`.

Tests: `.venv-phonon/bin/python -m unittest discover -s tests -v`.

## Repository contents and publishing

The public repository includes source, tests, install dependencies, benchmark
tools, and the two short generated sound cues. Recordings, dictated transcripts,
datasets, models, logs, editor settings, machine-specific configuration, and local
research notes are excluded. Supply your own audio for the file commands above.
The experimental benchmark dashboard in `tst/` also needs local benchmark data;
see `tst/data/README.md`. None of that data is needed to install dictation or run
the unit tests.

Before publishing changes, check both the staged files and the branch history:

```bash
git add .
python3 scripts/check-publication.py --staged
git diff --cached --stat
# After reviewing and committing:
python3 scripts/check-publication.py HEAD
git push -u origin master
```

`python3 scripts/check-publication.py --install-hook` installs a local pre-push
check. It rejects excluded files, files over 10 MiB, recognized credential
patterns, and branches named `local-only/*`. The checks cover the complete
history being pushed. They supplement reviewing changes; they do not guarantee
that arbitrary text contains no private information. Local backup branches must
stay local; push `master` explicitly rather than using `--all` or `--mirror`.

## Held-key dictation with sound timing

Run `bin/ptt.sh daemon` once to keep Phonon-2 loaded, then bind
`bin/sound-test.sh` to your shortcut's press action. No separate release binding
is needed. The script snapshots the held keys first, starts microphone capture,
and plays the start tone. Releasing the keys stops capture, finalizes and types
the transcript, and plays the exit tone. Capture stops independently of slow
sound playback. `--release any` ends on the first original key's release; the
default waits for all original keys. New key presses do not extend the hold.
Settled sentences are typed during recording by default; release sends the
remaining tail. Internal ASR previews remain revisable and are never typed.

Both tones default to **50% of their previous amplitude**. Adjust the variable
at the very top of `bin/sound-test.sh`, or override it for an invocation:

```bash
TONE_VOLUME_PERCENT=25 ./bin/sound-test.sh
./bin/sound-test.sh --sound-only  # cues and timing without microphone or typing
```

Scaled WAV files are cached once per volume setting. A missing/busy PTT daemon
fails before the start cue, and a shortcut released during startup does not open
a late recording. Output-device selection below affects the tones; configure the
microphone with `bin/ptt.sh daemon --device ...`.

Timing is printed to stderr and appended to `build/sound-key-test/timing.log`.
Watch it from another terminal while triggering the shortcut:

```bash
tail -F build/sound-key-test/timing.log
```

Each run logs Python startup, device scanning, key-label lookup, the start
playback request, player runtime, and release-to-exit-playback timing. Times are
milliseconds since Bash entry; they exclude desktop shortcut dispatch before
Bash starts. Player runtime includes the 180 ms tone. These software timestamps
do not measure the exact moment sound reaches the speakers.

The tones are pre-generated under `res/sound-test/`, so FFmpeg is no longer run
on startup. Input capabilities are checked through sysfs to skip unrelated
devices, and device cleanup is kept out of the path before the exit sound.
`paplay` requests a 20 ms buffer. Bluetooth wake-up and buffering may still add
audible delay. To compare another output without changing the desktop default:

```bash
pactl list short sinks
./bin/sound-test.sh --device '<sink-name>'
```

Use `--log /path/to/file.log` to choose another timing log.
