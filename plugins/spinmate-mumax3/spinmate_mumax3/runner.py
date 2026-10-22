"""Runs mumax3 in the current run folder and converts its table to the files SpinMate reads.
Called as: python -m spinmate_mumax3.runner   (cwd = run folder with parameters.txt)"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from spinmate_mumax3 import binary, script


def main():
    t0 = time.time()
    Path("input.mx3").write_text(script(Path("parameters.txt").read_text()))
    exe = binary()
    if not exe:
        sys.exit("mumax3 not found: install it or set MUMAX3_BIN")
    cmd = [sys.executable, exe] if exe.endswith(".py") else [exe]   # a .py stand-in is used by the tests
    subprocess.run(cmd + ["-f", "input.mx3"], check=True)
    table = Path("input.out") / "table.txt"
    shutil.copy(table, "table.txt")
    import numpy as np
    head = table.read_text().splitlines()[0].lstrip("#").split("\t")
    cols = [h.strip().split(" ")[0] for h in head]
    data = np.loadtxt(table, ndmin=2)
    t, m = data[:, cols.index("t")], data[:, [cols.index("mx"), cols.index("my"), cols.index("mz")]]
    # the viewer draws two sublattices; a ferromagnet shows the same <m> twice
    np.savetxt("output1.dat", np.column_stack((t, m)), fmt="%.16E")
    np.savetxt("output2.dat", np.column_stack((t, m)), fmt="%.16E")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 3.5))
    for i, c in enumerate("xyz"):
        ax.plot(t * 1e9, m[:, i], label=f"<m{c}>")
    ax.set_xlabel("time (ns)"); ax.set_ylabel("<m>"); ax.legend(); fig.tight_layout()
    fig.savefig("Two_Spin_Dynamics.png", dpi=120)
    summary = {"engine": "mumax3", "points": int(len(t)), "t_start_s": float(t[0]), "t_end_s": float(t[-1]),
               "runtime_s": round(time.time() - t0, 2), "m_initial": m[0].round(6).tolist(),
               "m_final": m[-1].round(6).tolist(), "m1_final": m[-1].round(6).tolist(), "m2_final": m[-1].round(6).tolist()}
    if "E_total" in cols:
        summary["energy_final_J"] = float(data[-1, cols.index("E_total")])
    final = Path("input.out") / "m_final.ovf"
    if final.exists():                       # SaveAs(m, "m_final"): the final texture, as SpinMate keeps it
        from spinmate_core import ovf
        mf, cell = ovf.read(final)
        np.savez_compressed("m_final.npz", m=(mf[:, 0] if mf.shape[1] == 1 else mf).astype(np.float32),
                            cell=np.array(cell))
        summary["texture_saved"] = True
    Path("summary.json").write_text(json.dumps(summary))


if __name__ == "__main__":
    main()
