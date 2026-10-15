"""Run the original macrospin code in the current directory.

Called as: python -m app.runner   (cwd = run folder containing parameters.txt)
The physics code in macrospin/ is not modified. This wrapper only:
  - counts Heun steps to report progress
  - silences the per-step print in the temperature-dependent branch
  - adds a font fallback (Times New Roman is missing in Docker)
  - writes summary.json and neel_z.dat when the run ends
  - continuation runs: shifts the solver time by t_offset (from continuation.json) so time-dependent
    drives keep their phase, and writes absolute times to the output files
"""
import json
import os
import sys
import time
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent / "macrospin"
sys.path.insert(0, str(CODE_DIR))

PROGRESS_EVERY = 500


def write_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def read_offset(folder="."):
    """t_offset [s] of a continuation run, 0 for a normal run."""
    path = Path(folder) / "continuation.json"
    if not path.exists():
        return 0.0
    return float(json.loads(path.read_text()).get("t_offset_s") or 0.0)


def patch_progress(total_steps, t_offset=0.0):
    import solver as solver_mod

    original = solver_mod.solver.Heun
    state = {"n": 0, "t0": time.time()}

    def heun(self, m1, m2, t0):
        state["n"] += 1
        if state["n"] % PROGRESS_EVERY == 0:
            write_json("progress.json", {
                "step": state["n"], "total": total_steps,
                "elapsed": time.time() - state["t0"],
            })
        # absolute time for the drives (AC field, A0(t), Ku(t), chirp, T(t))
        return original(self, m1, m2, t0 + t_offset)

    solver_mod.solver.Heun = heun
    return state


def patch_output_time(t_offset):
    """Write absolute times in output1.dat / output2.dat and on the plot."""
    if not t_offset:
        return
    import output as output_mod

    original = output_mod.output.save_data

    def save_data(self, xx):
        for res in xx:
            res[:, 0] += t_offset
        return original(self, xx)

    output_mod.output.save_data = save_data


def dominant_frequency(t, y):
    # peak of the FFT magnitude, ignoring the DC bin
    import numpy as np
    if len(t) < 16:
        return None
    dt = float(np.mean(np.diff(t)))
    y = y - np.mean(y)
    if np.allclose(y, 0):
        return None
    spec = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(len(y), dt)
    k = int(np.argmax(spec[1:]) + 1)
    return float(freqs[k])


def switches(t, nz, level=0.5):
    """Real reversals of n_z: the sign counts only once |n_z| > level, so ringing around 0 is ignored."""
    state, times = 0, []
    for ti, v in zip(t, nz):
        s = 1 if v > level else -1 if v < -level else 0
        if s and state and s != state:
            times.append(float(ti))
        if s:
            state = s
    return times


def settle_time(t, m1, m2, tol=0.02):
    """Time after which m1 and m2 stay within tol of their final values; None if not settled by 90 % of the run."""
    import numpy as np
    dev = np.maximum(np.linalg.norm(m1 - m1[-1], axis=1), np.linalg.norm(m2 - m2[-1], axis=1))
    out = np.where(dev > tol)[0]
    if not len(out):
        return float(t[0])
    k = int(out[-1]) + 1
    return float(t[k]) if k < 0.9 * len(t) else None


def summarise(runtime, folder=".", extra=None):
    import numpy as np
    folder = Path(folder)
    d1 = np.loadtxt(folder / "output1.dat")
    d2 = np.loadtxt(folder / "output2.dat")
    t = d1[:, 0]
    m1, m2 = d1[:, 1:4], d2[:, 1:4]
    neel = (m1 - m2) / 2.0
    mnet = (m1 + m2) / 2.0

    # same definition as original_scripts/neel_vector_generate.py
    neel_z = np.abs((m1[:, 2] - m2[:, 2]) / 2.0)
    np.savetxt(folder / "neel_z.dat", np.column_stack((t, neel_z)), fmt="%.6e")

    dot = np.sum(m1 * m2, axis=1)

    def order(v):
        return "AFM" if v < -0.9 else "FM" if v > 0.9 else "canted"

    sign = np.sign(neel[:, 2])
    flips = np.where(np.diff(sign) != 0)[0]
    half = len(t) // 2
    sw = switches(t, neel[:, 2])
    settled = settle_time(t, m1, m2)

    def vec(a):
        return [round(float(x), 6) for x in a]

    summary = {
        "points": int(len(t)),
        "t_start_s": float(t[0]),
        "t_end_s": float(t[-1]),
        "runtime_s": round(runtime, 2),
        "m1_initial": vec(m1[0]), "m1_final": vec(m1[-1]),
        "m2_initial": vec(m2[0]), "m2_final": vec(m2[-1]),
        "neel_initial": vec(neel[0]), "neel_final": vec(neel[-1]),
        "net_m_final": vec(mnet[-1]),
        "m1_dot_m2_initial": round(float(dot[0]), 4), "m1_dot_m2_final": round(float(dot[-1]), 4),
        "order_initial": order(dot[0]), "order_final": order(dot[-1]),
        "neel_z_abs_mean": float(np.mean(neel_z)),
        "neel_z_abs_mean_second_half": float(np.mean(neel_z[half:])),
        "net_m_z_mean": float(np.mean(mnet[:, 2])),
        "neel_z_switches": len(sw),
        "first_neel_z_switch_s": sw[0] if sw else None,
        "neel_z_sign_changes": int(len(flips)),          # every zero crossing, ringing included
        "first_neel_z_sign_change_s": float(t[flips[0] + 1]) if len(flips) else None,
        "settle_time_s": settled,
        "dominant_freq_m1x_GHz": None,
        "dominant_freq_neel_z_GHz": None,
    }
    # FFT of the second half: kept only with >= 5 periods in the window and a real oscillation
    window = float(t[-1] - t[half]) if len(t) > 1 else 0.0
    notes = []
    for key, series in (("dominant_freq_m1x_GHz", m1[half:, 0]),
                        ("dominant_freq_neel_z_GHz", neel[half:, 2])):
        if float(np.std(series)) < 1e-4:
            notes.append(f"{key}: no oscillation left in the second half")
            continue
        f = dominant_frequency(t[half:], series)
        if f and f * window >= 5:
            summary[key] = round(f / 1e9, 4)
        elif f:
            notes.append(f"{key}: fewer than 5 periods in the second half, not reliable")
    if notes:
        summary["freq_note"] = "; ".join(notes)
    summary.update(extra or {})
    write_json(folder / "summary.json", summary)
    return summary


def main():
    os.environ.setdefault("MPLBACKEND", "Agg")
    import matplotlib
    matplotlib.use("Agg")

    from importdata import parse_arguments
    sys.argv = ["main.py", "--input-file", "parameters.txt"]
    args = parse_arguments()
    total = int(args.t / args.h)

    t_offset = read_offset()
    patch_progress(total, t_offset)
    patch_output_time(t_offset)

    if os.environ.get("MACROSPIN_VERBOSE") != "1":
        import current
        current.print = lambda *a, **k: None  # per-step debug print

    import main as main_mod
    import matplotlib.pyplot as plt
    plt.rcParams["font.serif"] = ["Times New Roman", "Liberation Serif", "DejaVu Serif"]
    plt.show = lambda *a, **k: None

    t0 = time.time()
    main_mod.MAIN().main()
    runtime = time.time() - t0
    write_json("progress.json", {"step": total, "total": total, "elapsed": runtime})
    summarise(runtime, extra={"t_offset_s": t_offset} if t_offset else None)


if __name__ == "__main__":
    main()
