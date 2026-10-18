# Tell It Something Dictation

Local speech-to-text for Linux, powered by **Phonon-2 on CPU**. Dictate into the
focused application with push-to-talk or a single utterance.

## Install

Run from your desktop session as your normal user on Debian, Ubuntu, or Pop!_OS
with systemd:

```bash
curl -fsSL https://raw.githubusercontent.com/scottmonster/tits-dic/master/install.sh \
  | bash -s -- --repo https://github.com/scottmonster/tits-dic.git --ref master
```

The installer uses `sudo` for system dependencies and virtual keyboard access.
It installs the CPU runtime and model, starts dictation, and enables automatic
startup at login after reboot. The daemon also restarts if it exits.
**Keyboard shortcuts are yours to configure.**

Files go in `~/.local/share/titsdic`; commands go in `~/.local/bin`, which must
be on your `PATH`. Rerun the command above to update.

To install an existing checkout, run `bash install.sh` inside it and keep that
directory in place. See `bash install.sh --help` for custom locations and skip
options. Other distributions need FFmpeg, PortAudio, libsndfile, Python 3, Git,
curl, and `ydotool`/`ydotoold` installed manually; use `--skip-system-packages`.

## Use

Configure your desktop shortcuts with these commands. Use their full paths
under `~/.local/bin` if your shortcut tool needs them.

| Action | Command |
| --- | --- |
| Press push-to-talk key | `titsdic-ptt start` |
| Release push-to-talk key | `titsdic-ptt stop` |
| Toggle recording instead | `titsdic-ptt toggle` |
| Cancel pending output | `titsdic-ptt cancel` |
| Check status | `titsdic-ptt status` |

Keep the target field focused throughout dictation and typing. Prefer a shortcut
without held Ctrl/Alt/Super modifiers, or release the shortcut and use toggle mode.

Completed sentences type during natural pauses; long unpunctuated text falls
back to 40-word chunks. Release sends the remaining text. Uninterrupted speech
still needs a pause or release. Cancel cannot undo text already typed.

By default the microphone stays open, keeping the previous 1.25 seconds in memory
to catch speech just before a key press. Audio is not saved.

For one utterance without push-to-talk, run `titsdic`. It stops after 700 ms of
silence and types the transcript. Ctrl+C cancels.

## Settings and service

To select a microphone or change daemon options, run
`systemctl --user edit titsdic.service` and add an override like:

```ini
[Service]
ExecStart=
ExecStart=%h/.local/share/titsdic/bin/ptt.sh serve --device "your microphone" --buffer-length 2
```

Adjust the path for a custom install location, then restart the service. Other
options include `--buffer-length 0` to open the mic only while recording,
`--output-mode final` to type only after release, and `--chunk-words N` to change
the fallback size. `titsdic-ptt serve --help` lists all daemon options.

```bash
systemctl --user restart titsdic.service
systemctl --user stop titsdic.service            # stop until next login/start
systemctl --user disable --now titsdic.service   # also disable autostart
journalctl --user -u titsdic.service -f           # follow service logs
```

Use systemd to stop the installed daemon: `titsdic-ptt shutdown` causes it to
restart automatically.

## Troubleshooting and development

Run `titsdic-ptt report` to collect a local report in `build/diagnostics/`.
Persistent logs are in `~/.local/state/titsdic/logs/`. **Logs and reports can
contain dictated text.**

Test a recording without microphone capture or keyboard output:

```bash
titsdic --file /path/to/audio.wav --dry-run --offline
```

Run the unit tests from an installed checkout:

```bash
.venv-phonon/bin/python -m unittest discover -s tests
```

Recordings, datasets, models, logs, and local notes are excluded from Git. The
benchmark tools under `tst/` require your own [local data](tst/data/README.md).
