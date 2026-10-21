# Tell It Something Dictation

Local speech-to-text for Linux, powered by **Phonon-2 on CPU**. Dictate into the
focused application with push-to-talk or a single utterance.

## Install

Run from your desktop session as your normal user on Debian, Ubuntu, or Pop!_OS
with systemd:

```bash
curl -fsSL https://raw.githubusercontent.com/scottmonster/tits-dic/master/install.sh | bash
```

The installer uses `sudo` for system dependencies and virtual keyboard access.
It installs the CPU runtime and model, starts dictation, and enables automatic
startup at login after reboot. The daemon also restarts if it exits.
**Keyboard shortcuts are yours to configure.**

This clones the official repository's `master` branch. Files go in
`~/.local/share/titsdic`; `titsdic`, `titsdic-ptt`, and `titsdic-htt` go in
`~/.local/bin`, which must be on your `PATH`. To override the source or revision,
append options with `| bash -s -- --repo URL --ref BRANCH_OR_TAG`.

To install an existing checkout, run `bash install.sh` inside it and keep that
directory in place. See `bash install.sh --help` for custom locations and skip
options. Other distributions need FFmpeg, PortAudio, libsndfile, Python 3, Git,
curl, and `ydotool`/`ydotoold` installed manually; use `--skip-system-packages`.

## Use

Configure your desktop shortcuts with these commands. Use their full paths
under `~/.local/bin` if your shortcut tool needs them.

| Action | Command |
| --- | --- |
| Tap once to start; tap again to stop | `titsdic-ptt` |
| Hold to record; release to stop | `titsdic-htt` |
| Explicitly start recording | `titsdic-ptt start` |
| Explicitly stop recording | `titsdic-ptt stop` |
| Cancel pending output | `titsdic-ptt cancel` |
| Check status | `titsdic-ptt status` |

In a checkout, bind the full path to `bin/tap-to-talk.sh` without arguments to
one shortcut. Each invocation toggles the daemon's recording state. After stopping,
wait for processing to finish before starting another recording. `toggle` is also
available explicitly.

For hold-to-talk, bind `titsdic-htt` (or the checkout's `bin/hold-to-talk.sh`): hold the shortcut
to record, then release it to stop. It retains the start/end tones and existing
options (`--help` lists them). The daemon must already be running for either mode;
the installer starts it, or use `bin/tap-to-talk.sh daemon`.

The old `bin/ptt.sh` and `bin/sound-test.sh` paths forward to the renamed scripts
so existing shortcuts and service units keep working.

Both shortcuts send recording controls directly to the warm daemon. Hold-to-talk
starts provisional capture alongside the keyboard snapshot, before preparing tones
or logs. It holds back recognition and typing until setup succeeds. Missing held
keys, an early release, setup failure, or interruption discards that capture;
cleanup can only cancel the session started by that invocation. Unconfirmed capture
expires after 10 seconds. `--sound-only` never starts dictation.

Run `titsdic-ptt cancel` to stop recording and discard unsent text, including while
the daemon is finishing recognition. Text already typed remains in the target app.
After updating from an older daemon, restart `titsdic.service` for provisional
capture support.

Keep the target field focused throughout dictation and typing. Prefer a shortcut
without held Ctrl/Alt/Super modifiers, or release the shortcut and use toggle mode.

Completed sentences type during natural pauses; long unpunctuated text falls
back to 40-word chunks. Stopping sends the remaining text. Uninterrupted speech
still needs a pause or stop. Cancel cannot undo text already typed.

By default the microphone stays open, keeping the previous 1.25 seconds in memory
to catch speech just before a key press. Audio is not saved.

For one utterance without push-to-talk, run `titsdic`. It stops after 700 ms of
silence and types the transcript. Ctrl+C cancels.

## Settings and service

The installer creates `~/.config/titsdic/config.toml` without overwriting existing
settings. If `XDG_CONFIG_HOME` is set, it uses `$XDG_CONFIG_HOME/titsdic/config.toml`.
To locate or create the file from an older installation:

```bash
titsdic-ptt config           # print the config path
titsdic-ptt config init      # create defaults if missing
titsdic-ptt config show      # validate and print the saved settings as JSON
```

Edit the file with your preferred text editor, for example:

```toml
[dictation]
model = "phonon-2"
threads = 4
pause_ms = 300
# device = "your microphone"  # Omit for the system default; a numeric ID also works.

[ptt]
buffer_length = 1.25
output_mode = "sentences"
chunk_words = 40
```

Restart the service after editing. `buffer_length = 0` opens the mic only while
recording; `output_mode = "final"` types only after release. `chunk_words = 0`
uses punctuation only instead of the fallback word limit.

`[dictation]` applies to both `titsdic` and the PTT daemon; `[ptt]` applies only to
the daemon. Missing settings use the built-in defaults shown above. Invalid or
unknown settings produce an error before audio capture starts. Command-line
options override the file, including options in existing systemd overrides.
Use `--config /path/to/config.toml` with `titsdic`, `titsdic-ptt serve`, or
`titsdic-ptt daemon` to select another file, or `--no-config` to ignore it.
`titsdic-ptt serve --help` lists all daemon options.

```bash
systemctl --user restart titsdic.service
systemctl --user stop titsdic.service            # stop until next login/start
systemctl --user disable --now titsdic.service   # also disable autostart
journalctl --user -u titsdic.service -f           # follow service logs
```

Use systemd to stop the installed daemon: `titsdic-ptt shutdown` causes it to
restart automatically.

## Update and uninstall

```bash
titsdic update
titsdic-ptt uninstall
```

Update fetches `master` from the checkout's `origin`, fast-forwards, and runs the
updated installer. It requires a clean `master` checkout without local commits
outside the remote history. Finish or cancel any active dictation first. Config
and systemd overrides are preserved. Installer flags `--skip-system-packages`,
`--skip-typing-setup`, `--no-start`, `--completion-shell`, and `--completion-rc`
are also accepted by `update`.
`titsdic-ptt update` remains available as an alias.

Uninstall disables/stops the dictation service and removes its unit and the three
installed command launchers, completion files, and managed shell configuration
blocks. It preserves the checkout, Python environment,
settings, systemd overrides, logs, and cached models. System packages and shared
`ydotoold`/uinput setup are preserved. You can delete the retained files yourself
when no longer needed. To reinstall from the retained checkout, run
`bash install.sh` there. Lifecycle commands also work directly from a checkout
as `bash bin/tap-to-talk.sh update` or `bash bin/tap-to-talk.sh uninstall`.

## Shell completion

The installer enables completion for `titsdic`, `titsdic-ptt`, and `titsdic-htt`
in the current interactive Bash or Zsh shell (falling back to `$SHELL` and your
account's login shell). Open a new shell after installation.

For Zsh, it reads your interactive login configuration to discover `ZDOTDIR`
and `fpath`. It uses an existing user-owned completion directory outside plugin
and package trees, or creates `$ZDOTDIR/completions`. A managed block in the
detected `.zshrc` registers the commands, including when a framework has already
run `compinit`.

For Bash, it reads the interactive configuration to discover
`BASH_COMPLETION_USER_DIR` or `XDG_DATA_HOME`. It installs into the resulting
`bash-completion/completions` directory (`$BASH_COMPLETION_USER_DIR/completions`
when set). Managed blocks load completion directly, so the `bash-completion`
package is optional. It handles `.bashrc` and the active login profile, or a
custom `--rcfile`/`--init-file` detected on the calling interactive Bash process.

Use `--completion-shell bash`, `zsh`, or `both` to select shells explicitly.
Use `--completion-rc /absolute/path/to/rc` with one shell for a custom startup
file that cannot be inferred from the calling process. `--completion-shell none`
skips completion and removes completion previously installed by this checkout.
Discovery failures stop installation; they do not silently pick default paths.

The local `.titsdic-completions.json` record tracks the files and exact shell
configuration blocks owned by this checkout. Keep it for updates and uninstall.
Uninstall uses that record even if your shell or XDG settings have changed, and
refuses to remove completion files or blocks that you have edited.

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
