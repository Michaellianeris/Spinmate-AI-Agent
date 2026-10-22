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


def excitation_mask(film, vals):
    """1 where B_pulse acts: the whole film, a stripe across y, or a dot."""
    X, Y = np.meshgrid(film.x, film.y)
    w, x0 = vals["excite_width"], vals["excite_x"]
    if vals["excite"] == "stripe":
        m = np.abs(X - x0) <= w / 2
    elif vals["excite"] == "dot":
        m = np.hypot(X - x0, Y) <= w / 2
    else:
        m = np.ones_like(X, bool)
    if not m.any():                                  # narrower than a cell: the nearest column or cell
        m = np.zeros_like(X, bool)
        i = int(np.argmin(np.abs(film.x - x0)))
        m[:, i] = True if vals["excite"] == "stripe" else False
        if vals["excite"] == "dot":
            m[film.Ny // 2, i] = True
    return m[None].astype(float)


def roll_to(film, m, sx, sy):
    """m shifted by (sx, sy) metres on the periodic film (whole cells)."""
    return np.roll(m, (int(round(sy / film.dx)), int(round(sx / film.dx))), axis=(1, 2))


def set_torques(film, vals):
    """Spin torques from the current density j: Zhang-Li STT and damping-like SOT. Returns what was set."""
    j = np.asarray(vals["j"], float)
    jn = float(np.linalg.norm(j))
    out = {}
    if jn == 0:
        return out
    if vals["stt_P"]:
        film.stt_u = vals["stt_P"] * S.MUB * j / (S.QE * film.Ms)       # g = 2
        film.stt_beta = vals["stt_beta"]
        out["stt_u_mps"] = [round(float(v), 3) for v in film.stt_u[:2]]
    if vals["theta_SH"]:
        film.sot_h = S.HBAR * vals["theta_SH"] * jn / (2 * S.QE * S.MU0 * film.Ms * vals["thickness"])
        sig = np.asarray(vals["sot_sigma"], float)
        if not np.any(sig):
            sig = np.cross([0.0, 0.0, 1.0], j / jn)                   # spin Hall effect: z x j
        film.sot_sigma = sig / np.linalg.norm(sig)
        out["sot_field_A_per_m"] = round(float(film.sot_h), 2)
        out["sot_sigma"] = [round(float(v), 4) for v in film.sot_sigma]
    if out:
        out["current_A_per_m2"] = [float(v) for v in j[:2]]
    return out


def motion_summary(film, vals, track):
    """Velocity of the texture centre (fit over the second half of the run) and the Hall angle to j."""
    t, x, y = track[:, 0], track[:, 1], track[:, 2]
    ok = ~np.isnan(x)
    t, x, y = t[ok], x[ok], y[ok]
    Lx, Ly = film.Nx * film.dx, film.Ny * film.dx
    x = np.unwrap(2 * np.pi * x / Lx) * Lx / (2 * np.pi)            # crossings of the periodic edges
    y = np.unwrap(2 * np.pi * y / Ly) * Ly / (2 * np.pi)
    h = len(t) // 2
    vx, vy = np.polyfit(t[h:], x[h:], 1)[0], np.polyfit(t[h:], y[h:], 1)[0]
    j = np.asarray(vals["j"], float)[:2]
    jh = j / np.linalg.norm(j)
    v_par, v_perp = vx * jh[0] + vy * jh[1], -vx * jh[1] + vy * jh[0]     # along j and along z x j
    hall = float(np.degrees(np.arctan2(v_perp, abs(v_par)))) * (1 if v_par >= 0 else -1)
    direction = float(np.degrees(np.arctan2(v_perp, v_par)))
    np.savetxt("track.dat", np.column_stack([t, x, y]), fmt="%.8E", header="t (s), x (m), y (m) of the texture centre")
    return {"velocity_mps": [round(float(vx), 3) + 0.0, round(float(vy), 3) + 0.0], "speed_mps": round(float(np.hypot(vx, vy)), 3),
            "hall_angle_deg": round(hall, 2) + 0.0, "direction_to_j_deg": round(direction, 2) + 0.0, "distance_nm": round(float(np.hypot(x[-1] - x[0], y[-1] - y[0])) * 1e9, 2)}


def _unwrap(film, track):
    t, x, y = track[:, 0], track[:, 1], track[:, 2]
    ok = ~np.isnan(x)
    t, x, y = t[ok], x[ok], y[ok]
    Lx, Ly = film.Nx * film.dx, film.Ny * film.dx
    return t, np.unwrap(2 * np.pi * x / Lx) * Lx / (2 * np.pi), np.unwrap(2 * np.pi * y / Ly) * Ly / (2 * np.pi)


def diffusion_summary(film, track):
    """Brownian motion of the texture centre: D from the mean squared displacement, <r²> = 4 D tau."""
    t, x, y = _unwrap(film, track)
    np.savetxt("track.dat", np.column_stack([t, x, y]), fmt="%.8E", header="t (s), x (m), y (m) of the texture centre")
    lags = range(1, max(2, len(t) // 10))
    taus, msd = [], []
    for L in lags:
        dx, dy = x[L:] - x[:-L], y[L:] - y[:-L]
        taus.append(t[L] - t[0]); msd.append(float(np.mean(dx ** 2 + dy ** 2)))
    taus, msd = np.array(taus), np.array(msd)
    D = float(np.sum(taus * msd) / np.sum(taus ** 2)) / 4                # line through the origin
    return {"diffusion_m2ps": D, "rms_displacement_nm": round(float(np.sqrt(np.mean((x - x[0]) ** 2 + (y - y[0]) ** 2))) * 1e9, 3),
            "distance_nm": round(float(np.hypot(x[-1] - x[0], y[-1] - y[0])) * 1e9, 2)}


def damping(film, vals):
    """Gilbert damping, rising to 1 over `absorb` at the x edges (and y edges for a dot)."""
    a, w = vals["alpha"], vals["absorb"]
    if w <= 0:
        return a
    X, Y = np.meshgrid(film.x, film.y)
    hx, hy = film.Nx * film.dx / 2, film.Ny * film.dx / 2
    ramp = np.clip((np.abs(X) - (hx - w)) / w, 0, 1)
    if vals["excite"] == "dot":
        ramp = np.maximum(ramp, np.clip((np.abs(Y) - (hy - w)) / w, 0, 1))
    return a + (1 - a) * ramp ** 2


def theory_f(film, vals, k):
    """Spin waves of a film magnetised along +z (local demag): f = gamma0/2pi (H + H_K,eff + 2A/(mu0 Ms) k^2),
    with the k^2 of the 5-point Laplacian."""
    Keff = film.Ku - 0.5 * S.MU0 * film.Ms ** 2
    H = vals["B_ext"][2] / S.MU0
    k2 = (2 / film.dx * np.sin(np.asarray(k) * film.dx / 2)) ** 2
    return S.GAMMA0 / (2 * np.pi) * (H + 2 * Keff / (S.MU0 * film.Ms) + 2 * film.A / (S.MU0 * film.Ms) * k2)


def spin_wave_summary(film, vals, t, wave, mx_map):
    out = {"spin_wave_gap_GHz": round(float(theory_f(film, vals, 0)) / 1e9, 3)}
    x = film.x
    keep = np.abs(x - vals["excite_x"]) > vals["excite_width"] / 2 + 2 * film.dx
    if vals["absorb"] > 0:
        keep &= np.abs(x) < film.Nx * film.dx / 2 - vals["absorb"]
    if vals["pulse"] == "sine" and keep.sum() > 8:
        row = wave[-1][keep] - wave[-1][keep].mean()
        n = 8 * len(row)
        spec = np.abs(np.fft.rfft(row * np.hanning(len(row)), n))
        kk = 2 * np.pi * np.fft.rfftfreq(n, film.dx)
        k_sim = float(kk[1 + spec[1:].argmax()])
        ks = np.linspace(0, np.pi / film.dx, 20000)
        k_th = float(ks[np.argmin(np.abs(theory_f(film, vals, ks) - vals["f_rf"]))]) \
            if vals["f_rf"] > theory_f(film, vals, 0) else None
        out["wavelength_nm"] = round(2 * np.pi / k_sim * 1e9, 2)
        if k_th:
            out["wavelength_theory_nm"] = round(2 * np.pi / k_th * 1e9, 2)
        else:
            out["note"] = "f_rf is below the spin-wave gap: the waves do not propagate"
    if vals["pulse"] == "sinc":
        k, f, P = dispersion(film, vals, t, wave)
        sel = (f > 1.05 * theory_f(film, vals, 0)) & (f < 0.9 * vals["f_rf"])
        errs = []
        for i in np.where(sel)[0][::4]:
            j = int(np.argmax(P[i, k >= 0]))
            kj = k[k >= 0][j]
            if kj > 0:
                errs.append(abs(float(theory_f(film, vals, kj)) - f[i]) / f[i])
        if errs:
            out["dispersion_mismatch_pct"] = round(100 * float(np.median(errs)), 2)
    return out


def dispersion(film, vals, t, wave):
    """|FFT|^2 of mx(x, t) along the centre row: k (rad/m), f (Hz, >= 0), power [f, k]."""
    W = wave - wave.mean(0)
    W = W * np.hanning(len(t))[:, None] * np.hanning(W.shape[1])[None, :]
    F = np.fft.fftshift(np.fft.fft2(W))
    f = np.fft.fftshift(np.fft.fftfreq(len(t), vals["table_dt"]))
    k = np.fft.fftshift(2 * np.pi * np.fft.fftfreq(W.shape[1], film.dx))
    pos = f >= 0
    return k, f[pos], np.abs(F[pos]) ** 2


def _blur(a, sigma):
    """Gaussian blur (periodic), sigma in cells."""
    ky = np.fft.fftfreq(a.shape[0])[:, None]; kx = np.fft.fftfreq(a.shape[1])[None, :]
    g = np.exp(-2 * (np.pi * sigma) ** 2 * (kx ** 2 + ky ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(a) * g))


def save_wave_arrows(render, film, vals, summary, m, m0, x_nm, y_nm, cap):
    """Spin waves in the style of the textures: 3D arrows and a top view, the precession enlarged to be seen."""
    lam = summary.get("wavelength_theory_nm") or summary.get("wavelength_nm") or 20 * film.dx * 1e9
    dm = (m - m0)[:2]
    amp = np.hypot(dm[0], dm[1])
    env = np.sqrt(_blur(amp ** 2, lam / 2 / (film.dx * 1e9)))       # local amplitude of the wave
    env = np.maximum(env, 0.04 * env.max())                          # no wave: stays near the ground state
    tilt = 0.9 * dm / env                                            # every crest tilted the same, colour = phase
    vis = np.stack([m0[0] + tilt[0], m0[1] + tilt[1], m0[2]])
    vis = vis / np.linalg.norm(vis, axis=0)
    cell = film.dx * 1e9
    absorb = vals["absorb"] * 1e9
    x_lo, x_hi = x_nm[0] + absorb, x_nm[-1] - absorb
    if vals["excite"] == "dot":
        cx, half_x = vals["excite_x"] * 1e9, min(2 * lam, (x_hi - x_lo) / 2)
        half_y = min(2 * lam, y_nm[-1] - absorb)
    else:                                            # a window beside the antenna, towards the larger side
        a = vals["excite_x"] * 1e9
        width = min(4 * lam, max(x_hi - a, a - x_lo) - 2 * cell)
        cx = a + width / 2 + 2 * cell if x_hi - a >= a - x_lo else a - width / 2 - 2 * cell
        half_x, half_y = width / 2, min(1.5 * lam, y_nm[-1])
    sx, sy = np.abs(x_nm - cx) <= half_x, np.abs(y_nm) <= half_y
    mc = vis[:, sy][:, :, sx]
    step = max(1, int(round(lam / cell / 7)))
    render.save_views(mc, x_nm[sx], y_nm[sy], cap + " · precession enlarged to show the phase",
                      prefix="spinwave", step=step)


def main():
    t0 = time.time()
    vals = parse(Path("parameters.txt").read_text())
    afm = vals["lattice"] in ("afm", "saf")
    if afm:
        saf = vals["lattice"] == "saf"
        J = -vals["J_rkky"] / vals["thickness"] if saf else vals["J_afm"]     # RKKY per area -> per volume
        film = S.AFMFilm(vals["Nx"], vals["Ny"], vals["dx"], vals["Msat"], vals["Aex"], vals["Ku"], vals["D"],
                         J_afm=J, dmi=vals["dmi"], boundary=vals["boundary"], B=vals["B_ext"],
                         demag="local" if saf else "none")
    else:
        film = S.Film(vals["Nx"], vals["Ny"], vals["dx"], vals["Msat"], vals["Aex"], vals["Ku"], vals["D"],
                      dmi=vals["dmi"], boundary=vals["boundary"], B=vals["B_ext"])
    d = vals["thickness"]
    m = S.initial(film, vals["texture"], R0=vals["R0"], m0=vals["m0"], seed=vals["seed"])
    if afm:
        m = np.stack([m, -m], 1)                      # sublattice 2 antiparallel to sublattice 1
    if vals["start_from"]:                          # the final texture of an earlier run
        from spinmate_core.mm2d import start_file, start_problem
        err = start_problem(vals)
        if err:
            raise SystemExit(err)
        m = S._norm(np.load(start_file(vals["start_from"]))["m"].astype(float))
    view = film.neel if afm else (lambda mm: mm)          # what the pictures and the texture metrics look at
    avg = (lambda mm: mm[:, 0].mean((1, 2))) if afm else (lambda mm: mm.mean((1, 2)))
    avg2 = (lambda mm: mm[:, 1].mean((1, 2))) if afm else avg
    rows2 = []
    summary = {"engine": "mm2d", "mode": vals["mode"], "texture_seed": vals["texture"],
               "grid": [vals["Nx"], vals["Ny"]], "cell_nm": vals["dx"] * 1e9,
               "energy_initial_J": film.energy(m, d)}
    if vals["start_from"]:
        summary["started_from"] = vals["start_from"]
        summary["texture_seed"] = f"final texture of {vals['start_from']}"
    rows = []                                  # (x, mx, my, mz) for output1.dat
    erows = []                                 # energy log
    relaxed = vals["mode"] == "relax" or vals["relax_first"]
    if relaxed:
        total = vals["max_iter"] if vals["mode"] == "relax" else vals["max_iter"] + int(vals["t"] / vals["table_dt"])

        def cb(it, torque, mm):
            rows.append((it, *avg(mm))); rows2.append((it, *avg2(mm)))
            erows.append((it, film.energy(mm, d), torque))
            if it % 200 == 0:
                _progress(it, total, t0)
        m, it, torque = S.minimise(film, m, tol=vals["tol"], max_iter=vals["max_iter"], callback=cb)
        summary.update(relax_iterations=it, relax_torque_A_per_m=round(torque, 4),
                       relaxed=bool(torque < vals["tol"]))
    wave = None
    if vals["mode"] == "dynamics":
        rows, rows2, erows = [], [], []
        n_out = int(round(vals["t"] / vals["table_dt"]))
        start = summary.get("relax_iterations", 0)
        mask = excitation_mask(film, vals)
        pulse = np.asarray(vals["B_pulse"], float).reshape(3, 1, 1) / S.MU0 * mask
        if afm:
            pulse = pulse[:, None]                        # the same field on both sublattices
        alpha = damping(film, vals)
        current = set_torques(film, vals)
        if current:
            summary.update(current)
            summary["thiele_velocity_mps"] = [round(float(v), 3) + 0.0 for v in S.thiele_velocity(film, m, vals["alpha"])]
        T = vals["Temp"]
        localised = vals["texture"] in ("skyrmion", "skyrmionium") and vals["excite"] == "uniform"
        follow = bool(current) or (T > 0 and localised)       # track the centre: drift or Brownian motion
        if T > 0:
            dt_th = vals["thermal_dt"] or 0.15 / film.max_freq()
            rng = np.random.default_rng(vals["seed"] + 1)
            summary.update(temperature_K=T, thermal_dt_s=dt_th)
            if localised and not current:
                summary["diffusion_thiele_m2ps"] = S.KB * T * S.diffusion_thiele(film, view(m), vals["alpha"], d)
        track = [(0.0, *(S.centre(film, view(m)) or (np.nan, np.nan)))]
        mz_sum, mz_n = 0.0, 0
        base = film.H_ext.copy()
        shape, f_rf, t_p = vals["pulse"], vals["f_rf"], vals["t_pulse"]
        h_max = 1 / (24 * f_rf) if shape != "step" else np.inf      # the field changes within a period

        def amp(t):
            if t >= t_p:
                return 0.0
            if shape == "sine":                    # switched on over three periods: no broadband transient
                return np.sin(2 * np.pi * f_rf * t) * min(1.0, np.sin(np.pi / 2 * f_rf * t / 3) ** 2 if f_rf * t < 3 else 1.0)
            if shape == "sinc":
                return float(np.sinc(2 * f_rf * (t - t_p / 2)))
            return 1.0
        t, dt, steps = 0.0, 0.1 / film.max_freq(), 0
        row_j = film.Ny // 2
        m0 = m.copy()
        wave = [view(m)[0, row_j].copy()]
        rows.append((0.0, *avg(m))); rows2.append((0.0, *avg2(m))); erows.append((0.0, film.energy(m, d)))
        for k in range(1, n_out + 1):
            t_target = k * vals["table_dt"]
            while t < t_target * (1 - 1e-12):
                h = min(dt, t_target - t, h_max if t < t_p else np.inf)
                if t < t_p < t + h:
                    h = t_p - t                    # end the pulse exactly
                if T > 0:                          # stochastic LLG: fixed Heun steps (Stratonovich)
                    h = min(dt_th, t_target - t, h_max if t < t_p else np.inf)
                    if t < t_p < t + h:
                        h = t_p - t
                    noise = S.thermal_sigma(film, alpha, T, h, d) * rng.normal(size=m.shape)
                    film.H_ext = base + pulse * amp(t + h / 2) + noise
                    m = S.heun(film, m, h, alpha)
                    used = h
                else:
                    film.H_ext = base + pulse * amp(t + h / 2)
                    m, used, dt = S.rk45_step(film, m, h, alpha, tol=vals["rk_tol"])
                t += used; steps += 1
            film.H_ext = base
            rows.append((t, *avg(m))); rows2.append((t, *avg2(m))); erows.append((t, film.energy(m, d)))
            wave.append(view(m)[0, row_j].copy())
            if follow:
                track.append((t, *(S.centre(film, view(m)) or (np.nan, np.nan))))
            if T > 0 and k > n_out // 5:
                mz_sum += float(avg(m)[2]); mz_n += 1
            _progress(start + k, start + n_out, t0)
        summary.update(dynamics_steps=steps, t_end_s=t, points=len(rows))
        if current and len(track) > 4:
            summary.update(motion_summary(film, vals, np.array(track)))
        elif follow and len(track) > 20:
            summary.update(diffusion_summary(film, np.array(track)))
        if T > 0 and mz_n:
            summary["mean_mz_thermal"] = round(mz_sum / mz_n, 5)
        tt = np.array([r[0] for r in rows])
        sel = tt >= t_p
        if sel.sum() > 16 and vals["excite"] == "uniform" and not current and (T == 0 or any(vals["B_pulse"])):
            for comp, idx in (("mx", 1), ("mz", 3)):
                sig = np.array([r[idx] for r in rows])[sel]
                sig = sig - sig.mean()
                spec = np.abs(np.fft.rfft(sig * np.hanning(len(sig))))
                f = np.fft.rfftfreq(len(sig), vals["table_dt"])
                if spec[1:].max() > 1e-9:
                    summary[f"peak_frequency_{comp}_GHz"] = round(float(f[1 + spec[1:].argmax()]) / 1e9, 3)
        if vals["excite"] != "uniform":
            wave = np.array(wave)
            np.savez_compressed("spinwave.npz", t=tt, x=film.x, y=film.y, mx_row=wave.astype(np.float32),
                                mx_map=(m[0] - m0[0]).astype(np.float32), mask=mask[0] > 0)
            summary.update(spin_wave_summary(film, vals, tt, wave, m[0] - m0[0]))
    _progress(1, 1, t0)

    rows = np.array(rows) if rows else np.array([[0.0, *avg(m)]])
    rows2 = np.array(rows2) if len(rows2) == len(rows) else np.column_stack([rows[:, 0], np.tile(avg2(m), (len(rows), 1))])
    np.savetxt("output1.dat", rows, fmt="%.10E")
    np.savetxt("output2.dat", rows2 if afm else rows, fmt="%.10E")    # a ferromagnet has one sublattice
    if erows:
        np.savetxt("energy.dat", np.array(erows), fmt="%.10E")
    np.savez_compressed("m_final.npz", m=m.astype(np.float32), x=film.x, y=film.y)

    Q = round(S.topological_charge(view(m)), 4) + 0.0      # no -0.00
    if afm:
        summary["sublattice_charges"] = [round(S.topological_charge(m[:, k]), 4) + 0.0 for k in (0, 1)]
        summary["max_canting"] = round(float(np.abs(m[:, 0] + m[:, 1]).max() / 2), 6)
    shift = (0.0, 0.0)
    if film.boundary == "periodic" and not (wave is not None and vals["excite"] != "uniform"):
        c = S.centre(film, view(m))            # a texture that moved across the edge: show it whole, centred
        if c is not None and not S.texture_metrics(film, view(m)).get("crosses_film"):
            shift = (-c[0], -c[1])
    m_view = roll_to(film, view(m), *shift)
    metrics = S.texture_metrics(film, m_view)
    summary.update(topological_charge=round(Q, 4), energy_final_J=film.energy(m, d),
                   m_final=[round(float(v), 6) for v in avg(m)], runtime_s=round(time.time() - t0, 2),
                   **{k: v for k, v in metrics.items()})
    summary["m1_final"] = summary["m_final"]
    summary["m2_final"] = [round(float(v), 6) for v in avg2(m)]
    summary["lattice"] = vals["lattice"]
    summary["texture"] = name_texture(Q, metrics)
    if vals["mode"] == "dynamics" and vals["excite"] != "uniform":
        summary["texture"] = "spin_waves"

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
    mc, xc, yc = render.crop(m_view, x_nm, y_nm, metrics.get("centre_nm"), half)
    pair = None
    if afm:                                       # the two sublattices, cropped like the Néel vector
        pair = [render.crop(roll_to(film, m[:, k], *shift), x_nm, y_nm, metrics.get("centre_nm"), half)[0] for k in (0, 1)]
    dmi = f"D = {vals['D'] * 1e3:.3g} mJ/m² ({vals['dmi']})" if vals["D"] else "no DMI"
    kind = summary["texture"].replace("_", " ")
    if summary["texture"] in ("skyrmion", "skyrmionium") and vals["D"]:
        kind = ("Bloch " if vals["dmi"] == "bulk" else "Néel ") + kind

    caption = (f"{kind} · Q = {Q:+.2f} · A = {vals['Aex'] * 1e12:.3g} pJ/m, {dmi}, "
               f"Ku = {vals['Ku'] / 1e6:.3g} MJ/m³, Ms = {vals['Msat'] / 1e3:.4g} kA/m · {vals['dx'] * 1e9:.3g} nm cells")
    if vals["Temp"] > 0 and vals["mode"] == "dynamics":
        caption += f" · T = {vals['Temp']:.0f} K"
    if afm:
        q1, q2 = summary["sublattice_charges"]
        caption = (f"{'synthetic AFM' if vals['lattice'] == 'saf' else 'AFM'} {kind} · Q1 = {q1:+.0f}, Q2 = {q2:+.0f} · A = {vals['Aex'] * 1e12:.3g} pJ/m, {dmi}, "
                   f"Ku = {vals['Ku'] / 1e6:.3g} MJ/m³ · arrows: m1 red, m2 blue; film: Néel vector n")
    if "velocity_mps" in summary or "diffusion_m2ps" in summary:
        tr = np.loadtxt("track.dat")
        mid = ((tr[0, 1] + tr[-1, 1]) / 2, (tr[0, 2] + tr[-1, 2]) / 2)    # the path in the middle of the picture
        tr[:, 1] -= mid[0]; tr[:, 2] -= mid[1]
        render.save_track(roll_to(film, view(m), -mid[0], -mid[1]), x_nm, y_nm, tr, summary, vals)
    if wave is None or vals["excite"] == "uniform":          # a uniform film with waves has no texture to show
        render.save_views(mc, xc, yc, caption,
                          round_view=half is not None, pair=pair)
    else:
        wtxt = (f"λ = {summary['wavelength_nm']:.1f} nm (theory {summary['wavelength_theory_nm']:.1f} nm)"
                if summary.get("wavelength_theory_nm") else "")
        drive = {"sine": f"sine {vals['f_rf'] / 1e9:.3g} GHz", "sinc": f"sinc up to {vals['f_rf'] / 1e9:.3g} GHz",
                 "step": "field step"}[vals["pulse"]]
        cap = (f"spin waves from a {vals['excite']} antenna · {drive} · t = {summary['t_end_s'] * 1e12:.0f} ps"
               + (f" · {wtxt}" if wtxt else "") + f" · gap {summary['spin_wave_gap_GHz']:.1f} GHz")
        render.save_spinwave(m[0] - m0[0], x_nm, y_nm, excitation_mask(film, vals)[0] > 0, cap,
                             absorb_nm=vals["absorb"] * 1e9)
        save_wave_arrows(render, film, vals, summary, m, m0, x_nm, y_nm, cap)
        mask = excitation_mask(film, vals)[0]
        render.save_profile(x_nm, (m[0] - m0[0])[film.Ny // 2], x_nm[mask.any(0)][[0, -1]] if mask.any() else [],
                            vals["absorb"] * 1e9, summary.get("wavelength_nm"))
        if vals["pulse"] == "sinc":
            k, f, P = dispersion(film, vals, np.array([r[0] for r in rows]), wave)
            ks = np.linspace(k.min(), k.max(), 400)
            render.save_dispersion(k, f, P, ks, theory_f(film, vals, ks), vals["f_rf"],
                                   f"mx(x, t) along y = 0 · {vals['Nx']} × {vals['dx'] * 1e9:.3g} nm cells · "
                                   f"line: γ₀/2π·(H + H_K,eff + 2A k²/μ₀Ms)"
                                   + (f" · median mismatch {summary['dispersion_mismatch_pct']:.1f} %"
                                      if "dispersion_mismatch_pct" in summary else ""))
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
