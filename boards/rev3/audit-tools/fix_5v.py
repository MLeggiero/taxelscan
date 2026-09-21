"""Re-route +5V around the MCU cluster instead of under it: rip the net, then
lay D1 -> D2 along the bottom lane and D2 -> R15 up the right side."""
import sys, os
sys.argv = ["x", "noop"]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "route_fix.py")).read().split('if __name__ == "__main__":')[0])
b = K.LoadBoard(PCB); n = 0
for t in list(b.GetTracks()):
    if t.GetNetname() == "+5V": b.Delete(t); n += 1
b.Save(PCB); print("ripped", n, "+5V items")
R = Router()
MCU = (116.0, 107.5, 148.0, 128.5)      # nothing of +5V may cross the MCU / analog block
R.route("+5V", [("U12", "4")], [("C27", "1")], TOP, 0.3, label="VIN>C27")
R.route("+5V", [("C27", "1")], [("C22", "1")], TOP, 0.3, label="C27>C22")
R.route("+5V", [("U12", "1"), ("U12", "4"), ("C27", "1"), ("C22", "1")], [("D1", "1")], OUTER, 0.3, label="U12>D1")
if not R.route("+5V", [("D1", "1")], [("D2", "1")], OUTER, 0.3, keepout=MCU, label="D1>D2 (lane)"):
    R.route("+5V", [("D1", "1")], [("D2", "1")], OUTER, 0.3, label="D1>D2 free")
if not R.route("+5V", [("D2", "1")], [("R15", "1")], OUTER, 0.2, keepout=MCU, label="D2>R15 (right side)"):
    R.route("+5V", [("D2", "1")], [("R15", "1")], OUTER, 0.2, label="D2>R15 free")
R.flush()
fill_and_drc("fix5v")
