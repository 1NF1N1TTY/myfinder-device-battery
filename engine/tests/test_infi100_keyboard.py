"""INFI100 2.4G keyboard battery protocol tests; no physical HID I/O."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _hidstub  # noqa: F401
from providers import infi100_keyboard as I
from test_new_brands import entry as hid_entry, fixture as hid_fixture

P = I.Infi100KeyboardProvider


def entry(physical="infi100-receiver", **changes):
    changes.setdefault("product_string", I.PRODUCT_STRING)
    changes.setdefault("path", (r"\\?\hid#vid_05ac&pid_024f&mi_03&col01#" + physical).encode())
    return hid_entry(P, physical, **changes)


def reply(level=100, trailer=0x85, **changes):
    frame = [0x20, 0x01, 0x00, level] + [0] * 27 + [trailer]
    for offset, value in changes.items():
        frame[int(offset)] = value
    return frame


def fixture(frames=(), inventory=None, **kwargs):
    return hid_fixture(P, frames, [entry()] if inventory is None else inventory, **kwargs)


class Infi100KeyboardTests(unittest.TestCase):
    def test_captured_request_and_known_battery_percentages(self):
        expected = [0x20, 0x01, 0x00] + [0] * 28 + [0x21]
        self.assertEqual(I.make_request(), expected)
        for level, trailer in ((100, 0x85), (97, 0x82), (96, 0x81)):
            with self.subTest(level=level), fixture([reply(level, trailer)]) as bus:
                status, = P().poll()
                self.assertEqual((status.level, status.kind, status.source,
                                  status.verification_level),
                                 (level, "keyboard", "infi100_keyboard", "B"))
                self.assertEqual(status.name, I.PIDS[0x024F])
                self.assertFalse(status.charging_known)
                self.assertEqual(bus.writes, [(entry()["path"], expected)])
                self.assertEqual(bus.reads[0][0], 32)
                self.assertEqual(bus.opened, bus.closed)

    def test_parser_checks_report_signature_length_and_percentage(self):
        for level in (0, 1, 50, 96, 97, 100):
            self.assertEqual(I.parse_reply(reply(level)), (level, False, ""))
        invalid = [reply()[:-1], reply() + [0], reply(**{"0": 0x21}),
                   reply(**{"1": 0x02}), reply(**{"2": 0x01}),
                   reply(101), reply(255), I.make_request()]
        for frame in invalid:
            with self.subTest(frame=frame):
                self.assertIsNone(I.parse_reply(frame))

    def test_other_identity_collection_product_or_unproven_reports_never_open(self):
        defects = [
            dict(vendor_id=0x320F), dict(product_id=0x5088),
            dict(usage_page=0xFF13), dict(usage=0x0001),
            dict(product_string="INFI100"),
            dict(input_report_length=31), dict(input_report_length=33),
            dict(output_report_length=31), dict(output_report_length=33),
            dict(input_report_sizes=None), dict(output_report_sizes=None),
            dict(input_report_sizes={"32": 31}), dict(output_report_sizes={"32": 31}),
            dict(input_report_sizes={"1": 32}), dict(output_report_sizes={"1": 32}),
            dict(path=b""), dict(physical_id="collection:unknown"),
        ]
        for changes in defects:
            with self.subTest(changes=changes), fixture([reply()], [entry(**changes)]) as bus:
                self.assertEqual(P().poll(), [])
                self.assertEqual(bus.opened, [])
                self.assertEqual(bus.writes, [])

    def test_sibling_interface_is_ignored_but_duplicate_mi03_is_rejected(self):
        sibling = entry(path=b"\\\\?\\hid#vid_05ac&pid_024f&mi_00#wired")
        with fixture([reply()], [entry(), sibling]) as bus:
            status, = P().poll()
            self.assertEqual(status.level, 100)
            self.assertEqual(bus.writes, [(entry()["path"], I.make_request())])

        duplicate = entry(path=b"\\\\?\\hid#vid_05ac&pid_024f&mi_03&col01#second")
        with fixture([reply()], [entry(), duplicate]) as bus:
            self.assertEqual(P().poll(), [])
        self.assertEqual(bus.opened, [])
        self.assertEqual(bus.writes, [])

    def test_silence_short_write_and_io_failure_close_handle_without_reading(self):
        for frames, options in (([], {}), ([reply()], {"write_result": 31}),
                                ([reply()], {"error_at": "read"})):
            with self.subTest(options=options), fixture(frames, **options) as bus:
                self.assertEqual(P().poll(), [])
                self.assertEqual(bus.opened, bus.closed)
                if options.get("write_result") is not None:
                    self.assertEqual(bus.reads, [])


if __name__ == "__main__":
    unittest.main()
