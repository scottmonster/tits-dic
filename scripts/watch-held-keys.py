#!/usr/bin/env python3
"""Snapshot held Linux keyboard keys, then report their release without grabbing input."""
import argparse
import errno
import fcntl
from pathlib import Path
import os
import re
import select
import struct
import sys
import time

KEY_BYTES = 96  # KEY_CNT=768 in linux/input-event-codes.h
EVENT = struct.Struct("llHHi")  # native struct input_event


def bitmap(fd, number):
    data = bytearray(KEY_BYTES)
    request = (2 << 30) | (KEY_BYTES << 16) | (ord("E") << 8) | number
    fcntl.ioctl(fd, request, data, True)
    return {code for code in range(KEY_BYTES * 8) if data[code // 8] & (1 << (code % 8))}


def keyboard_codes(codes):
    # Exclude mouse/joystick/gamepad BTN_* ranges; retain extended KEY_* codes.
    return {code for code in codes if (0 < code < 0x100 or 0x160 <= code < 0x220
                                      or 0x230 <= code < 0x2c0 or 0x2e8 <= code < 0x300)}


def sysfs_key_codes(path):
    """Read capabilities without opening/waking an unrelated input device."""
    try:
        words = (Path("/sys/class/input") / Path(path).name / "device/capabilities/key").read_text().split()
    except OSError:
        return None  # Custom device paths can still use EVIOCGBIT below.
    word_bits = struct.calcsize("L") * 8
    codes = set()
    for i, word in enumerate(reversed(words)):
        value = int(word, 16)
        codes.update(i * word_bits + bit for bit in range(word_bits) if value & (1 << bit))
    return codes


def key_names():
    names = {}
    header = Path("/usr/include/linux/input-event-codes.h")
    if header.is_file():
        for name, value in re.findall(r"^#define\s+(KEY_\w+)\s+(0x[0-9a-fA-F]+|[0-9]+)\b", header.read_text(), re.M):
            if name not in {"KEY_MAX", "KEY_CNT", "KEY_MIN_INTERESTING"}:
                names.setdefault(int(value, 0), name)
    return names


class HeldKeys:
    def __init__(self, paths=None):
        self.pending = {}
        self.initial = {}
        self.readable = 0
        self.open_fds = []
        denied = []
        try:
            for path in paths if paths is not None else sorted(Path("/dev/input").glob("event*")):
                try:
                    known_caps = sysfs_key_codes(path)
                    if known_caps is not None and not keyboard_codes(known_caps):
                        continue
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                    self.open_fds.append(fd)
                    caps = keyboard_codes(known_caps if known_caps is not None else bitmap(fd, 0x21))
                    if not caps:
                        continue
                    self.readable += 1
                    # Discard events predating this device's startup snapshot.
                    while True:
                        try:
                            if not os.read(fd, EVENT.size * 64): break
                        except BlockingIOError:
                            break
                    held = bitmap(fd, 0x18) & caps  # EVIOCGKEY: currently held, not future presses
                    if held:
                        self.pending[fd] = held
                        self.initial[fd] = set(held)
                except PermissionError:
                    denied.append(str(path))
                except OSError as exc:
                    if exc.errno not in {errno.ENODEV, errno.ENOENT, errno.ENOTTY}:
                        raise
                # Closing evdev descriptors can take several milliseconds each.
                # Keep them until after the release notification.
            if not self.readable:
                detail = " Input-device read permission is required." if denied else ""
                raise RuntimeError("No readable keyboard input devices found." + detail)
        except BaseException:
            self.close()
            raise

    def close(self):
        for fd in self.open_fds:
            os.close(fd)
        self.open_fds.clear()
        self.pending.clear()

    def wait(self, release="all"):
        while any(self.pending.values()):
            active = [fd for fd, down in self.pending.items() if down]
            ready, _, _ = select.select(active, [], [], .02)
            for fd in active:
                down = self.pending[fd]
                try:
                    if fd in ready:
                        data = os.read(fd, EVENT.size * 64)
                        if not data:
                            down.clear()
                        for _, _, kind, code, value in EVENT.iter_unpack(data):
                            if kind == 1 and value == 0:
                                down.discard(code)
                    # Resynchronize after queue overflows and device state changes.
                    down.intersection_update(bitmap(fd, 0x18))
                except OSError as exc:
                    if exc.errno not in {errno.ENODEV, errno.EIO, errno.ENOENT}:
                        raise
                    down.clear()  # unplugging the held-key device also ends the hold
                if release == "any" and down != self.initial[fd]:
                    return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", choices=("all", "any"), default="all")
    parser.add_argument("--timings", action="store_true", help="Include timestamps for the Bash timing log")
    args = parser.parse_args()
    capture_started_us = time.time_ns() // 1000
    watcher = HeldKeys()  # First action: snapshot the keys before preparing any sounds.
    capture_finished_us = time.time_ns() // 1000
    try:
        if not watcher.initial:
            if args.timings:
                print(f"EMPTY\t{capture_started_us}\t{capture_finished_us}\t{time.time_ns() // 1000}\t-", flush=True)
            print("No keys are held. Launch the script from a shortcut and keep its key(s) down.", file=sys.stderr)
            return 2
        names = key_names()
        codes = sorted({code for codes in watcher.initial.values() for code in codes})
        labels = " + ".join(names.get(code, f"KEY_CODE_{code}") for code in codes)
        if args.timings:
            ready_us = time.time_ns() // 1000
            print(f"HELD\t{capture_started_us}\t{capture_finished_us}\t{ready_us}\t{labels}", flush=True)
        else:
            print(labels, flush=True)
        watcher.wait(args.release)
        print(f"RELEASED\t{time.time_ns() // 1000}" if args.timings else "RELEASED", flush=True)
        return 0
    finally:
        watcher.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"Cannot monitor held keys: {exc}", file=sys.stderr)
        raise SystemExit(1)
