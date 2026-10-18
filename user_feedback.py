"""Desktop feedback for dictation problems; notification failures are nonfatal."""
import json
import subprocess
import sys

import diagnostics as diag


def notify(title, message, *, emit=diag.event):
    emit("notification_requested", title=title, message=message)
    try:
        # gdbus is available on the desktop even when notify-send is absent.
        result = subprocess.run([
            "gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications", "--method",
            "org.freedesktop.Notifications.Notify", "titsdic", "0", "dialog-warning",
            json.dumps(title), json.dumps(message), "[]", "{}", "8000",
        ], check=True, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError) as exc:
        emit("notification_failed", exc=exc, stderr=getattr(exc, "stderr", None))
        print(f"{title}: {message} (desktop notification unavailable: {exc})",
              file=sys.stderr, flush=True)
        return False
    emit("notification_sent", response=result.stdout.strip())
    return True


def no_text(metrics, *, desktop=True):
    if not metrics.input_samples:
        reason = "no_audio"
        message = "No audio arrived. Check that your microphone is connected, powered on, and selected."
    elif not metrics.speech_frames:
        reason = "no_speech"
        message = ("No speech was detected, so nothing was typed. Check your microphone's "
                   "power, mute switch, and selected input.")
    else:
        reason = "empty_asr"
        message = "Speech was detected, but recognition returned no text. Please try again."
    metrics.emit("empty_transcript", reason=reason, input_samples=metrics.input_samples,
                 speech_frames=metrics.speech_frames, inference_calls=metrics.inference_calls)
    if desktop:
        notify("Dictation: no text captured", message, emit=metrics.emit)
