"""Held-key behavior using synthetic input events; no key injection or playback."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("held_keys", Path(__file__).resolve().parents[1] / "scripts/watch-held-keys.py")
held = importlib.util.module_from_spec(spec)
spec.loader.exec_module(held)


def watcher(keys):
    result = held.HeldKeys.__new__(held.HeldKeys)
    result.pending = {10: set(keys)}
    result.initial = {10: set(keys)}
    return result


def event(code, value):
    return held.EVENT.pack(0, 0, 1, code, value)


class HeldKeyTests(unittest.TestCase):
    def test_non_keyboard_devices_are_skipped_before_open(self):
        with patch.object(held, "sysfs_key_codes", return_value={272, 304}), \
             patch.object(held.os, "open") as open_device:
            with self.assertRaisesRegex(RuntimeError, "No readable keyboard"):
                held.HeldKeys(["non-keyboard"])
        open_device.assert_not_called()

    def test_unused_keyboard_descriptors_are_not_closed_during_snapshot(self):
        with patch.object(held, "sysfs_key_codes", return_value={29, 57}), \
             patch.object(held.os, "open", return_value=10), \
             patch.object(held.os, "read", side_effect=BlockingIOError), \
             patch.object(held, "bitmap", return_value=set()), \
             patch.object(held.os, "close") as close:
            result = held.HeldKeys(["keyboard"])
            self.assertFalse(result.initial)
            close.assert_not_called()
            result.close()
            close.assert_called_once_with(10)

    def test_snapshot_queries_current_state_before_waiting(self):
        with patch.object(held.os, "open", return_value=10), \
             patch.object(held.os, "read", side_effect=BlockingIOError), \
             patch.object(held.os, "close") as close, \
             patch.object(held, "bitmap", side_effect=[{29, 57, 272}, {29, 57}]) as bits:
            result = held.HeldKeys(["fake-keyboard"])
            self.assertEqual(result.initial, {10: {29, 57}})
            self.assertEqual([call.args[1] for call in bits.call_args_list], [0x21, 0x18])
            close.assert_not_called()
            result.close()
            close.assert_called_once_with(10)

    def test_all_original_keys_must_be_released(self):
        result = watcher({29, 57})
        with patch.object(held.select, "select", return_value=([10], [], [])) as select, \
             patch.object(held.os, "read", side_effect=[event(57, 0), event(29, 0)]), \
             patch.object(held, "bitmap", side_effect=[{29}, set()]):
            result.wait()
        self.assertEqual(select.call_count, 2)

    def test_any_mode_finishes_on_first_original_release(self):
        result = watcher({29, 57})
        with patch.object(held.select, "select", return_value=([10], [], [])) as select, \
             patch.object(held.os, "read", return_value=event(57, 0)), \
             patch.object(held, "bitmap", return_value={29}):
            result.wait("any")
        self.assertEqual(select.call_count, 1)

    def test_unrelated_keys_and_autorepeat_do_not_change_the_hold(self):
        result = watcher({57})
        events = [event(30, 0) + event(30, 1) + event(57, 2), event(57, 0)]
        with patch.object(held.select, "select", return_value=([10], [], [])) as select, \
             patch.object(held.os, "read", side_effect=events), \
             patch.object(held, "bitmap", side_effect=[{30, 57}, {30}]):
            result.wait()
        self.assertEqual(select.call_count, 2)

    def test_release_then_repress_still_counts_as_release(self):
        result = watcher({57})
        with patch.object(held.select, "select", return_value=([10], [], [])), \
             patch.object(held.os, "read", return_value=event(57, 0) + event(57, 1)), \
             patch.object(held, "bitmap", return_value={57}):
            result.wait()
        self.assertFalse(result.pending[10])

    def test_mouse_and_gamepad_buttons_are_not_keyboard_keys(self):
        self.assertEqual(held.keyboard_codes({29, 57, 272, 304, 544, 705}), {29, 57})


if __name__ == "__main__":
    unittest.main()
