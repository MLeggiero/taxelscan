import sys, os
sys.path.insert(0, "C:/Users/mleggiero/Documents/KiCad/taxelscan/tmp/usb-power-deps")
HERE = "C:/Users/mleggiero/Documents/KiCad/taxelscan/boards/rev3"
os.chdir(HERE); sys.path.insert(0, HERE)
sys.argv = ["maze_route.py"] + sys.argv[1:]
g = {"__name__": "__main__", "__file__": HERE + "/maze_route.py"}
exec(compile(open(HERE + "/maze_route.py", encoding="utf-8").read(), HERE + "/maze_route.py", "exec"), g)
