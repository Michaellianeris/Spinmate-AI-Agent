#!/usr/bin/env python3
"""Stand-in for mumax3 in the tests.

A plain script (no m.LoadFile): writes input.out/table.txt like mumax3 for a damped precession.
A cross-check script from SpinMate (m.LoadFile, Minimize): reads the problem back from the .mx3 lines and relaxes it
with SpinMate's own solver, so the whole chain (script, OVF in and out, table, comparison) is exercised. It is not
mumax3: the agreement it gives only checks the plumbing."""
import math, os, re, sys
script = open(sys.argv[-1]).read()
os.makedirs("input.out", exist_ok=True)
if "LoadFile" not in script:
    t_end = float(re.search(r"Run\(([^)]+)\)", script).group(1))
    dt = float(re.search(r"TableAutoSave\(([^)]+)\)", script).group(1))
    with open("input.out/table.txt", "w") as f:
        f.write("# t (s)\tmx ()\tmy ()\tmz ()\tE_total (J)\n")
        n = int(round(t_end / dt))
        for i in range(n + 1):
            t = i * dt; th = 0.5 * math.exp(-t / 2e-10); ph = 2 * math.pi * 5e9 * t
            f.write(f"{t:.6e}\t{math.sin(th)*math.cos(ph):.6f}\t{math.sin(th)*math.sin(ph):.6f}\t{math.cos(th):.6f}\t0\n")
    sys.exit(0)

import numpy as np
from spinmate_core import ovf
from spinmate_core.mm2d import geometry, solver as F
num = lambda key: float(re.search(rf"^{key}\s*=\s*(\S+)", script, re.M).group(1))
nx, ny, _ = (int(v) for v in re.search(r"SetGridSize\((\d+), (\d+), (\d+)\)", script).groups())
dx, _, dz = (float(v) for v in re.search(r"SetCellSize\(([^,]+), ([^,]+), ([^)]+)\)", script).groups())
dmi = "interfacial" if re.search(r"^Dind", script, re.M) else "bulk"
D = num("Dind" if dmi == "interfacial" else "Dbulk")
B = [float(v) for v in re.search(r"^B_ext = vector\(([^)]+)\)", script, re.M).group(1).split(",")]
film = F.Film(nx, ny, dx, num("Msat"), num("Aex"), num("Ku1"), D, dmi=dmi, demag="none",
              boundary="periodic" if "SetPBC" in script else "free", B=B, thickness=dz)
shape = re.search(r'ImageShape\("([^"]+)"\)', script)
if shape:
    film.set_mask(geometry.from_png(shape.group(1), nx, ny))
m0, _ = ovf.read(re.search(r'm.LoadFile\("([^"]+)"\)', script).group(1))
m, _, _ = F.minimise(film, m0[:, 0], tol=1.0)
ovf.write("input.out/m_final.ovf", m, dx, dx, dz)
sel = film.mask if film.mask is not None else np.ones((ny, nx), bool)
mm = m[:, sel].mean(1)
with open("input.out/table.txt", "w") as f:
    f.write("# t (s)\tmx ()\tmy ()\tmz ()\tE_total (J)\n")
    f.write(f"0\t{mm[0]:.8f}\t{mm[1]:.8f}\t{mm[2]:.8f}\t{film.energy(m, dz):.10e}\n")
