"""strapgeo.py - hand-laid escapes for U9's right-column pins 32-35 (ADDR0, ADDR1,
ADDR2, USB_PWR_FAULT), for use on top of t28's base (stage-3 copper for the ADC nets,
STATUS, USB_CC_OUT1/2 and USB_BUS_EN).

Between USB_BUS_EN's escape (pin 36) and ADC_SDI/ADC_SCK (pins 31/29) the four pins
have three natural exits: the channel under U9 between the thermal pad and the bottom
pin row (one 0.1 mm track fits north of ADC_SDO's ring via), the via site just east
of the column, and the F.Cu lane east between USB_BUS_EN (y 122.3) and ADC_SCK
(y 123.15). Moving ADC_SDO's ring via south-west to (128.30, 123.23) - 0.66 mm from
ADC_SCK's via, its drill 0.25 mm from the channel's lower track - opens the channel
to two tracks, which makes four:

  ADDR2          inner tip -> channel, upper track y 122.575 -> via (126.70, 122.90)
  ADDR0          inner tip -> channel, lower track y 122.78  -> via (127.50, 123.10)
  ADDR1          outer tip -> east lane y 122.68 (clear of ADC_SCK's drill), y 122.80 past
                 USB_BUS_EN's via -> via (136.75, 122.40), north of USB_BUS_EN's B.Cu diagonal
  USB_PWR_FAULT  outer tip -> via (130.75, 122.10)

W = 0.1 mm tracks inside U9_escape (0.1 mm clearance there), vias 0.5/0.3."""

W = 0.1
VIA = (0.5, 0.3)

SDO_REMOVE = {  # stage-3 ADC_SDO copper replaced by the moved ring via
    "via": [(128.4, 123.075)],
    "track": [("F", (128.4, 123.075), (128.15, 123.825)), ("In2", (128.8, 121.85), (128.4, 123.075))],
}


def geometry():
    segs = [
        # ADC_SDO ring via moved
        ("ADC_SDO", "F", (128.30, 123.23), (128.15, 123.85)),
        ("ADC_SDO", "In2", (128.80, 121.85), (128.30, 123.23)),
        # ADDR2: pin 34 inner tip, channel upper track
        ("ADDR2", "F", (129.95, 122.32), (129.40, 122.32)),
        ("ADDR2", "F", (129.40, 122.32), (129.145, 122.575)),
        ("ADDR2", "F", (129.145, 122.575), (126.90, 122.575)),
        ("ADDR2", "F", (126.90, 122.575), (126.70, 122.90)),
        # ADDR0: pin 32 inner tip, channel lower track
        ("ADDR0", "F", (129.95, 123.12), (129.40, 123.12)),
        ("ADDR0", "F", (129.40, 123.12), (129.06, 122.78)),
        ("ADDR0", "F", (129.06, 122.78), (127.70, 122.78)),
        ("ADDR0", "F", (127.70, 122.78), (127.50, 123.10)),
        # ADDR1: pin 33 outer tip, east lane
        ("ADDR1", "F", (129.95, 122.72), (130.40, 122.68)),
        ("ADDR1", "F", (130.40, 122.68), (134.86, 122.68)),
        ("ADDR1", "F", (134.86, 122.68), (134.98, 122.80)),
        ("ADDR1", "F", (134.98, 122.80), (136.45, 122.80)),
        ("ADDR1", "F", (136.45, 122.80), (136.75, 122.40)),
        # USB_PWR_FAULT: pin 35 outer tip, via east of the column
        ("USB_PWR_FAULT", "F", (129.95, 121.92), (130.50, 121.92)),
        ("USB_PWR_FAULT", "F", (130.50, 121.92), (130.75, 122.10)),
    ]
    vias = [
        ("ADC_SDO", (128.30, 123.23)),
        ("ADDR2", (126.70, 122.90)),
        ("ADDR0", (127.50, 123.10)),
        ("ADDR1", (136.75, 122.40)),
        ("USB_PWR_FAULT", (130.75, 122.10)),
    ]
    return segs, vias
