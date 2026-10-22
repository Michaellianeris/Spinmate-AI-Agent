"""Cross-check with mumax3: the same mm2d input on both engines, from the same starting state, and a comparison of
the results (texture, topological charge, radius, energy, <m>(t)).

The mm2d parameters are turned into an .mx3 script: the same grid, cells, material, field and DMI; the local
thin-film demag of mm2d becomes no demag in mumax3 with Ku1 = Ku - mu0 Ms^2/2 (the same energy, exactly);
demag=full keeps the demag of mumax3 (with periodic images for a periodic film); a mask becomes ImageShape."""
import json
import time
from pathlib import Path

import numpy as np

from . import engines, ovf, simulations as S

COMPARE = S.WORKSPACE / "compare"
COMPARE.mkdir(parents=True, exist_ok=True)
MU0 = 4e-7 * np.pi
# what counts as the same result
TOL = {"charge": 0.1, "radius_rel": 0.05, "energy_rel": 0.02, "angle_rms_deg": 5.0, "m_t": 0.02}


def problems(vals):
    """Why this mm2d input cannot be run the same way in mumax3, or []."""
    out = []
    if vals["lattice"] != "ferro":
        out.append("lattice=afm/saf (mumax3 has one magnetisation per cell here)")
    if any(vals["j"]):
        out.append("currents (j): the torques are set up differently in mumax3")
    if vals["Temp"] > 0:
        out.append("temperature: thermal runs differ by their random numbers")
    if vals["excite"] != "uniform" or vals["pulse"] != "step":
        out.append("antennas and shaped pulses (excite, pulse)")
    if vals["absorb"] > 0:
        out.append("absorbing edges (absorb)")
    if vals["start_from"]:
        out.append("start_from (start from a seed instead)")
    return out


def mx3(vals, m0_path, mask_path=None):
    """The .mx3 script of an mm2d input."""
    v3 = lambda v: f"vector({float(v[0])!r}, {float(v[1])!r}, {float(v[2])!r})"
    Ku = vals["Ku"] - (0.5 * MU0 * vals["Msat"] ** 2 if vals["demag"] == "local" else 0.0)
    lines = ["// written by SpinMate: the same problem as an mm2d run, for a cross-check",
             f"SetGridSize({vals['Nx']}, {vals['Ny']}, 1)",
             f"SetCellSize({vals['dx']!r}, {vals['dx']!r}, {vals['thickness']!r})"]
    if vals["boundary"] == "periodic":
        lines.append("SetPBC(8, 8, 0)" if vals["demag"] == "full" else "SetPBC(1, 1, 0)")
    if mask_path:
        lines.append(f'SetGeom(ImageShape("{mask_path}"))')
    lines += [f"Msat = {vals['Msat']!r}", f"Aex = {vals['Aex']!r}", f"Ku1 = {Ku!r}", "AnisU = vector(0, 0, 1)",
              f"{'Dind' if vals['dmi'] == 'interfacial' else 'Dbulk'} = {vals['D']!r}",
              f"EnableDemag = {'true' if vals['demag'] == 'full' else 'false'}",
              f"B_ext = {v3(vals['B_ext'])}", f"alpha = {vals['alpha']!r}",
              f'm.LoadFile("{m0_path}")', "OutputFormat = OVF2_TEXT", "TableAdd(E_total)"]
    if vals["mode"] == "relax":
        lines += ["TableAutoSave(0)", "MinimizerStop = 1e-7", "Minimize()", "TableSave()"]
    else:
        if vals["relax_first"]:
            lines += ["Minimize()"]
        lines += ["SetSolver(5)", f"MaxErr = {vals['rk_tol']!r}", f"TableAutoSave({vals['table_dt']!r})"]
        if vals["t_pulse"] > 0 and any(vals["B_pulse"]):
            on = [a + b for a, b in zip(vals["B_ext"], vals["B_pulse"])]
            lines += [f"B_ext = {v3(on)}", f"Run({min(vals['t_pulse'], vals['t'])!r})", f"B_ext = {v3(vals['B_ext'])}"]
            if vals["t"] > vals["t_pulse"]:
                lines.append(f"Run({vals['t'] - vals['t_pulse']:.9g})")
        else:
            lines.append(f"Run({vals['t']!r})")
    lines.append('SaveAs(m, "m_final")')
    return "\n".join(lines) + "\n"


def start(parameters_text, label=""):
    """Starts the mm2d run and the mumax3 run of the same input. Returns ids or an error."""
    from .mm2d import check, parse
    from .mm2d.runner import build
    try:
        vals = parse(parameters_text)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    errors, _ = check(vals)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}
    why = problems(vals)
    if why:
        return {"ok": False, "error": "this input cannot be cross-checked with mumax3: " + "; ".join(why)}
    try:
        ready, note = engines.get("mumax3").ready()
    except KeyError:
        ready, note = False, "the mumax3 plug-in is not installed (pip install -e plugins/spinmate-mumax3)"
    if not ready:
        return {"ok": False, "error": note}
    cid = S._next_id(COMPARE, "c")
    folder = COMPARE / cid
    film, m0 = build(vals, keep_mask_png=False)          # the very same starting state for both engines
    ovf.write(folder / "m0.ovf", m0, vals["dx"], vals["dx"], vals["thickness"])
    mask = None
    if film.mask is not None:
        from PIL import Image
        mask = folder / "mask.png"
        Image.fromarray(np.where(film.mask[::-1], 0, 255).astype(np.uint8)).save(mask)
    script = mx3(vals, (folder / "m0.ovf").resolve(), mask.resolve() if mask else None)
    (folder / "input.mx3").write_text(script)
    a = S.start_run(parameters_text, label=f"{label or cid} · mm2d", engine="mm2d")
    if not a["ok"]:
        return a
    b = S.start_run(script, label=f"{label or cid} · mumax3", engine="mumax3")
    if not b["ok"]:
        S.cancel(a["run_id"])
        return b
    S._write(folder / "meta.json", {"id": cid, "label": label, "mm2d_run": a["run_id"], "mumax3_run": b["run_id"],
                                    "mode": vals["mode"], "created": time.time()})
    return {"ok": True, "compare_id": cid, "run_ids": [a["run_id"], b["run_id"]]}


def _metrics(m, vals, mask):
    from .mm2d import solver as F
    film = F.Film(vals["Nx"], vals["Ny"], vals["dx"], vals["Msat"], vals["Aex"], vals["Ku"], vals["D"],
                  dmi=vals["dmi"], boundary=vals["boundary"])
    if mask is not None:
        film.set_mask(mask)
        m = np.where(mask, m, np.array([0, 0, 1.0])[:, None, None])
    met = F.texture_metrics(film, m)
    return {"topological_charge": round(F.topological_charge(m), 3), "radius_nm": round(met.get("radius_nm", 0.0), 3),
            "m_mean": [round(float(c), 5) for c in (m[:, mask] if mask is not None else m.reshape(3, -1)).mean(1)]}


def result(cid, wait_s=0.0):
    """Status of a cross-check and, when both runs are done, the comparison."""
    folder = COMPARE / str(cid)
    meta = S._read(folder / "meta.json") if S._valid(cid, "c") else None
    if not meta:
        return None
    end = time.time() + max(0.0, min(float(wait_s), 120.0))
    while True:
        ra, rb = S.run_info(meta["mm2d_run"]), S.run_info(meta["mumax3_run"])
        busy = any(r and r["status"] in ("queued", "running") for r in (ra, rb))
        if not busy or time.time() >= end:
            break
        time.sleep(1.0)
    out = dict(meta, compare_id=meta["id"], status={"mm2d": ra and ra["status"], "mumax3": rb and rb["status"]})
    if busy or not (ra and rb) or ra["status"] != "done" or rb["status"] != "done":
        if not busy:
            out["error"] = "a run did not finish: see its run card"
        return out
    done = folder / "result.json"
    if done.exists():
        return dict(out, **json.loads(done.read_text()))
    out.update(_compare(meta, ra, rb, folder))
    done.write_text(json.dumps({k: out[k] for k in ("checks", "agree", "mm2d", "mumax3", "plot") if k in out}))
    return out


def _compare(meta, ra, rb, folder):
    from .mm2d import parse
    vals = parse((S.RUNS / meta["mm2d_run"] / "parameters.txt").read_text())
    ma = np.load(S.RUNS / meta["mm2d_run"] / "m_final.npz")["m"].astype(float)
    fb = S.RUNS / meta["mumax3_run"] / "m_final.npz"
    if not fb.exists():
        return {"error": "the mumax3 run has no final texture (m_final.ovf was not written)"}
    mb = np.load(fb)["m"].astype(float)
    mb = mb[:, 0] if mb.ndim == 4 else mb
    mask = None
    if (S.RUNS / meta["mm2d_run"] / "mask.png").exists():
        from .mm2d import geometry
        mask = geometry.from_png(S.RUNS / meta["mm2d_run"] / "mask.png", vals["Nx"], vals["Ny"])
    A, B = _metrics(ma, vals, mask), _metrics(mb, vals, mask)
    A["energy_J"] = (ra.get("summary") or {}).get("energy_final_J")
    B["energy_J"] = (rb.get("summary") or {}).get("energy_final_J")
    cosang = np.clip(np.sum(ma * mb, 0), -1, 1)
    ang = np.degrees(np.arccos(cosang))
    sel = mask if mask is not None else np.ones(ang.shape, bool)
    checks = []

    def add(name, a, b, diff, tol, unit=""):
        checks.append({"quantity": name, "mm2d": a, "mumax3": b, "difference": round(float(diff), 4),
                       "tolerance": tol, "unit": unit, "ok": bool(diff <= tol)})
    add("topological charge Q", A["topological_charge"], B["topological_charge"],
        abs(A["topological_charge"] - B["topological_charge"]), TOL["charge"])
    if A["radius_nm"] > 0 or B["radius_nm"] > 0:
        add("radius (nm)", A["radius_nm"], B["radius_nm"],
            abs(A["radius_nm"] - B["radius_nm"]) / max(A["radius_nm"], B["radius_nm"]), TOL["radius_rel"], "relative")
    if A["energy_J"] is not None and B["energy_J"] is not None and (A["energy_J"] or B["energy_J"]):
        add("energy (J)", A["energy_J"], B["energy_J"], abs(A["energy_J"] - B["energy_J"]) /
            max(abs(A["energy_J"]), abs(B["energy_J"])), TOL["energy_rel"], "relative")
    add("angle between the textures, rms (deg)", None, None, float(np.sqrt(np.mean(ang[sel] ** 2))), TOL["angle_rms_deg"], "deg")
    if meta["mode"] == "dynamics":
        da = np.loadtxt(S.RUNS / meta["mm2d_run"] / "output1.dat", ndmin=2)
        db = np.loadtxt(S.RUNS / meta["mumax3_run"] / "output1.dat", ndmin=2)
        t = da[:, 0][(da[:, 0] >= db[0, 0]) & (da[:, 0] <= db[-1, 0])]
        diff = max(float(np.max(np.abs(np.interp(t, da[:, 0], da[:, k]) - np.interp(t, db[:, 0], db[:, k]))))
                   for k in (1, 2, 3)) if len(t) else np.inf
        add("<m>(t), largest difference", None, None, diff, TOL["m_t"])
    plot(ma, mb, ang, vals, sel, folder / "compare.png", checks)
    return {"checks": checks, "agree": all(c["ok"] for c in checks), "mm2d": A, "mumax3": B, "plot": "compare.png"}


def plot(ma, mb, ang, vals, sel, path, checks):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from .mm2d.render import colour
    x = (np.arange(vals["Nx"]) - vals["Nx"] / 2 + 0.5) * vals["dx"] * 1e9
    y = (np.arange(vals["Ny"]) - vals["Ny"] / 2 + 0.5) * vals["dx"] * 1e9
    ext = [x[0], x[-1], y[0], y[-1]]
    fig, ax = plt.subplots(1, 3, figsize=(15, 5.2))
    for a, m, title in ((ax[0], ma, "mm2d (SpinMate)"), (ax[1], mb, "mumax3")):
        mm = np.where(sel, m, np.nan)
        a.imshow(colour(*mm), extent=ext, origin="lower", interpolation="bicubic")
        a.set_title(title, fontsize=13); a.set_xlabel("x (nm)"); a.set_ylabel("y (nm)")
    im = ax[2].imshow(np.where(sel, ang, np.nan), extent=ext, origin="lower", cmap="magma", vmin=0,
                      vmax=max(5.0, float(np.nanmax(np.where(sel, ang, np.nan)))))
    ax[2].set_title("angle between them (deg)", fontsize=13); ax[2].set_xlabel("x (nm)")
    fig.colorbar(im, ax=ax[2], fraction=0.046)
    text = " · ".join(f"{c['quantity'].split(' (')[0]}: {'ok' if c['ok'] else 'differs'}" for c in checks)
    fig.suptitle(text, fontsize=11)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def plot_file(cid):
    p = COMPARE / str(cid) / "compare.png"
    return p if S._valid(cid, "c") and p.exists() else None
