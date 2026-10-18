#!/usr/bin/env python3
"""Phonon-2 dictation entry point and a warm, CPU-only push-to-talk server."""
import argparse
from collections import deque
import fcntl
import json
import math
import os
from pathlib import Path
import queue
import signal
import socket
import stat
import sys
import threading
import time
import uuid
import diagnostics as diag
import user_feedback
from sentence_output import SentenceOutput

ROOT = Path(__file__).resolve().parent


def default_socket():
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/titsdic-{os.getuid()}"))
    return runtime / "titsdic" / "ptt.sock"


def request(path, command, *, metadata=True):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(path))
        payload = ({"command": command, "run_id": os.environ["TITSDIC_RUN_ID"]}
                   if metadata and os.environ.get("TITSDIC_RUN_ID") else command)
        message = json.dumps(payload) if isinstance(payload, dict) else payload
        connection.sendall(message.encode() + b"\n")
        with connection.makefile("rb") as reader:
            result = json.loads(reader.readline(1024 * 1024))
    if isinstance(payload, dict) and result.get("error") == "Unknown PTT command":
        return request(path, command, metadata=False)  # Compatibility with an already-running older daemon.
    return result


class BufferedMicrophone:
    """Keep recent idle audio, then atomically hand capture to a recording queue."""

    def __init__(self, pipeline, seconds):
        self.p = pipeline
        self.lock = threading.Lock()
        # Preserve whole VAD frames; round up by less than one capture block.
        self.frames = deque(maxlen=math.ceil(seconds * 1000 / pipeline.FRAME_MS))
        self.target = None
        self.metrics = None
        self.stream = pipeline.start_mic_stream(callback=self.callback)
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            raise

    def callback(self, indata, frames, time_info, status):
        audio = indata.copy().reshape(-1).astype(self.p.np.float32)
        captured_at = time.perf_counter()
        with self.lock:
            if self.target is None:
                self.frames.append((audio, status, captured_at))
            else:
                self.p.enqueue_mic_audio(audio, status, self.metrics, self.target)

    def attach(self, target, metrics):
        with self.lock:
            # A stalled/disconnected source must not replay old speech on press.
            cutoff = time.perf_counter() - self.frames.maxlen * self.p.FRAME_MS / 1000
            while self.frames and self.frames[0][2] < cutoff:
                self.frames.popleft()
            samples = sum(len(audio) for audio, _, _ in self.frames)
            for audio, status, _ in self.frames:
                self.p.enqueue_mic_audio(audio, status, metrics, target)
            self.frames.clear()
            self.target, self.metrics = target, metrics
            return samples / self.p.TARGET_SR

    def detach(self):
        with self.lock:
            # No recording audio is retained for the next press. New idle audio
            # starts filling the buffer immediately, including during ASR.
            self.target, self.metrics = None, None

    def close(self):
        self.detach()
        try:
            self.stream.stop()
        finally:
            self.stream.close()
            with self.lock:
                self.frames.clear()


class LiveSession:
    """One capture/ASR session at a time; retain the loaded model between presses."""
    def __init__(self, args):
        import npstream as pipeline
        self.p = pipeline
        self.args = args
        self.session_id = None
        self.run_id = None
        load_started = time.perf_counter()
        diag.event("model_loading", model=args.model, threads=args.threads, device="cpu")
        self.model = pipeline.Model(model=args.model, n_threads=args.threads,
                                    context_params={"use_gpu": False, "flash_attn": False},
                                    no_context=True, language="en", split_on_word=True)
        diag.event("model_loaded", model=args.model, load_ms=(time.perf_counter()-load_started)*1000)
        self.lock = threading.RLock()
        self.state = "idle"
        self.stream = None
        self.worker = None
        self.feeder = None
        self.cancelled = threading.Event()
        self.feed_stop = threading.Event()
        self.last_text = ""
        self.last_error = None
        self.last_delay_ms = None
        self.buffer_length = args.buffer_length
        self.output_mode = getattr(args, "output_mode", "sentences")
        self.chunk_words = getattr(args, "chunk_words", 40)
        self.buffered_mic = None
        if args.file is None and self.buffer_length > 0:
            device = args.device
            pipeline.SOUND_DEVICE_ID = int(device) if device and device.isdecimal() else device
            self.buffered_mic = BufferedMicrophone(pipeline, self.buffer_length)
            self.log("buffer_capture_started", buffer_length=self.buffer_length,
                     device=self.buffered_mic.stream.device,
                     samplerate=self.buffered_mic.stream.samplerate,
                     blocksize=self.buffered_mic.stream.blocksize,
                     latency=self.buffered_mic.stream.latency)

    def log(self, name, **fields):
        diag.event(name, session_id=self.session_id, run_id=self.run_id, **fields)

    def status(self):
        with self.lock:
            return {"ok": True, "state": self.state, "model": self.args.model, "device": "cpu",
                    "keyboard_output": not self.args.dry_run, "last_text": self.last_text,
                    "last_error": self.last_error, "last_final_asr_delay_ms": self.last_delay_ms,
                    "session_id": self.session_id, "run_id": self.run_id, "daemon_pid": os.getpid(),
                    "log_file": diag.current_log_file(),
                    "config": {"threads": self.args.threads, "pause_ms": self.args.pause_ms,
                               "buffer_length": self.buffer_length,
                               "output_mode": self.output_mode, "chunk_words": self.chunk_words,
                               "input_device": self.args.device, "file": str(self.args.file) if self.args.file else None}}

    def start(self, run_id=None):
        with self.lock:
            if self.state != "idle":
                self.log("start_rejected", state=self.state, requesting_run_id=run_id)
                return {"ok": False, "error": f"Cannot start while {self.state}", "session_id": self.session_id}
            self.session_id = uuid.uuid4().hex[:16]
            self.run_id = run_id
            self.last_text, self.last_delay_ms = "", None
            self.session_started = time.perf_counter()
            self.log("session_starting", input_device=self.args.device,
                     input_file=str(self.args.file) if self.args.file else None,
                     keyboard_output=not self.args.dry_run, model=self.args.model, threads=self.args.threads)
            p = self.p
            p.model = self.model
            p.ACTIVE_MODEL_PATH = self.args.model
            p.ASR_PROFILE, p.USE_FILTER, p.PROGRESSIVE_TEXT = "balanced", False, True
            p.USE_TEST_FILE = self.args.file is not None
            p.WAIT_AUDIO_COMPLETE = True
            # PTT release supplies EOF; a pause must not end the recording.
            p.MAX_UTTERANCE_FRAMES = 0
            p.PAUSE_ENDPOINT_MS = self.args.pause_ms
            device = self.args.device
            p.SOUND_DEVICE_ID = int(device) if device and device.isdecimal() else device
            p.audio_q = queue.Queue()
            p.vad = p.webrtcvad.Vad(p.VAD_MODE)
            self.metrics = p.Metrics()
            self.metrics.event_logger = self.log
            p.metrics_global = self.metrics
            self.cancelled.clear()
            self.feed_stop.clear()
            self.last_error = None
            self.state = "recording"
            try:
                if self.args.file is not None:
                    if not self.args.file.is_file():
                        raise FileNotFoundError(self.args.file)
                    self.feeder = threading.Thread(target=p.play_file_into_audio_q,
                        args=(self.args.file,), kwargs={"metrics": self.metrics, "stop_event": self.feed_stop},
                        daemon=True)
                    self.feeder.start()
                elif self.buffered_mic is not None:
                    buffered_seconds = self.buffered_mic.attach(p.audio_q, self.metrics)
                    self.log("audio_buffer_prepended", seconds=buffered_seconds,
                             buffer_length=self.buffer_length)
                else:
                    self.stream = p.start_mic_stream()
                    self.stream.start()
                self.log("capture_started", open_ms=(time.perf_counter()-self.session_started)*1000)
                if self.stream is not None:
                    self.log("capture_config", device=self.stream.device,
                             samplerate=self.stream.samplerate, blocksize=self.stream.blocksize,
                             latency=self.stream.latency)
                self.worker = threading.Thread(target=self._run, name=f"asr-{self.session_id}", daemon=True)
                self.worker.start()
            except Exception as exc:
                self.log("capture_start_failed", exc=exc)
                self.last_error = f"{type(exc).__name__}: {exc}"
                if not self.args.dry_run and self.args.file is None:
                    user_feedback.notify("Dictation could not start",
                        "Could not start audio capture. Check your microphone connection and selected input.",
                        emit=self.log)
                try:
                    self._close_capture()
                finally:
                    self.state = "idle"
                raise
            return {"ok": True, "state": "recording", "session_id": self.session_id, "run_id": self.run_id}

    def _close_capture(self):
        if self.buffered_mic is not None:
            self.buffered_mic.detach()
        if self.stream is not None:
            stream, self.stream = self.stream, None
            closed_at = time.perf_counter()
            try:
                stream.stop()
            finally:
                stream.close()
                self.log("capture_closed", close_ms=(time.perf_counter()-closed_at)*1000)

    def stop(self, cancel=False):
        with self.lock:
            self.log("cancel_requested" if cancel else "stop_requested", state=self.state,
                     queue_frames=self.p.audio_q.qsize() if self.state != "idle" else 0)
            if cancel:
                self.cancelled.set()
            if self.state == "recording":
                self.state = "processing"
                self.feed_stop.set()
                try:
                    self._close_capture()
                except Exception as exc:
                    self.log("capture_close_failed", exc=exc)
                    self.metrics.input_error = exc
                    raise
                finally:
                    # Stop capture first, then drain every queued frame before EOF.
                    self.p.audio_q.put(None)
            return {"ok": True, "state": self.state, "cancelled": self.cancelled.is_set(),
                    "session_id": self.session_id, "run_id": self.run_id}

    def _run(self):
        p = self.p
        output = None
        try:
            if self.output_mode == "sentences" and not self.args.dry_run:
                output = SentenceOutput(lambda text: p.type_with_ydotool(text, metrics=self.metrics),
                                        self.cancelled, word_limit=self.chunk_words, emit=self.log)
            options = {"on_stable_text": output.update} if output is not None else {}
            _, text = p.collect_one_utterance(None, metrics=self.metrics, **options)
            with self.lock:
                self.state = "processing"
                self._close_capture()
            if self.metrics.input_error is not None:
                raise self.metrics.input_error
            self.metrics.mark("text_ready")
            self.last_text = text
            if self.metrics.has("input_last_frame"):
                self.last_delay_ms = self.metrics.delta("input_last_frame", "text_ready") * 1000
            self.log("text_ready", final_asr_delay_ms=self.last_delay_ms, **diag.text_fields(text))
            if not self.cancelled.is_set() and not text.strip():
                user_feedback.no_text(self.metrics,
                    desktop=not self.args.dry_run and self.args.file is None)
            elif not self.cancelled.is_set() and not self.args.dry_run:
                if output is not None:
                    output.finish(text)
                else:
                    p.type_with_ydotool(text, metrics=self.metrics)
            else:
                self.log("typing_skipped", cancelled=self.cancelled.is_set(), dry_run=self.args.dry_run)
        except Exception as exc:
            self.log("session_failed", exc=exc)
            self.last_error = f"{type(exc).__name__}: {exc}"
            print(self.last_error, file=sys.stderr, flush=True)
            if not self.cancelled.is_set() and not self.args.dry_run and self.args.file is None:
                user_feedback.notify("Dictation failed",
                    "Dictation could not finish. Check the troubleshooting report for details.", emit=self.log)
        finally:
            if output is not None:
                output.close(discard=True)
            self.feed_stop.set()
            if self.feeder is not None:
                self.feeder.join(timeout=5)
            with self.lock:
                try:
                    self._close_capture()
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    self.log("cleanup_failed", exc=exc)
                self.metrics.health_tick(force=True)
                self.log("session_finished", cancelled=self.cancelled.is_set(), error=self.last_error,
                         input_seconds=self.metrics.input_samples/p.TARGET_SR,
                         speech_seconds=self.metrics.speech_frames*p.FRAME_MS/1000,
                         inference_calls=self.metrics.inference_calls,
                         inference_ms=self.metrics.inference_seconds*1000,
                         final_asr_delay_ms=self.last_delay_ms,
                         input_end_to_typing_ms=(self.metrics.delta("input_last_frame", "typing_end")*1000
                             if self.metrics.has("input_last_frame") and self.metrics.has("typing_end") else None),
                         duration_ms=(time.perf_counter()-self.session_started)*1000)
                diag.flush()
                self.state = "idle"
                print(json.dumps(self.status()), flush=True)

    def shutdown(self):
        self.stop(cancel=True)
        if self.worker is not None:
            self.worker.join(timeout=10)
        if self.buffered_mic is not None:
            self.buffered_mic.close()
            self.buffered_mic = None
            self.log("buffer_capture_closed")


def serve(args):
    path = args.socket
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent = path.parent.lstat()
    if stat.S_ISLNK(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise RuntimeError(f"PTT socket directory must be owned by you with mode 0700: {path.parent}")
    with (path.parent / "server.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A PTT server is already starting or running") from None
        if path.exists():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise RuntimeError(f"Refusing to replace a non-socket: {path}")
            path.unlink()
        log_file = diag.configure("ptt")
        diag.event("daemon_starting", socket=str(path), log_file=log_file, python=sys.executable,
                   model=args.model, threads=args.threads, input_device=args.device,
                   buffer_length=args.buffer_length,
                   output_mode=args.output_mode, chunk_words=args.chunk_words,
                   keyboard_output=not args.dry_run, input_file=str(args.file) if args.file else None)
        diag.event("runtime_info", **diag.runtime_info())
        try:
            session = LiveSession(args)
        except Exception as exc:
            diag.event("daemon_initialization_failed", exc=exc)
            raise
        running = True
        def stop_server(*_):
            nonlocal running
            diag.event("daemon_signal", signal=_[0] if _ else None)
            running = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, stop_server)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                os.chmod(path, 0o600)
                server.listen(8)
                server.settimeout(.25)
                diag.event("daemon_ready", socket=str(path))
                capture_mode = (f"Microphone buffering {session.buffer_length:g}s in memory."
                                if session.buffered_mic is not None else "Capture opens only on start.")
                print(f"PTT ready. {capture_mode} Structured log: {log_file}", flush=True)
                while running:
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        connection.settimeout(2)
                        command, run_id, command_started = None, None, time.perf_counter()
                        try:
                            with connection.makefile("rb") as reader:
                                message = reader.readline(4096).decode().strip()
                            payload = json.loads(message) if message.startswith("{") else {"command": message}
                            command = payload.get("command", "")
                            run_id = payload.get("run_id")
                            command_started = time.perf_counter()
                            if command != "status":
                                diag.event("command_received", command=command, run_id=run_id,
                                           session_id=session.session_id)
                            if command == "start": result = session.start(run_id=run_id)
                            elif command == "stop": result = session.stop()
                            elif command == "cancel": result = session.stop(cancel=True)
                            elif command == "toggle":
                                result = session.stop() if session.status()["state"] == "recording" else session.start(run_id=run_id)
                            elif command == "status": result = session.status()
                            elif command == "shutdown":
                                session.stop(cancel=True)
                                running = False
                                result = {"ok": True, "state": "shutting_down"}
                            else: result = {"ok": False, "error": "Unknown PTT command"}
                        except Exception as exc:
                            diag.event("command_failed", exc=exc, command=command, run_id=run_id,
                                       session_id=session.session_id)
                            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                        if command != "status":
                            diag.event("command_completed", command=command,
                                       run_id=run_id, session_id=session.session_id,
                                       ok=result.get("ok"), error=result.get("error"),
                                       duration_ms=(time.perf_counter()-command_started)*1000)
                        try:
                            connection.sendall(json.dumps(result).encode() + b"\n")
                        except (BrokenPipeError, ConnectionResetError) as exc:
                            diag.event("client_disconnected", exc=exc)
        finally:
            session.shutdown()
            path.unlink(missing_ok=True)
            diag.event("daemon_stopped")
            diag.flush()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("listen", "serve"):
        sub = commands.add_parser(name)
        sub.add_argument("--model", default="phonon-2")
        sub.add_argument("--threads", type=int, default=4)
        sub.add_argument("--device", help="Input device ID or name (system default if omitted)")
        sub.add_argument("--pause-ms", type=int, default=300)
        sub.add_argument("--dry-run", action="store_true", help="Print text without typing")
        sub.add_argument("--file", type=Path, help="Use a recording instead of the microphone")
        if name == "listen":
            sub.add_argument("--offline", action="store_true")
            sub.add_argument("--json-out", type=Path)
        else:
            sub.add_argument("--socket", type=Path, default=default_socket())
            sub.add_argument("--buffer-length", type=float, default=1.25, metavar="SECONDS",
                             help="Keep microphone audio before each press (default: 1.25 seconds; 0 disables)")
            sub.add_argument("--output-mode", choices=("sentences", "final"), default="sentences",
                             help="Type settled sentences while recording, or everything after release")
            sub.add_argument("--chunk-words", type=int, default=40, metavar="N",
                             help="Split long settled text after N words (default: 40; 0 uses punctuation only)")
    client = commands.add_parser("command")
    client.add_argument("action", choices=("start", "stop", "toggle", "cancel", "status", "shutdown"))
    client.add_argument("--socket", type=Path, default=default_socket())
    args = parser.parse_args(argv)
    if args.command == "command":
        try:
            result = request(args.socket, args.action)
        except (OSError, ValueError) as exc:
            print(f"PTT unavailable: {exc}. Run bin/ptt.sh daemon first.", file=sys.stderr)
            return 1
        print(json.dumps(result))
        return 0 if result.get("ok") else 1
    if args.threads < 1 or args.pause_ms < 30:
        parser.error("--threads must be positive and --pause-ms must be at least 30")
    if args.command == "serve":
        if not math.isfinite(args.buffer_length) or args.buffer_length < 0:
            parser.error("--buffer-length must be a finite, non-negative number of seconds")
        if args.chunk_words < 0:
            parser.error("--chunk-words must be non-negative")
        return serve(args)
    diag.configure("dictate")
    diag.event("oneshot_starting", model=args.model, input_device=args.device, keyboard_output=not args.dry_run)
    diag.event("runtime_info", **diag.runtime_info())
    import npstream
    options = [str(args.file)] if args.file is not None else ["--mic", "--until-silence"]
    options += ["--model", args.model, "--threads", str(args.threads), "--pause-ms", str(args.pause_ms)]
    if not args.dry_run: options.append("--type")
    if args.device is not None: options += ["--device", args.device]
    if args.offline: options.append("--offline")
    if args.json_out: options += ["--json-out", str(args.json_out)]
    return npstream.main(options)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ImportError) as exc:
        diag.event("entrypoint_failed", exc=exc)
        diag.flush()
        print(f"Dictation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
