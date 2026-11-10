"""INFI100 2.4G receiver battery query, based on USBPcap captures.

The receiver exposes a vendor HID collection FF60:0061 (MI_03), report ID
0x20, 32-byte interrupt OUT/IN reports. Only the captured battery request is
sent; byte 3 of a matching response is the percentage. The trailing response
byte is intentionally not interpreted.
"""
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
        info = super()._pick(infos)
        if info is not None:
            path = info.get("path")
            if isinstance(path, bytes):
                path = path.decode("utf-8", "replace")
            # The vendor collection FF60:0061 is present on more than one
            # interface. The OEM configuration identifies the wireless receiver
            # specifically as MI_03; never query the sibling collection.
            if not isinstance(path, str) or "&mi_03&" not in path.lower():
                self._diag.append("not the configured MI_03 interface: nothing sent")
                return None
            product = info.get("product_string")
            if product != PRODUCT_STRING:
                self._diag.append("unexpected product string: nothing sent")
                return None
        return info
