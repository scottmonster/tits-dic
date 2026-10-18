"""Local structured diagnostics; logging is opt-in until an entry point configures it."""
from datetime import datetime, timezone
import json
import importlib.metadata
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import sys
from pathlib import Path
import threading
import time
import traceback

_started = time.perf_counter()
_logger = None
_log_file = None


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        stream = super()._open()
        os.fchmod(stream.fileno(), 0o600)
        return stream


def log_directory():
    return Path(os.environ.get('TITSDIC_LOG_DIR',
        str(Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))) / 'titsdic/logs')))


def configure(component='ptt'):
    global _logger, _log_file
    directory = log_directory()
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Each process owns its rotating files, avoiding concurrent rotation races.
    path = directory / f'{component}-{os.getpid()}.jsonl'
    logger = logging.getLogger(f'titsdic.{component}.{os.getpid()}')
    logger.propagate = False
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = PrivateRotatingHandler(path, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
        os.chmod(path, 0o600)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
    _logger, _log_file = logger, path
    return str(path)


def current_log_file():
    return str(_log_file) if _log_file else None


def runtime_info():
    versions = {}
    for package in ('fermion-research', 'torch', 'sounddevice', 'numpy', 'webrtcvad'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {'python': sys.version, 'executable': sys.executable,
            'platform': platform.platform(), 'packages': versions}


def close():
    global _logger, _log_file
    if _logger:
        for handler in _logger.handlers[:]:
            handler.close()
            _logger.removeHandler(handler)
    _logger = _log_file = None


def text_fields(text):
    fields = {'text_chars': len(text)}
    if os.environ.get('TITSDIC_LOG_TEXT', '1') != '0':
        fields['text'] = text
    return fields


def event(name, *, exc=None, **fields):
    if _logger is None:
        return
    record = {'time_utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
              'elapsed_ms': round((time.perf_counter()-_started)*1000, 3),
              'monotonic_ns': time.monotonic_ns(), 'pid': os.getpid(),
              'thread': threading.current_thread().name, 'event': name, **fields}
    if exc is not None:
        record.update(error_type=type(exc).__name__, error=str(exc),
                      traceback=''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    _logger.info(json.dumps(record, ensure_ascii=False, default=str))


def flush():
    if _logger:
        for handler in _logger.handlers:
            handler.flush()
