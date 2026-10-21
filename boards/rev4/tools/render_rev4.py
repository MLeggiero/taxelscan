"""render_rev4.py - the board renders the READMEs show, from ../rev4.kicad_pcb.

    python3 render_rev4.py

Writes, with kicad-cli's 3D renderer:

  ../../../assets/taxelscan-v4-top.png, -bottom.png   plan views with the parts (KiCad's 3D
                                                      models must be installed: the
                                                      kicad-packages3d package on Linux),
                                                      transparent background, cropped to the
                                                      board, 985 px wide
  ../pcb_top.png                                       the bare board from the top - pads, vias,
                                                      tracks and silkscreen, no parts - on
                                                      KiCad's background, 1568 x 984

Basic quality: the ray-traced setting adds a drop shadow round the board.
"""
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.normpath(os.path.join(HERE, "..", "rev4.kicad_pcb"))
ASSETS = os.path.normpath(os.path.join(HERE, "..", "..", "..", "assets"))
CLI = shutil.which("kicad-cli") or "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"


def render(board, side, out, w, h, background, zoom=1.0, env=None):
    r = subprocess.run([CLI, "pcb", "render", "--side", side, "--width", str(w), "--height", str(h),
                        "--quality", "basic", "--background", background, "--zoom", str(zoom), "-o", out, board],
                       capture_output=True, text=True, env=env)
    if r.returncode or not os.path.exists(out):
        sys.exit("render %s failed: %s" % (side, (r.stderr or r.stdout)[-300:]))


def main():
    tmp = tempfile.mkdtemp()
    for side in ("top", "bottom"):
        raw = os.path.join(tmp, side + ".png")
        render(BOARD, side, raw, 1970, 1206, "transparent")
        im = Image.open(raw)
        im = im.crop(im.getbbox())
        im = im.resize((985, round(im.size[1] * 985 / im.size[0])), Image.LANCZOS)
        dst = os.path.join(ASSETS, "taxelscan-v4-%s.png" % side)
        im.save(dst)
        print("  %s %s" % (dst, im.size))
    # the bare board: a copy away from the project, so that U9's project-relative model
    # is not found either, and the library models pointed at an empty directory
    bare = os.path.join(tmp, "bare.kicad_pcb")
    shutil.copyfile(BOARD, bare)
    os.mkdir(os.path.join(tmp, "no3d"))
    env = dict(os.environ, KICAD10_3DMODEL_DIR=os.path.join(tmp, "no3d"))
    raw = os.path.join(tmp, "bare.png")
    render(bare, "top", raw, 1600, 1016, "opaque", zoom=1.28, env=env)
    im = Image.open(raw)
    W, H = 1568, 984
    w, h = im.size                   # kicad-cli gives a little less than it is asked for
    cw, ch = (w, round(w * H / W)) if w * H <= h * W else (round(h * W / H), h)
    im = im.crop(((w - cw) // 2, (h - ch) // 2, (w - cw) // 2 + cw, (h - ch) // 2 + ch))
    dst = os.path.normpath(os.path.join(HERE, "..", "pcb_top.png"))
    im.resize((W, H), Image.LANCZOS).save(dst)
    print("  %s %s" % (dst, (W, H)))
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
