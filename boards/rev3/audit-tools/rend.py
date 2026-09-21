"""Render a window of a board dump (from insp.py) layer by layer.

  python.exe rend.py dump.json out.png x0 y0 x1 y1 [px_per_mm] [layers] [hilite nets, comma sep]

layers: any of F,In2,B (default "F,In2,B"); pads on those layers are drawn,
tracks on those layers are drawn in layer colours, vias always.
"""
import sys, json, math
sys.path.insert(0, "C:/Users/mleggiero/Documents/KiCad/taxelscan/tmp/usb-power-deps")
from PIL import Image, ImageDraw, ImageFont

d = json.load(open(sys.argv[1]))
out = sys.argv[2]
x0, y0, x1, y1 = map(float, sys.argv[3:7])
S = float(sys.argv[7]) if len(sys.argv) > 7 else 80
LAY = sys.argv[8].split(",") if len(sys.argv) > 8 else ["F", "In2", "B"]
HL = set(sys.argv[9].split(",")) if len(sys.argv) > 9 and sys.argv[9] else set()
im = Image.new("RGB", (int((x1 - x0) * S), int((y1 - y0) * S)), "white")
dr = ImageDraw.Draw(im, "RGBA")
try:
    font = ImageFont.load_default(size=max(9, int(S * 0.13)))
    small = ImageFont.load_default(size=max(8, int(S * 0.09)))
except Exception:
    font = small = ImageFont.load_default()


def P(x, y):
    return ((x - x0) * S, (y - y0) * S)


# grid
g = 0.5
k = math.floor(x0 / g)
while k * g <= x1:
    X = k * g
    dr.line((P(X, y0), P(X, y1)), fill=(225, 225, 225) if abs(X - round(X)) > 1e-6 else (190, 190, 190), width=1)
    if abs(X - round(X)) < 1e-6:
        dr.text((P(X, y0)[0] + 2, 2), "%g" % X, fill=(120, 120, 120), font=small)
    k += 1
k = math.floor(y0 / g)
while k * g <= y1:
    Y = k * g
    dr.line((P(x0, Y), P(x1, Y)), fill=(225, 225, 225) if abs(Y - round(Y)) > 1e-6 else (190, 190, 190), width=1)
    if abs(Y - round(Y)) < 1e-6:
        dr.text((2, P(x0, Y)[1] + 2), "%g" % Y, fill=(120, 120, 120), font=small)
    k += 1

# rule areas
for z in d["zones"]:
    if z["rule"]:
        for ch in z["outline"]:
            dr.line([P(*p) for p in ch + ch[:1]], fill=(160, 0, 160, 200), width=2)

COL = {"F": (215, 40, 40, 170), "In2": (40, 160, 40, 150), "B": (40, 80, 220, 150)}
# pads
for p in d["pads"]:
    on = [l for l in p["lay"] if l in LAY]
    if not on:
        continue
    if not (x0 - 3 < p["x"] < x1 + 3 and y0 - 3 < p["y"] < y1 + 3):
        continue
    fill = (120, 120, 120, 170) if p["net"] != "GND" else (170, 170, 170, 170)
    if p["net"] in HL:
        fill = (255, 170, 0, 200)
    if p["poly"]:
        for ch in p["poly"]:
            dr.polygon([P(*q) for q in ch], fill=fill, outline=(60, 60, 60))
    else:
        dr.rectangle((P(p["x"] - p["sx"] / 2, p["y"] - p["sy"] / 2), P(p["x"] + p["sx"] / 2, p["y"] + p["sy"] / 2)), fill=fill)
    if p["drill"]:
        r = p["drill"] / 2
        dr.ellipse((P(p["x"] - r, p["y"] - r), P(p["x"] + r, p["y"] + r)), fill=(255, 255, 255))
    lab = "%s.%s" % (p["ref"], p["num"])
    dr.text(P(p["x"], p["y"]), lab, fill=(0, 0, 0), font=small, anchor="mm")
    if S >= 60 and p["net"]:
        dr.text((P(p["x"], p["y"])[0], P(p["x"], p["y"])[1] + S * 0.11), p["net"][:12], fill=(0, 0, 90), font=small, anchor="mm")

# tracks, back to front
for lay in ("B", "In2", "F"):
    if lay not in LAY:
        continue
    for t in d["tracks"]:
        if t["lay"] != lay:
            continue
        if max(t["x1"], t["x2"]) < x0 - 1 or min(t["x1"], t["x2"]) > x1 + 1 or max(t["y1"], t["y2"]) < y0 - 1 or min(t["y1"], t["y2"]) > y1 + 1:
            continue
        c = COL[lay]
        if t["net"] in HL:
            c = (255, 140, 0, 230) if lay == "F" else (200, 0, 200, 220)
        w = max(1, int(round(t["w"] * S)))
        dr.line((P(t["x1"], t["y1"]), P(t["x2"], t["y2"])), fill=c, width=w)
        r = t["w"] / 2
        for x, y in ((t["x1"], t["y1"]), (t["x2"], t["y2"])):
            dr.ellipse((P(x - r, y - r), P(x + r, y + r)), fill=c)
# vias
for v in d["vias"]:
    if not (x0 - 1 < v["x"] < x1 + 1 and y0 - 1 < v["y"] < y1 + 1):
        continue
    r = v["size"] / 2
    c = (0, 0, 0, 200) if v["net"] not in HL else (255, 120, 0, 255)
    dr.ellipse((P(v["x"] - r, v["y"] - r), P(v["x"] + r, v["y"] + r)), outline=c, width=max(2, int(S * 0.03)), fill=(255, 255, 255, 120))
    rr = v["drill"] / 2
    dr.ellipse((P(v["x"] - rr, v["y"] - rr), P(v["x"] + rr, v["y"] + rr)), fill=(80, 80, 80, 160))
    if S >= 60:
        dr.text(P(v["x"], v["y"] + r + 0.06), v["net"][:10], fill=(0, 0, 0), font=small, anchor="mt")
# net labels on longer segments
for t in d["tracks"]:
    if t["lay"] not in LAY:
        continue
    L = math.hypot(t["x2"] - t["x1"], t["y2"] - t["y1"])
    if L * S > 70:
        x, y = (t["x1"] + t["x2"]) / 2, (t["y1"] + t["y2"]) / 2
        if x0 < x < x1 and y0 < y < y1:
            dr.text(P(x, y), t["net"][:12] + ("" if t["lay"] == "F" else "/" + t["lay"]), fill=(0, 0, 0), font=small, anchor="mm")
im.save(out)
print("saved", out, im.size)
