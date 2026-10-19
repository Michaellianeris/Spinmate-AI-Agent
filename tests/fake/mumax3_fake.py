#!/usr/bin/env python3
"""Stand-in for mumax3 in the tests: writes input.out/table.txt like mumax3 for a damped precession."""
import math, os, re, sys
script = open(sys.argv[-1]).read()
t_end = float(re.search(r"Run\(([^)]+)\)", script).group(1))
dt = float(re.search(r"TableAutoSave\(([^)]+)\)", script).group(1))
os.makedirs("input.out", exist_ok=True)
with open("input.out/table.txt", "w") as f:
    f.write("# t (s)\tmx ()\tmy ()\tmz ()\tE_total (J)\n")
    n = int(round(t_end / dt))
    for i in range(n + 1):
        t = i * dt; th = 0.5 * math.exp(-t / 2e-10); ph = 2 * math.pi * 5e9 * t
        f.write(f"{t:.6e}\t{math.sin(th)*math.cos(ph):.6f}\t{math.sin(th)*math.sin(ph):.6f}\t{math.cos(th):.6f}\t0\n")
