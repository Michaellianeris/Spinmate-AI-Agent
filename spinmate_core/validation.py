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
    return {"Ms": Ms, "g0": g0, "K": 2 * Ku / (MU0 * Ms ** 2), "J": 4 * abs(A0) / (MU0 * Ms ** 2 * l ** 2),
            "F0": g0 * Ms / (2 * np.pi)}   # frequency unit: fields in the solver are in units of Ms


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
    # energy density / (mu0 Ms^2) for the field the solver uses (anisotropy as measured below: 2 Ku)
    E = c["J"] * np.sum(a * b, 1) - c["K"] * (a[:, 2] ** 2 + b[:, 2] ** 2)
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
    note = ""
    if not ok_f0 and _rel(f[0.0], 2 * f0_exp) < 0.01:
        note = ("The field slope is right, but f(H = 0) is twice the anisotropy frequency: the solver acts with "
                "2 Ku. See finding F1.")
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
    exp1 = c["F0"] * np.sqrt(c["K"] * (2 * c["J"] + c["K"]))
    exp2 = c["F0"] * np.sqrt(2 * c["K"] * (2 * c["J"] + 2 * c["K"]))
    ok = _rel(f, exp1) < 0.01
    note = "" if ok else (f"Matches 2 Ku within {100 * _rel(f, exp2):.1f} %. See finding F1." if _rel(f, exp2) < 0.01 else "")
    return Result("afmr", "Antiferromagnetic resonance, A0 < 0, H = 0", "pass" if ok else "fail",
                  {"f GHz": f / 1e9}, {"f GHz": exp1 / 1e9, "f GHz with 2 Ku": exp2 / 1e9}, "1 %", note, [rid])


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
                  note="ω is the measured precession frequency, so this case does not depend on F1.", runs=[rid])


CASES = {"norm": norm, "determinism": determinism, "energy": energy, "kittel": kittel, "afmr": afmr,
         "convergence": convergence, "damping": damping}
FAST = ("norm", "determinism", "energy")

FINDINGS = [
    ("F1", "Static anisotropy counted twice",
     "In macrospin/current.py, ku() returns the static K when flag2 (Ku(t)) is off, and solver.py adds it again "
     "through the AC anisotropy term along u_ani_AC, which defaults to the easy axis. The dynamics therefore "
     "use 2 Ku: the AFMR and Kittel frequencies match 2 Ku, not Ku. Either return 0 from ku() when flag2 is "
     "off, or document that the effective anisotropy is 2 Ku. The original code is not changed by SpinMate."),
    ("F2", "RK4 weight 0.166 instead of 1/6",
     "solver.py RK4() uses 0.166 * h; 1/6 = 0.1667. The default integrator is Heun, so runs are not affected."),
    ("F3", "Thermal field direction",
     "solver.py draws the thermal field direction from np.random.rand(3), which only has positive components; "
     "an isotropic field needs a normal distribution. It only matters with Temp_Ampl > 0."),
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
<h2>Findings in the original code</h2><ul>{finds}</ul></body></html>"""


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
