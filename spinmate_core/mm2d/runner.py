"""Runs one mm2d simulation in the current run folder (parameters.txt is there).
Called as: python -m spinmate_core.mm2d.runner"""
import json
import time
from pathlib import Path

import numpy as np

from spinmate_core.mm2d import parse
from spinmate_core.mm2d import solver as S


def _progress(step, total, t0):
    Path("progress.json").write_text(json.dumps({"step": int(step), "total": int(total),
                                                 "elapsed": round(time.time() - t0, 2)}))


def main():
    t0 = time.time()
    vals = parse(Path("parameters.txt").read_text())
    film = S.Film(vals["Nx"], vals["Ny"], vals["dx"], vals["Msat"], vals["Aex"], vals["Ku"], vals["D"],
                  dmi=vals["dmi"], boundary=vals["boundary"], B=vals["B_ext"])
    d = vals["thickness"]
    m = S.initial(film, vals["texture"], R0=vals["R0"], m0=vals["m0"], seed=vals["seed"])
    summary = {"engine": "mm2d", "mode": vals["mode"], "texture_seed": vals["texture"],
               "grid": [vals["Nx"], vals["Ny"]], "cell_nm": vals["dx"] * 1e9,
               "energy_initial_J": film.energy(m, d)}
    rows = []                                  # (x, mx, my, mz) for output1.dat
    erows = []                                 # energy log
    relaxed = vals["mode"] == "relax" or vals["relax_first"]
    if relaxed:
        total = vals["max_iter"] if vals["mode"] == "relax" else vals["max_iter"] + int(vals["t"] / vals["table_dt"])

        def cb(it, torque, mm):
            rows.append((it, *mm.mean((1, 2))))
            erows.append((it, film.energy(mm, d), torque))
            if it % 200 == 0:
                _progress(it, total, t0)
        m, it, torque = S.minimise(film, m, tol=vals["tol"], max_iter=vals["max_iter"], callback=cb)
        summary.update(relax_iterations=it, relax_torque_A_per_m=round(torque, 4),
                       relaxed=bool(torque < vals["tol"]))
    if vals["mode"] == "dynamics":
        rows, erows = [], []
        n_out = int(round(vals["t"] / vals["table_dt"]))
        start = summary.get("relax_iterations", 0)
        pulse = np.asarray(vals["B_pulse"], float).reshape(3, 1, 1) / S.MU0
        base = film.H_ext.copy()
        t, dt, steps = 0.0, 0.1 / film.max_freq(), 0
        rows.append((0.0, *m.mean((1, 2)))); erows.append((0.0, film.energy(m, d)))
        for k in range(1, n_out + 1):
            t_target = k * vals["table_dt"]
            while t < t_target * (1 - 1e-12):
                on = t < vals["t_pulse"]
                film.H_ext = base + pulse if on else base
                h = min(dt, t_target - t)
                if on and t + h > vals["t_pulse"]:
                    h = vals["t_pulse"] - t        # end the pulse exactly
                m, used, dt = S.rk45_step(film, m, h, vals["alpha"], tol=vals["rk_tol"])
                t += used; steps += 1
            film.H_ext = base
            rows.append((t, *m.mean((1, 2)))); erows.append((t, film.energy(m, d)))
            _progress(start + k, start + n_out, t0)
        summary.update(dynamics_steps=steps, t_end_s=t, points=len(rows))
        mz = np.array([r[3] for r in rows])
        tt = np.array([r[0] for r in rows])
        sel = tt >= vals["t_pulse"]
        if sel.sum() > 16:                          # main frequency of <mz> and <mx> after the pulse
            for comp, idx in (("mx", 1), ("mz", 3)):
                sig = np.array([r[idx] for r in rows])[sel]
                sig = sig - sig.mean()
                spec = np.abs(np.fft.rfft(sig * np.hanning(len(sig))))
                f = np.fft.rfftfreq(len(sig), vals["table_dt"])
                if spec[1:].max() > 1e-9:
                    summary[f"peak_frequency_{comp}_GHz"] = round(float(f[1 + spec[1:].argmax()]) / 1e9, 3)
    _progress(1, 1, t0)

    rows = np.array(rows) if rows else np.array([[0.0, *m.mean((1, 2))]])
    np.savetxt("output1.dat", rows, fmt="%.10E")
    np.savetxt("output2.dat", rows, fmt="%.10E")    # the viewer draws two sublattices; a ferromagnet has one
    if erows:
        np.savetxt("energy.dat", np.array(erows), fmt="%.10E")
    np.savez_compressed("m_final.npz", m=m.astype(np.float32), x=film.x, y=film.y)

    Q = round(S.topological_charge(m), 4) + 0.0      # no -0.00
    metrics = S.texture_metrics(film, m)
    summary.update(topological_charge=round(Q, 4), energy_final_J=film.energy(m, d),
                   m_final=[round(float(v), 6) for v in m.mean((1, 2))], runtime_s=round(time.time() - t0, 2),
                   **{k: v for k, v in metrics.items()})
    summary["m1_final"] = summary["m2_final"] = summary["m_final"]
    summary["texture"] = name_texture(Q, metrics)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from spinmate_core.mm2d import render
    fig, ax = plt.subplots(figsize=(6, 3.5))
    xs = rows[:, 0] * (1e9 if vals["mode"] == "dynamics" else 1)
    for i, c in enumerate("xyz"):
        ax.plot(xs, rows[:, i + 1], label=f"<m{c}>")
    ax.set_xlabel("time (ns)" if vals["mode"] == "dynamics" else "relaxation iteration")
    ax.set_ylabel("<m>"); ax.legend(); fig.tight_layout(); fig.savefig("Two_Spin_Dynamics.png", dpi=120)
    plt.close(fig)

    x_nm, y_nm = film.x * 1e9, film.y * 1e9
    half = None
    if summary["texture"] in ("skyrmion", "skyrmionium", "antiskyrmion") and metrics.get("rings_nm"):
        half = 1.6 * max(metrics["rings_nm"]) + 6
    mc, xc, yc = render.crop(m, x_nm, y_nm, metrics.get("centre_nm"), half)
    dmi = f"D = {vals['D'] * 1e3:.3g} mJ/m² ({vals['dmi']})" if vals["D"] else "no DMI"
    caption = (f"{summary['texture'].replace('_', ' ')} · Q = {Q:+.2f} · A = {vals['Aex'] * 1e12:.3g} pJ/m, {dmi}, "
               f"Ku = {vals['Ku'] / 1e6:.3g} MJ/m³, Ms = {vals['Msat'] / 1e3:.4g} kA/m · {vals['dx'] * 1e9:.3g} nm cells")
    render.save_views(mc, xc, yc, caption, round_view=half is not None)
    Path("summary.json").write_text(json.dumps(summary))


def name_texture(Q, metrics):
    rings = metrics.get("rings_nm") or []
    q = round(Q)
    if abs(Q - q) > 0.15:
        return "unsettled"
    if not metrics.get("reversed_area_nm2"):
        return "uniform"
    if metrics.get("crosses_film"):
        return "domains"
    if q in (1, -1) and len(rings) == 1:
        return "skyrmion"
    if q == 0 and len(rings) == 2:
        return "skyrmionium"
    return f"Q={q}"


if __name__ == "__main__":
    main()
