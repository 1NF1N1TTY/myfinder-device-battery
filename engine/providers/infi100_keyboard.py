"""INFI100 2.4G receiver battery query, based on USBPcap captures.

The receiver exposes a vendor HID collection FF60:0061 (MI_03), report ID
0x20, 32-byte interrupt OUT/IN reports. Only the captured battery request is
sent; byte 3 of a matching response is the percentage. The trailing response
byte is intentionally not interpreted.
"""
import re

from battery_hid import BatteryHidProvider

VID = 0x05AC
PIDS = {0x024F: "INFI100 2.4G 键盘"}
PRODUCT_STRING = "2.4G Wireless Keyboard"


def make_request():
    # Captured OUT report: 20 01 00, zero-filled body, final marker 21.
    return [0x20, 0x01, 0x00] + [0x00] * 28 + [0x21]


def parse_reply(reply):
    if (len(reply) != 32 or any(type(value) is not int or not 0 <= value <= 255
                                for value in reply)
            or reply[0:3] != [0x20, 0x01, 0x00]
            or not 0 <= reply[3] <= 100
            or list(reply) == make_request()):
        return None
    return reply[3], False, ""


class Infi100KeyboardProvider(BatteryHidProvider):
    name = "infi100_keyboard"
    vid, pids = VID, PIDS
    collection = (0xFF60, 0x0061)
    input_id = output_id = 0x20
    input_bounds = (32, 32)
    output_bytes = 32
    kind = "keyboard"
    verification_level = "B"  # Confirmed against the original driver and USBPcap.
    make_request = staticmethod(make_request)
    parse_reply = staticmethod(parse_reply)

    def _pick(self, infos):
        # Match the OEM-identified wireless interface before collection selection.
        # Windows HID paths can spell the MI_03 boundary with either '&' or '#'.
        mi03 = []
        for info in infos:
            path = info.get("path")
            if isinstance(path, bytes):
                path = path.decode("utf-8", "replace")
            if isinstance(path, str) and re.search(r"(?:^|[&#])mi_03(?:[&#]|$)", path, re.I):
                mi03.append(info)
        if not mi03:
            self._diag.append("configured MI_03 receiver interface not found: nothing sent")
            return None

        wireless = [info for info in mi03
                    if isinstance(info.get("product_string"), str)
                    and info["product_string"].strip().casefold() == PRODUCT_STRING.casefold()]
        if not wireless:
            self._diag.append("configured MI_03 product string not found: nothing sent")
            return None
        return super()._pick(wireless)
