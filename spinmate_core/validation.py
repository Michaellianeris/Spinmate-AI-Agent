"""Physics validation of the macrospin engine: each case runs the real solver and compares the result with
an analytic expectation. No LLM. Run all cases with  python -m spinmate_core.validation  (report in
WORKSPACE/validation/)."""
import html
import json
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from . import simulations as S
from .params import defaults

MU0 = 4e-7 * np.pi


@dataclass
class Result:
    id: str
    title: str
    status: str            # pass | fail
    measured: dict
    expected: dict
    tolerance: str
    note: str = ""
    runs: list = field(default_factory=list)
    seconds: float = 0.0


def _const():
    d = defaults()
    Ms, A0, Ku, l, g0 = d["Ms"], d["A0"], d["Ku"], d["l"], d["g0"]
    # each sublattice carries Ms/2: H_K = 4 Ku / (mu0 Ms), H_E = 4 |A0| / (mu0 Ms l^2), here in units of Ms
    return {"Ms": Ms, "g0": g0, "K": 4 * Ku / (MU0 * Ms ** 2), "J": 4 * abs(A0) / (MU0 * Ms ** 2 * l ** 2),
            "F0": g0 * Ms / (2 * np.pi)}   # frequency unit


def _run(text, label):
    r = S.start_run(text, label=label)
    if not r.get("ok"):
        raise RuntimeError(r.get("error"))
    done = S.wait_for_run(r["run_id"], 900)
    if done.get("status") != "done":
        raise RuntimeError(f"{r['run_id']} {done.get('status')}")
    return r["run_id"], S._load_series(r["run_id"])


def _vec(v):
    return ",".join(f"{x:.10g}" for x in v)


def _freq(t, y):
    """Frequency from linearly interpolated zero crossings of the oscillating part."""
    y = y - y.mean()
    i = np.where(np.diff(np.signbit(y)))[0]
    tc = t[i] - y[i] * (t[i + 1] - t[i]) / (y[i + 1] - y[i])
    if len(tc) < 4:
        return float("nan")
    return (len(tc) - 1) / 2 / (tc[-1] - tc[0])


def _rel(a, b):
    return abs(a - b) / abs(b)


def _tilted(deg, afm):
    s, c = np.sin(np.radians(deg)), np.cos(np.radians(deg))
    return ((s, 0, -c), (-s, 0, c)) if afm else ((s, 0, c), (s, 0, c))


# ---------------------------------------------------------------- cases
def norm():
    rid, d = _run("--t=1e-10\n--A0=0.248e-12\n", "validation: norm")
    dev = float(max(np.abs(np.linalg.norm(d[:, 1:4], axis=1) - 1).max(), np.abs(np.linalg.norm(d[:, 4:7], axis=1) - 1).max()))
    return Result("norm", "|m1| = |m2| = 1 along the whole trajectory", "pass" if dev < 1e-9 else "fail",
                  {"max | |m|-1 |": dev}, {"max | |m|-1 |": 0.0}, "< 1e-9", runs=[rid])


def determinism():
    text = "--t=5e-11\n--A0=0.248e-12\n"
    a, da = _run(text, "validation: determinism 1")
    b, db = _run(text, "validation: determinism 2")
    diff = float(np.abs(da - db).max())
    return Result("determinism", "Same input, same trajectory (T = 0)", "pass" if diff == 0 else "fail",
                  {"max difference": diff}, {"max difference": 0.0}, "exactly 0", runs=[a, b])


def energy():
    """alpha = 0, no current, no field: the energy of the two sublattices is conserved."""
    c = _const()
    m1, m2 = _tilted(10, afm=True)
    rid, d = _run(f"--a=0\n--t=5e-10\n--m1={_vec(m1)}\n--m2={_vec(m2)}\n", "validation: energy")
    a, b = d[:, 1:4], d[:, 4:7]
    # energy whose gradient is the solver's field: exchange J m1.m2 (AFM) and anisotropy K/2 mz^2 per sublattice
    E = c["J"] * np.sum(a * b, 1) - c["K"] / 2 * (a[:, 2] ** 2 + b[:, 2] ** 2)
    drift = float((E.max() - E.min()) / abs(E[0]))
    return Result("energy", "Energy conservation without damping (α = 0)", "pass" if drift < 1e-3 else "fail",
                  {"relative drift": drift}, {"relative drift": 0.0}, "< 1e-3 over 0.5 ns",
                  note="Heun step h = 1e-13 s, the default.", runs=[rid])


def kittel():
    """Ferromagnetic uniform mode: f = (g0 Ms / 2 pi)(h + k), h = mu0 H / (mu0 Ms)."""
    c = _const()
    m1, m2 = _tilted(3, afm=False)
    f, runs = {}, []
    for H in (0.0, 0.1):
        rid, d = _run(f"--a=0\n--A0=0.248e-12\n--t=2e-9\n--H={H}\n--Hex_DC=0,0,1\n--m1={_vec(m1)}\n--m2={_vec(m2)}\n",
                      f"validation: Kittel H={H} T")
        f[H] = _freq(d[:, 0], d[:, 1])
        runs.append(rid)
    slope = (f[0.1] - f[0.0]) / 0.1
    slope_exp = c["g0"] / MU0 / (2 * np.pi)            # gamma / 2 pi, Hz per T
    f0_exp = c["F0"] * c["K"]
    ok_slope = _rel(slope, slope_exp) < 0.01
    ok_f0 = _rel(f[0.0], f0_exp) < 0.01
    note = "f = (γ/2π) μ0 (H_K + H), μ0 H_K = 4 Ku / Ms."
    return Result("kittel", "Ferromagnetic resonance (Kittel), A0 > 0, easy axis z",
                  "pass" if ok_slope and ok_f0 else "fail",
                  {"f(H=0) GHz": f[0.0] / 1e9, "f(H=0.1 T) GHz": f[0.1] / 1e9, "slope GHz/T": slope / 1e9},
                  {"f(H=0) GHz": f0_exp / 1e9, "slope GHz/T": slope_exp / 1e9}, "1 %", note, runs)


def afmr(h=2e-14):
    """Antiferromagnetic resonance at H = 0: f = (g0 Ms / 2 pi) sqrt(k (2 j + k))."""
    c = _const()
    m1, m2 = _tilted(2, afm=True)
    rid, d = _run(f"--a=0\n--h={h}\n--t=3e-10\n--m1={_vec(m1)}\n--m2={_vec(m2)}\n", f"validation: AFMR h={h:g}")
    f = _freq(d[:, 0], (d[:, 1] - d[:, 4]) / 2)
    exp = c["F0"] * np.sqrt(c["K"] * (2 * c["J"] + c["K"]))
    return Result("afmr", "Antiferromagnetic resonance, A0 < 0, H = 0", "pass" if _rel(f, exp) < 0.01 else "fail",
                  {"f GHz": f / 1e9}, {"f GHz": exp / 1e9}, "1 %", "f = (γ/2π) μ0 √(H_K (2 H_E + H_K))", [rid])


def convergence():
    """The AFMR frequency does not depend on the time step: default h = 1e-13 s against 2e-14 s."""
    c = _const()
    m1, m2 = _tilted(2, afm=True)
    f, runs = {}, []
    for h in (1e-13, 2e-14):
        rid, d = _run(f"--a=0\n--h={h}\n--t=3e-10\n--m1={_vec(m1)}\n--m2={_vec(m2)}\n", f"validation: step {h:g}")
        f[h] = _freq(d[:, 0], (d[:, 1] - d[:, 4]) / 2)
        runs.append(rid)
    r = _rel(f[1e-13], f[2e-14])
    return Result("convergence", "Time-step convergence (AFMR frequency)", "pass" if r < 0.005 else "fail",
                  {"f(h=1e-13) GHz": f[1e-13] / 1e9, "f(h=2e-14) GHz": f[2e-14] / 1e9, "difference": r},
                  {"difference": 0.0}, "< 0.5 %", runs=runs)


def damping():
    """Gilbert damping of the ferromagnetic mode: the cone angle decays as exp(-alpha omega t) for small alpha."""
    a = 0.01
    m1, m2 = _tilted(5, afm=False)
    rid, d = _run(f"--a={a}\n--A0=0.248e-12\n--t=4e-9\n--m1={_vec(m1)}\n--m2={_vec(m2)}\n", "validation: damping")
    t, mz = d[:, 0], d[:, 3]
    theta = np.arccos(np.clip(mz, -1, 1))
    rate = -np.polyfit(t, np.log(theta), 1)[0]
    w = 2 * np.pi * _freq(t, d[:, 1])
    exp = a * w
    return Result("damping", "Gilbert damping: decay rate α·ω of the ferromagnetic mode",
                  "pass" if _rel(rate, exp) < 0.02 else "fail",
                  {"decay rate 1/ns": rate / 1e9}, {"decay rate 1/ns": exp / 1e9}, "2 %",
                  note="ω is the measured precession frequency.", runs=[rid])


def phase_boundary():
    """AFM <-> FM stability boundary |Ku| = 2 |A0| / l^2: below it the order follows the sign of A0, above it the
    starting order survives. Checked at 0.9 and 1.1 times the boundary, from both sides (l = 3.5 nm)."""
    l, a0 = 3.5e-9, 3e-12
    kc = 2 * a0 / l ** 2
    fm = "--m1=0.25,0,0.968\n--m2=0.35,0,0.937\n"
    # below the boundary the starting order is unstable and leaves (near it slowly, so through canted states)
    cases = [("AFM start, A0 > 0", "AFM", f"--A0={a0}", "", 0.9, "leaves AFM"),
             ("AFM start, A0 > 0", "AFM", f"--A0={a0}", "", 1.1, "stays AFM"),
             ("FM start, A0 < 0", "FM", f"--A0={-a0}", fm, 0.9, "leaves FM"),
             ("FM start, A0 < 0", "FM", f"--A0={-a0}", fm, 1.1, "stays FM")]
    measured, expected, runs, ok = {}, {}, [], True
    for name, start_order, a0_line, start, f, want in cases:
        rid = S.start_run(f"{a0_line}\n--l={l}\n--Ku={f * kc:.6g}\n--t=1e-9\n{start}", label=f"validation: phase {name} {f}")["run_id"]
        end = (S.wait_for_run(rid, 900).get("summary") or {}).get("order_final")
        got = f"{'stays' if end == start_order else 'leaves'} {start_order} (ends {end})"
        key = f"{name}, Ku = {f} x boundary"
        measured[key], expected[key] = got, want
        ok &= got.startswith(want)
        runs.append(rid)
    return Result("phase", "AFM/FM stability boundary |Ku| = 2|A0|/l²", "pass" if ok else "fail", measured, expected,
                  "final order", note=f"A0 = ±3 pJ/m, l = 3.5 nm, boundary {kc / 1e6:.3f} MJ/m³", runs=runs)


def thermal():
    """The thermal field is isotropic and Gaussian: per-component mean 0, standard deviation Temp_Ampl."""
    import sys
    from .params import CODE_DIR
    if str(CODE_DIR) not in sys.path:
        sys.path.insert(0, str(CODE_DIR))
    from conf import conFile
    from solver import solver as Solver
    p = conFile()
    p.Temp_Ampl, p.flag5, p.Ku, p.A0, p.a = 1.0, False, 0.0, 0.0, 0.0
    sv = Solver(p)
    np.random.seed(1)
    m = np.array([0.0, 0.0, 1.0])
    # with no other field, mxx = -(g0 Ms) m x h_T, so the x and y components of h_T are recovered from it
    pref = p.g0 * p.Ms
    samples = np.array([sv.mxx(m, m, 0.0) for _ in range(20000)]) / -pref
    hx, hy = -samples[:, 1], samples[:, 0]
    mean, std = float(max(abs(hx.mean()), abs(hy.mean()))), float((hx.std() + hy.std()) / 2)
    ok = mean < 0.03 and abs(std - 1) < 0.03
    return Result("thermal", "Thermal field: isotropic, Gaussian, σ = Temp_Ampl", "pass" if ok else "fail",
                  {"|mean| (units of σ)": mean, "std (units of σ)": std}, {"|mean| (units of σ)": 0.0, "std (units of σ)": 1.0},
                  "3 % (20 000 samples)")


CASES = {"norm": norm, "determinism": determinism, "energy": energy, "kittel": kittel, "afmr": afmr,
         "convergence": convergence, "damping": damping, "phase": phase_boundary, "thermal": thermal}
FAST = ("norm", "determinism", "energy", "thermal")

# issues the first validation found in macrospin/, fixed in version 2.0; deterministic runs are unchanged bit for bit
FINDINGS = [
    ("F1", "Static anisotropy added through the AC term",
     "ku() in current.py returned the static K when flag2 was off, and solver.py added it again along u_ani_AC. "
     "The total, 4 Ku/(mu0 Ms) per sublattice, is right for sublattices of Ms/2 and gives the AFM/FM boundary "
     "|Ku| = 2|A0|/l^2, but it broke when u_ani_AC differs from u_ani or Ku(t) is on. Fixed: K = 4 Ku/(mu0 Ms^2) "
     "in one term, ku() returns only the AC part."),
    ("F4", "Ku(t) amplitude normalised with g0",
     "ku() divided Ku_Amp by g0 Ms^2 instead of mu0 Ms^2, so Ku(t) was 1.76e11 times too weak. Fixed."),
    ("F2", "RK4 weights and stages",
     "RK4() used 0.166 instead of 1/6 and fed the m1 slopes into the m2 stages. Fixed; Heun stays the default."),
    ("F3", "Thermal field direction",
     "The thermal field was Temp_Ampl times a unit vector from np.random.rand(3), with only positive components. "
     "Fixed: a Gaussian field with standard deviation Temp_Ampl per component, as run.py defines it."),
]


def run_all(names=None):
    out = []
    for name in names or CASES:
        t0 = time.time()
        try:
            r = CASES[name]()
        except Exception as e:      # a case that cannot run is a failure, not a crash of the suite
            r = Result(name, name, "fail", {}, {}, "", note=f"error: {e}")
        r.seconds = round(time.time() - t0, 1)
        out.append(r)
    return out


def _fmt(v):
    return f"{v:.4g}" if isinstance(v, float) else html.escape(str(v))


def report_html(results):
    rows = "".join(
        f"<tr class='{r.status}'><td><b>{html.escape(r.title)}</b><br><small>{html.escape(r.note)}</small></td>"
        f"<td>{'<br>'.join(f'{html.escape(k)}: {_fmt(v)}' for k, v in r.measured.items())}</td>"
        f"<td>{'<br>'.join(f'{html.escape(k)}: {_fmt(v)}' for k, v in r.expected.items())}</td>"
        f"<td>{html.escape(r.tolerance)}</td><td class='st'>{r.status.upper()}</td>"
        f"<td>{', '.join(r.runs)}</td></tr>" for r in results)
    finds = "".join(f"<li><b>{i} · {html.escape(t)}.</b> {html.escape(x)}</li>" for i, t, x in FINDINGS)
    n_ok = sum(r.status == "pass" for r in results)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>SpinMate physics validation</title>
<style>body{{font-family:system-ui,sans-serif;margin:32px;color:#13203A}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #D6DCE8;padding:8px;text-align:left;vertical-align:top;font-size:14px}}
tr.pass .st{{color:#1B7F3B;font-weight:700}}tr.fail .st{{color:#B42318;font-weight:700}}small{{color:#5B6478}}</style></head>
<body><h1>SpinMate physics validation</h1>
<p>Engine: macrospin (original code, unchanged) · {time.strftime('%Y-%m-%d %H:%M')} · {n_ok} of {len(results)} cases pass</p>
<table><tr><th>Case</th><th>Measured</th><th>Expected</th><th>Tolerance</th><th>Result</th><th>Runs</th></tr>{rows}</table>
<h2>Fixed in version 2.0</h2><ul>{finds}</ul></body></html>"""


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m spinmate_core.validation")
    ap.add_argument("cases", nargs="*", help=f"cases to run (default all): {', '.join(CASES)}")
    ap.add_argument("--allow", action="append", default=[], help="a known failing case that does not fail the exit code")
    a = ap.parse_args(argv)
    results = run_all(a.cases or None)
    folder = S.WORKSPACE / "validation"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps({"results": [asdict(r) for r in results],
                                                    "findings": FINDINGS}, indent=1, default=float))
    (folder / "report.html").write_text(report_html(results))
    for r in results:
        print(f"{r.status.upper():4}  {r.id:12} {r.seconds:6.1f} s  {r.note}")
    print(f"report: {folder / 'report.html'}")
    return 0 if all(r.status == "pass" or r.id in a.allow for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
