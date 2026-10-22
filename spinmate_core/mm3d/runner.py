"""Runs one mm3d relaxation in the current run folder (parameters.txt is there).
Called as: python -m spinmate_core.mm3d.runner"""
import json
import time
from pathlib import Path

import numpy as np

from spinmate_core.mm3d import parse
from spinmate_core.mm3d import solver as S


def _progress(step, total, t0):
    Path("progress.json").write_text(json.dumps({"step": int(step), "total": int(total),
                                                 "elapsed": round(time.time() - t0, 2)}))


def name_texture(met, slab):
    H, bps = met["hopf_index"], met["bloch_points"]
    tube = [abs(q) > 0.5 for q in met["Q_layers"]]
    if len(bps) == 1 and any(tube):
        return "chiral_bobber"                     # a tube from one surface that ends at a Bloch point
    if len(bps) == 2 and any(tube) and not (tube[0] or tube[-1]):
        return "toron"                             # a tube closed at both ends by Bloch points
    if bps:
        return "bloch_points"
    if abs(H) > 1.4:
        return "hopfion2"
    if abs(H) > 0.5:
        return "hopfion"
    if met["min_mz"] > 0.2:
        return "uniform"
    return "skyrmion_tube" if met["rings_nm"] else "other"


def main():
    t0 = time.time()
    vals = parse(Path("parameters.txt").read_text())
    slab = S.Slab(vals["Nx"], vals["Ny"], vals["Nz"], vals["dx"], vals["Msat"], vals["Aex"], vals["Ku"], vals["D"],
                  dmi=vals["dmi"], B=vals["B_ext"], boundary="periodic" if vals["geometry"] == "slab" else "free",
                  surfaces=vals["surfaces"], disk=vals["disk_diameter"] if vals["geometry"] == "disk" else 0.0)
    m = S.initial(slab, vals["texture"], R=vals["R0"] or None, seed=vals["seed"])
    if slab.mask is not None:
        m = np.where(slab.mask, m, np.array([0.0, 0.0, 1.0])[:, None, None, None])
    p = 4 * np.pi * vals["Aex"] / abs(vals["D"]) if vals["D"] else None
    summary = {"engine": "mm3d", "mode": "relax", "texture_seed": vals["texture"],
               "grid": [vals["Nx"], vals["Ny"], vals["Nz"]], "cell_nm": vals["dx"] * 1e9,
               "helical_period_nm": round(p * 1e9, 2) if p else None, "geometry": vals["geometry"],
               "energy_initial_J": slab.energy(m), "hopf_index_initial": round(S.hopf_index(m), 3)}
    rows, erows = [], []

    def avg(mm):
        return mm.mean((1, 2, 3)) if slab.mask is None else mm[:, slab.mask].mean(1)

    def cb(it, torque, mm):
        rows.append((it, *avg(mm)))
        erows.append((it, slab.energy(mm), torque))
        if it % 100 == 0:
            _progress(it, vals["max_iter"], t0)
    m, it, torque = S.minimise(slab, m, tol=vals["tol"], max_iter=vals["max_iter"], callback=cb)
    _progress(1, 1, t0)
    met = S.metrics(slab, m)
    bps = S.bloch_points(slab, m)
    met["bloch_points"] = [[round(a * 1e9, 2) + 0.0, round(b * 1e9, 2) + 0.0, round(c * 1e9, 2) + 0.0, q] for a, b, c, q in bps]
    met["bloch_point_count"] = len(bps)
    mm = m if slab.mask is None else np.where(slab.mask, m, np.array([0.0, 0.0, 1.0])[:, None, None, None])
    met["Q_layers"] = [round(S.skyrmion_number_z(mm, k), 3) + 0.0 for k in range(slab.Nz)]
    summary.update(relax_iterations=it, relax_torque_A_per_m=round(torque, 4), relaxed=bool(torque < vals["tol"]),
                   energy_final_J=slab.energy(m), m_final=[round(float(v), 6) for v in avg(m)],
                   runtime_s=round(time.time() - t0, 2), **met)
    summary["texture"] = name_texture(met, slab)
    summary["m1_final"] = summary["m2_final"] = summary["m_final"]
    summary["topological_charge"] = summary["hopf_index"]

    rows = np.array(rows) if rows else np.array([[0, *avg(m)]])
    np.savetxt("output1.dat", rows, fmt="%.10E")
    np.savetxt("output2.dat", rows, fmt="%.10E")
    if erows:
        np.savetxt("energy.dat", np.array(erows), fmt="%.10E")
    np.savez_compressed("m_final.npz", m=m.astype(np.float32), x=slab.x, y=slab.y, z=slab.z,
                        mask=slab.mask if slab.mask is not None else np.ones(m.shape[1:], bool))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from spinmate_core.mm3d import render
    fig, ax = plt.subplots(figsize=(6, 3.5))
    for i, c in enumerate("xyz"):
        ax.plot(rows[:, 0], rows[:, i + 1], label=f"<m{c}>")
    ax.set_xlabel("relaxation iteration"); ax.set_ylabel("<m>"); ax.legend(); fig.tight_layout()
    fig.savefig("Two_Spin_Dynamics.png", dpi=120); plt.close(fig)
    geo = (f"disk {vals['disk_diameter'] * 1e9:.3g} nm × {vals['Nz'] * vals['dx'] * 1e9:.3g} nm"
           if vals["geometry"] == "disk" else f"slab {vals['Nz'] * vals['dx'] * 1e9:.3g} nm thick")
    what = f"Hopf index H = {met['hopf_index']:+.2f}"
    if bps:
        what = f"{len(bps)} Bloch point{'s' if len(bps) > 1 else ''} (red +1, blue −1)"
    label = {"hopfion2": "two stacked hopfions", "bloch_points": "Bloch points"}.get(summary["texture"],
                                                                                      summary["texture"].replace("_", " "))
    cap = (f"{label} · {what} · surface m_z = 0, "
           f"coloured by in-plane direction · {geo}" + (f", p = {p * 1e9:.3g} nm" if p else "")
           + f" · B = {vals['B_ext'][2]:.3g} T")
    render.save_views(m, slab.x * 1e9, slab.y * 1e9, slab.z * 1e9, cap, slab.mask,
                      points=[(a * 1e9, b * 1e9, c * 1e9, q) for a, b, c, q in bps])
    if vals["animation"]:
        summary["animation_files"] = render.save_rotation(
            m, slab.x * 1e9, slab.y * 1e9, slab.z * 1e9, cap, slab.mask,
            points=[(a * 1e9, b * 1e9, c * 1e9, q) for a, b, c, q in bps])
    Path("summary.json").write_text(json.dumps(summary))


if __name__ == "__main__":
    main()
