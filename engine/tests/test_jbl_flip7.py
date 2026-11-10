import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import jbl_flip7 as J


def frame(raw):
    return bytes((0xAA, 0x12, 0x03, 0x00, 0x44, raw))


class Flip7ProtocolTests(unittest.TestCase):
    def test_discharge_percent(self):
        self.assertEqual(J.parse_notifications([frame(30)]), (30, False))

    def test_charging_percent(self):
        self.assertEqual(J.parse_notifications([frame(0x8A)]), (10, True))

    def test_zero_and_full_battery_are_valid(self):
        self.assertEqual(J.parse_notifications([frame(0)]), (0, False))
        self.assertEqual(J.parse_notifications([frame(100)]), (100, False))

    def test_unrelated_info_tokens_are_ignored(self):
        self.assertIsNone(J.parse_notifications([
            bytes((0xAA, 0x12, 0x03, 0x00, 0x43, 0x01))
        ]))

    def test_invalid_percent_is_rejected(self):
        self.assertIsNone(J.parse_notifications([frame(101)]))
        self.assertIsNone(J.parse_notifications([frame(0xE5)]))

    def test_bad_frame_and_wrong_command_are_ignored(self):
        self.assertIsNone(J.parse_notifications([
            bytes((0xAA, 0x99, 0x03, 0x00, 0x44, 0x32)),
            bytes((0xAA, 0x12, 0x02, 0x00, 0x44)),
        ]))

    def test_fragmented_notification_is_reassembled(self):
        packet = frame(0x8A)
        self.assertEqual(J.parse_notifications([packet[:2], packet[2:4], packet[4:]]),
                         (10, True))

    def test_concatenated_frames_find_battery_token(self):
        other = bytes((0xAA, 0x12, 0x03, 0x00, 0x43, 0x01))
        self.assertEqual(J.parse_notifications([other + frame(75)]), (75, False))

    def test_snapshot_status_has_device_reply_evidence(self):
        rows = [{"address": 0xAABBCCDDEEFF, "frames": [frame(0x8A).hex()]}]
        status, = J.statuses_from_snapshot(rows)
        self.assertEqual((status.name, status.level, status.charging), ("JBL Flip 7", 10, True))
        self.assertEqual((status.source, status.kind, status.via), ("jbl", "headset", "bluetooth"))
        self.assertTrue(status.charging_known)
        self.assertTrue(status.connection_known)

    def test_invalid_snapshot_identity_is_not_exposed(self):
        self.assertEqual(J.statuses_from_snapshot([
            {"address": "AA:BB:CC:DD:EE:FF", "frames": [frame(50).hex()]},
            {"address": 0x1FFFFFFFFFFFF, "frames": [frame(50).hex()]},
        ]), [])


if __name__ == "__main__":
    unittest.main()
