"""Background simulation runs and parameter sweeps, stored under WORKSPACE."""
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import catalog
from . import params as P

REPO = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ.get("WORKSPACE", REPO / "workspace")).resolve()
RUNS = WORKSPACE / "runs"
SWEEPS = WORKSPACE / "sweeps"
INPUTS = WORKSPACE / "inputs"
for d in (RUNS, SWEEPS, INPUTS):
    d.mkdir(parents=True, exist_ok=True)

MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL") or max(1, (os.cpu_count() or 2) - 1))
MAX_SWEEP_RUNS = int(os.environ.get("MAX_SWEEP_RUNS", "400"))
RESULT_FILES = ["Two_Spin_Dynamics.png", "output1.dat", "output2.dat", "neel_z.dat",
                "parameters.txt", "summary.json", "simulation_parameters.log", "stderr.log",
                "continuation.json", "chain.json"]

_lock = threading.Lock()
_neel_cache = {}      # (run_id, discard) -> mean |n_z|; finished runs do not change
_plot_key = {}        # sweep_id -> (done count, discard) of the last plot drawn
_series_cache = {}    # run_id -> (mtime key, array); newest few only
_procs = {}
# the desktop .exe has no python -m: it starts runs as "SpinMate.exe --runner"
RUNNER_CMD = [sys.executable, "--runner"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "app.runner"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window per run on Windows
_pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL)


# ---------- storage helpers

def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def _write(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def _next_id(folder, prefix):
    with _lock:
        nums = [int(m.group(1)) for p in folder.iterdir()
                if (m := re.fullmatch(prefix + r"(\d+)", p.name))]
        new = folder / f"{prefix}{(max(nums) + 1 if nums else 1):04d}"
        new.mkdir()
        return new.name


def _meta(run_id):
    return _read(RUNS / run_id / "meta.json")


def _update(run_id, **kw):
    with _lock:
        m = _meta(run_id) or {}
        m.update(kw)
        _write(RUNS / run_id / "meta.json", m)
    catalog.sync_run(RUNS / run_id)
    return m


def sec_per_step():
    """Median speed of finished runs; falls back to the default estimate."""
    vals = []
    for d in RUNS.iterdir():
        m, s = _read(d / "meta.json"), _read(d / "summary.json")
        if m and s and m.get("status") == "done" and m.get("steps") and m.get("kind") != "combined":
            vals.append(s["runtime_s"] / m["steps"])
    if not vals:
        return P.DEFAULT_SEC_PER_STEP
    vals.sort()
    return vals[len(vals) // 2]


def recover_interrupted():
    """Runs left queued/running by a previous server process cannot resume."""
    for d in RUNS.iterdir():
        m = _read(d / "meta.json")
        if m and m.get("status") in ("queued", "running"):
            m["status"] = "interrupted"
            _write(d / "meta.json", m)
            catalog.sync_run(d)


def upgrade_summaries():
    """Recompute summaries written before neel_z_switches / settle_time_s existed (once, at startup)."""
    from . import runner
    for d in sorted(RUNS.iterdir()):
        s = _read(d / "summary.json")
        if not s or "neel_z_switches" in s or not (d / "output1.dat").exists():
            continue
        keep = {k: s[k] for k in ("t_offset_s", "chain", "segment_starts_ns") if k in s}
        try:
            runner.summarise(s.get("runtime_s") or 0.0, folder=d, extra=keep)
        except Exception:
            pass


# ---------- single runs

def _execute(run_id):
    run_dir = RUNS / run_id
    if (_meta(run_id) or {}).get("status") == "cancelled":
        return
    env = dict(os.environ, PYTHONPATH=str(REPO), MPLBACKEND="Agg")
    _update(run_id, status="running", started=time.time())
    with open(run_dir / "stdout.log", "w") as out, open(run_dir / "stderr.log", "w") as err:
        proc = subprocess.Popen(RUNNER_CMD, cwd=run_dir, stdout=out, stderr=err, env=env,
                                creationflags=NO_WINDOW)
        _procs[run_id] = proc
        code = proc.wait()
    _procs.pop(run_id, None)
    if (_meta(run_id) or {}).get("status") == "cancelled":
        return
    ok = code == 0 and (run_dir / "summary.json").exists()
    _update(run_id, status="done" if ok else "failed", finished=time.time(), returncode=code)


def start_run(parameters_text, label="", sweep_id=None, sweep_value=None, parent_run=None, t_offset=None):
    check = P.validate(parameters_text, sec_per_step())
    if not check["ok"]:
        return {"ok": False, "error": check["error"]}
    run_id = _next_id(RUNS, "r")
    run_dir = RUNS / run_id
    (run_dir / "parameters.txt").write_text(check["cleaned_text"])
    if parent_run:
        _write(run_dir / "continuation.json", {"parent_run": parent_run, "t_offset_s": t_offset})
    _write(run_dir / "meta.json", {
        "id": run_id, "label": label or "", "status": "queued", "created": time.time(),
        "steps": check["steps"], "estimated_runtime_s": check["estimated_runtime_s"],
        "changed_from_defaults": check["changed_from_defaults"], "warnings": check["warnings"],
        "sweep_id": sweep_id, "sweep_value": sweep_value,
        "parent_run": parent_run, "t_offset_s": t_offset,
    })
    catalog.sync_run(run_dir)
    _pool.submit(_execute, run_id)
    return {"ok": True, "run_id": run_id, "steps": check["steps"],
            "estimated_runtime_s": check["estimated_runtime_s"], "warnings": check["warnings"]}


def cancel(run_id):
    m = _meta(run_id) if _valid(run_id, "r") else None
    if not m:
        return {"ok": False, "error": f"no run {run_id}"}
    if m["status"] not in ("queued", "running"):
        return {"ok": False, "error": f"{run_id} is already {m['status']}"}
    _update(run_id, status="cancelled", finished=time.time())
    proc = _procs.get(run_id)
    if proc:
        proc.terminate()
    return {"ok": True, "run_id": run_id, "status": "cancelled"}


def _valid(item_id, prefix):
    return bool(re.fullmatch(prefix + r"\d{4,}", str(item_id)))


def run_info(run_id, full=False):
    if not _valid(run_id, "r"):
        return None
    run_dir = RUNS / run_id
    m = _meta(run_id)
    if not m:
        return None
    info = dict(m)
    prog = _read(run_dir / "progress.json")
    if m["status"] == "running" and prog:
        info["progress"] = round(prog["step"] / max(prog["total"], 1), 4)
        rate = prog["elapsed"] / max(prog["step"], 1)
        info["eta_s"] = round(rate * (prog["total"] - prog["step"]), 1)
    elif m["status"] == "done":
        info["progress"] = 1.0
    else:
        info["progress"] = 0.0 if m["status"] == "queued" else (prog or {}).get("step", 0) / max(m["steps"], 1)
    info["summary"] = _read(run_dir / "summary.json")
    info["files"] = [f for f in RESULT_FILES if (run_dir / f).exists()]
    if full:
        info["parameters_text"] = (run_dir / "parameters.txt").read_text()
        err = run_dir / "stderr.log"
        if err.exists() and m["status"] == "failed":
            info["error_tail"] = err.read_text()[-2000:]
    return info


def list_runs(limit=50):
    ids = sorted((d.name for d in RUNS.iterdir() if (d / "meta.json").exists()), reverse=True)
    return [run_info(i) for i in ids[:limit]]


def run_file(run_id, name):
    if name not in RESULT_FILES or not _valid(run_id, "r"):
        return None
    path = RUNS / run_id / name
    return path if path.exists() else None


# ---------- continuation chains

def _last_state(run_id):
    """Final m1, m2 at full precision (last line of the output files)."""
    def last(path):
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            line = f.read().decode().strip().splitlines()[-1]
        return [float(x) for x in line.split()[1:4]]
    d = RUNS / run_id
    return last(d / "output1.dat"), last(d / "output2.dat")


def _end_time(run_id):
    """Physical time at the end of a run: t_offset + steps * h (the last stored row is the state after the last step)."""
    m = _meta(run_id)
    if m.get("kind") == "combined":
        return m["t_end_s"]
    args, _ = P._parse((RUNS / run_id / "parameters.txt").read_text())
    return float(m.get("t_offset_s") or 0.0) + int(args.t / args.h) * args.h


def continue_run(run_id, extra_t=None, extra_parameters="", label=""):
    """New run that starts from the final state of run_id at time t_end, with the same parameters
    (plus extra_parameters, which override). Drives keep their phase through t_offset."""
    m = _meta(run_id) if _valid(run_id, "r") else None
    if not m:
        return {"ok": False, "error": f"no run {run_id}"}
    if m["status"] != "done":
        return {"ok": False, "error": f"{run_id} is {m['status']}, it must be done to continue from it"}
    text = (RUNS / run_id / "parameters.txt").read_text()
    args, _ = P._parse(text)
    m1, m2 = _last_state(run_id)
    t_offset = _end_time(run_id)
    vec = lambda v: ",".join(repr(x) for x in v)
    new = (text.rstrip("\n") + "\n# continuation of " + run_id + "\n"
           + f"--m1={vec(m1)}\n--m2={vec(m2)}\n--t={float(extra_t or args.t)!r}\n"
           + (extra_parameters.strip() + "\n" if extra_parameters.strip() else ""))
    out = start_run(new, label or f"{run_id} continued", parent_run=run_id, t_offset=t_offset)
    if out.get("ok"):
        out.update(parent_run=run_id, t_start_ns=round(t_offset * 1e9, 6),
                   t_end_ns=round((t_offset + float(extra_t or args.t)) * 1e9, 6))
        notes = []
        if args.flag0 and args.Temp > 0:
            notes.append("thermal noise: the random sequence differs from one long run")
        if args.gaussian:
            notes.append("--gaussian values are drawn again for this segment")
        if notes:
            out["notes"] = notes
    return out


def chain_of(run_id):
    """Run ids from the first segment to run_id, following parent_run links."""
    ids, cur, seen = [], run_id, set()
    while cur and cur not in seen:
        seen.add(cur)
        m = _meta(cur)
        if not m:
            break
        if m.get("kind") == "combined":
            ids = m["chain"] + ids
            break
        ids.insert(0, cur)
        cur = m.get("parent_run")
    return ids


def combine_runs(run_ids=None, run_id=None, label=""):
    """Join segments into one new run (output files, summary, plot), so the viewer, exports and
    get_run work on the whole time range. run_id alone follows its parent_run chain."""
    import numpy as np
    ids = list(run_ids or []) or (chain_of(run_id) if run_id and _valid(run_id, "r") else [])
    if len(ids) < 2:
        return {"ok": False, "error": "need at least two runs (give run_ids, or run_id of the last segment of a chain)"}
    for rid in ids:
        mm = _meta(rid) if _valid(rid, "r") else None
        if not mm or mm["status"] != "done":
            return {"ok": False, "error": f"{rid} is not a finished run"}
    a_all, b_all, bounds, warnings = [], [], [], []
    for i, rid in enumerate(ids):
        a = np.loadtxt(RUNS / rid / "output1.dat")
        b = np.loadtxt(RUNS / rid / "output2.dat")
        if i:
            parent = _meta(rid).get("parent_run")
            pm = _meta(parent) if parent and _valid(parent, "r") else None
            linked = parent == ids[i - 1] or bool(pm and pm.get("kind") == "combined" and pm["chain"][-1] == ids[i - 1])
            if linked:
                a, b = a[1:], b[1:]  # first row repeats the parent's final state
            else:
                dt = a[1, 0] - a[0, 0]
                shift = a_all[-1][-1, 0] + dt - a[0, 0]
                a[:, 0] += shift
                b[:, 0] += shift
                warnings.append(f"{rid} is not a continuation of {ids[i - 1]}: its time was shifted by "
                                f"{shift * 1e9:.4g} ns and m may jump at the boundary")
            bounds.append(float(a[0, 0]))
        a_all.append(a)
        b_all.append(b)
    A, B = np.vstack(a_all), np.vstack(b_all)
    new_id = _next_id(RUNS, "r")
    d = RUNS / new_id
    for name, arr in (("output1.dat", A), ("output2.dat", B)):
        np.savetxt(d / name, arr, fmt=["%.16E", "%.16f", "%.16f", "%.16f"])
    (d / "parameters.txt").write_text((RUNS / ids[-1] / "parameters.txt").read_text())
    from . import runner
    runtime = sum(((_read(RUNS / r / "summary.json") or {}).get("runtime_s") or 0) for r in ids)
    summary = runner.summarise(runtime, folder=d, extra={"chain": ids, "segment_starts_ns": [round(x * 1e9, 6) for x in bounds]})
    _chain_plot(d, A, B, bounds)
    _write(d / "chain.json", {"chain": ids, "segment_starts_s": bounds, "warnings": warnings})
    t_end = _end_time(ids[-1]) if not warnings else float(A[-1, 0] + (A[-1, 0] - A[-2, 0]))
    _write(d / "meta.json", {
        "id": new_id, "label": label or "combined " + " + ".join(ids), "status": "done", "kind": "combined",
        "chain": ids, "created": time.time(), "finished": time.time(), "steps": int(len(A)),
        "t_end_s": t_end, "warnings": warnings,
    })
    catalog.sync_run(d)
    return {"ok": True, "run_id": new_id, "chain": ids, "points": int(len(A)),
            "t_start_ns": round(float(A[0, 0]) * 1e9, 6), "t_end_ns": round(t_end * 1e9, 6),
            "order_initial": summary["order_initial"], "order_final": summary["order_final"],
            "warnings": warnings}


def _chain_plot(folder, A, B, bounds):
    """Same layout as the original Two_Spin_Dynamics.png, with dashed lines at the segment joins."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5, 3.2), dpi=200)
    t = A[:, 0] * 1e9
    for arr, ls, cols, sub in ((A, "-", ("green", "blue", "red"), "1"), (B, "--", ("orchid", "purple", "orange"), "2")):
        for k, (c, ax_name) in enumerate(zip(cols, "xyz")):
            ax.plot(t, arr[:, k + 1], ls, color=c, lw=0.9, label=rf"$m_{{{sub},{ax_name}}}$")
    for x in bounds:
        ax.axvline(x * 1e9, color="gray", ls=":", lw=0.8)
    ax.set_xlabel("Time (ns)")
    ax.set_ylabel(r"Magnetisation ($M/M_s$)")
    ax.tick_params(direction="in")
    ax.legend(loc="upper left", bbox_to_anchor=(1, 1), frameon=False, fontsize=6)
    fig.tight_layout()
    fig.savefig(folder / "Two_Spin_Dynamics.png")
    plt.close(fig)


# ---------- trajectories for the viewer

def _load_series(run_id):
    import numpy as np
    d = RUNS / run_id
    f1, f2 = d / "output1.dat", d / "output2.dat"
    if not (f1.exists() and f2.exists()):
        return None
    key = (f1.stat().st_mtime_ns, f2.stat().st_mtime_ns)
    with _lock:
        hit = _series_cache.get(run_id)
        if hit and hit[0] == key:
            return hit[1]
    a, b = np.loadtxt(f1), np.loadtxt(f2)
    data = np.column_stack((a[:, 0], a[:, 1:4], b[:, 1:4]))
    with _lock:
        _series_cache[run_id] = (key, data)
        while len(_series_cache) > 4:
            _series_cache.pop(next(iter(_series_cache)))
    return data


def run_series(run_id, points=1200, t0_ns=None, t1_ns=None):
    """Down-sampled m1, m2 (columns) for a time window; zooming asks for a narrower window."""
    import numpy as np
    if not _valid(run_id, "r"):
        return None
    data = _load_series(run_id)
    if data is None:
        return None
    t = data[:, 0]
    lo = 0 if t0_ns is None else int(np.searchsorted(t, t0_ns * 1e-9, "left"))
    hi = len(t) if t1_ns is None else int(np.searchsorted(t, t1_ns * 1e-9, "right"))
    lo = max(0, min(lo, len(t) - 2))
    hi = max(lo + 2, min(hi, len(t)))
    points = max(50, min(int(points), 5000))
    n = hi - lo
    idx = (np.unique(np.linspace(lo, hi - 1, points).round().astype(int))
           if n > points else np.arange(lo, hi))
    part = data[idx]

    def col(i, scale=1.0):
        return np.round(part[:, i] * scale, 6).tolist()

    return {"n_total": int(len(t)), "n_window": int(n), "n_returned": int(len(idx)),
            "t_ns": col(0, 1e9), "m1x": col(1), "m1y": col(2), "m1z": col(3),
            "m2x": col(4), "m2y": col(5), "m2z": col(6)}


def update_meta(run_id, starred=None, tags=None, label=None):
    """User annotations of a run: star, tags, label."""
    if not (_valid(run_id, "r") and _meta(run_id)):
        return None
    kw = {}
    if starred is not None:
        kw["starred"] = bool(starred)
    if tags is not None:
        kw["tags"] = sorted({re.sub(r"[^\w .+-]", "", str(t))[:30].strip() for t in tags if str(t).strip()})[:12]
    if label is not None:
        kw["label"] = str(label)[:120]
    return _update(run_id, **kw)


def delete_runs(run_ids):
    """Deletes finished runs (their folders and catalog rows). Queued or running runs are refused."""
    import shutil
    deleted, refused = [], []
    for rid in dict.fromkeys(run_ids or []):
        m = _meta(rid) if _valid(rid, "r") else None
        if not m:
            refused.append({"run_id": rid, "reason": "not found"})
        elif m.get("status") in ("queued", "running"):
            refused.append({"run_id": rid, "reason": f"{m['status']}; cancel it first"})
        else:
            shutil.rmtree(RUNS / rid, ignore_errors=True)
            with _lock:
                _series_cache.pop(rid, None)
            deleted.append(rid)
    catalog.delete_runs(deleted)
    return {"ok": True, "deleted": deleted, "refused": refused}


METRICS = ("order_initial", "order_final", "m1_dot_m2_final", "neel_z_switches", "first_neel_z_switch_s",
           "settle_time_s", "dominant_freq_m1x_GHz", "dominant_freq_neel_z_GHz", "t_start_s", "t_end_s", "runtime_s")


def compare(run_ids):
    """Side by side: key results of each run and the parameters that differ between them."""
    ids = [r for r in dict.fromkeys(run_ids or []) if _valid(r, "r")][:6]
    runs, params = [], {}
    for rid in ids:
        info = run_info(rid, full=True)
        if not info:
            continue
        args, _ = P._parse(info.get("parameters_text") or "")
        vals = {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in vars(args).items()
                if k != "input_file"} if args else {}
        params[rid] = vals
        s = info.get("summary") or {}
        runs.append({"id": rid, "label": info.get("label") or "", "status": info.get("status"),
                     "kind": info.get("kind") or "run", "metrics": {k: s.get(k) for k in METRICS}})
    if len(runs) < 2:
        return {"ok": False, "error": "need at least two existing runs to compare"}
    keys = sorted(set().union(*[set(v) for v in params.values()]))
    differ = {k: {rid: params[rid].get(k) for rid in params} for k in keys
              if len({json.dumps(params[rid].get(k)) for rid in params}) > 1}
    return {"ok": True, "run_ids": [r["id"] for r in runs], "runs": runs, "differing_parameters": differ}


def estimate(parameters_text, runs=1):
    """Wall time of `runs` copies of this run on the queue, or None if the text is invalid."""
    check = P.validate(parameters_text, sec_per_step())
    if not check["ok"]:
        return None
    waves = -(-max(1, runs) // MAX_PARALLEL)
    return check["estimated_runtime_s"] * waves


# ---------- sweeps

def start_sweep(parameters_text, parameter, values, label=""):
    if parameter not in P.float_params():
        return {"ok": False, "error": f"'{parameter}' is not a scalar parameter that can be swept"}
    values = [float(v) for v in values]
    if not values:
        return {"ok": False, "error": "no values given"}
    if len(values) > MAX_SWEEP_RUNS:
        return {"ok": False, "error": f"{len(values)} runs exceeds MAX_SWEEP_RUNS={MAX_SWEEP_RUNS}"}
    base = P.validate(parameters_text, sec_per_step())
    if not base["ok"]:
        return {"ok": False, "error": base["error"]}
    sweep_id = _next_id(SWEEPS, "s")
    run_ids = []
    for v in values:
        r = start_run(P.with_override(parameters_text, parameter, v),
                      label=f"{label or sweep_id} {parameter}={v:g}", sweep_id=sweep_id, sweep_value=v)
        if not r["ok"]:
            return {"ok": False, "error": f"{parameter}={v:g}: {r['error']}"}
        run_ids.append(r["run_id"])
    _write(SWEEPS / sweep_id / "meta.json", {
        "id": sweep_id, "label": label, "parameter": parameter, "values": values,
        "run_ids": run_ids, "created": time.time(),
    })
    catalog.sync_sweep(SWEEPS / sweep_id)
    total = base["estimated_runtime_s"] * len(values) / MAX_PARALLEL
    return {"ok": True, "sweep_id": sweep_id, "runs": len(run_ids), "parallel": MAX_PARALLEL,
            "estimated_wall_time_s": round(total, 1)}


def sweep_info(sweep_id, discard_fraction=0.0):
    if not _valid(sweep_id, "s"):
        return None
    m = _read(SWEEPS / sweep_id / "meta.json")
    if not m:
        return None
    rows, counts = [], {}
    for rid, v in zip(m["run_ids"], m["values"]):
        r = run_info(rid) or {"status": "missing"}
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        row = {"run_id": rid, "value": v, "status": r["status"]}
        if r["status"] == "done":
            row["neel_z_abs_mean"] = _neel_mean(rid, discard_fraction)
            s = r.get("summary") or {}
            row["dominant_freq_neel_z_GHz"] = s.get("dominant_freq_neel_z_GHz")
            row["neel_z_sign_changes"] = s.get("neel_z_sign_changes")
            row["neel_z_switches"] = s.get("neel_z_switches")
            row["order_final"] = s.get("order_final")
            row["neel_z_final"] = (s.get("neel_final") or [None, None, None])[2]
        rows.append(row)
    info = dict(m, status_counts=counts, rows=rows, discard_fraction=discard_fraction)
    if counts.get("done") and not counts.get("running") and not counts.get("queued"):
        key = (counts["done"], round(float(discard_fraction), 3))
        if _plot_key.get(sweep_id) != key or not (SWEEPS / sweep_id / "sweep_neel_z.png").exists():
            _sweep_plot(sweep_id, m["parameter"], rows)
            _plot_key[sweep_id] = key
        info["plot"] = "sweep_neel_z.png"
    return info


def _neel_mean(run_id, discard_fraction):
    import numpy as np
    key = (run_id, round(float(discard_fraction), 3))
    if key in _neel_cache:
        return _neel_cache[key]
    data = np.loadtxt(RUNS / run_id / "neel_z.dat")
    start = int(len(data) * max(0.0, min(discard_fraction, 0.95)))
    _neel_cache[key] = float(np.mean(data[start:, 1]))
    return _neel_cache[key]


def _sweep_plot(sweep_id, parameter, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pts = sorted((r["value"], r["neel_z_abs_mean"]) for r in rows if "neel_z_abs_mean" in r)
    x = [p[0] for p in pts]
    scale, unit = (1e9, " (GHz)") if parameter.endswith("Fr") else (1.0, "")
    fig, ax = plt.subplots(figsize=(5, 3.2), dpi=150)
    ax.plot([v / scale for v in x], [p[1] for p in pts], "o-", color="#1F4FD1", ms=3, lw=1.2)
    ax.set_xlabel(parameter + unit)
    ax.set_ylabel(r"average $|n_z|$")
    ax.grid(True, lw=0.4, alpha=0.5)
    fig.tight_layout()
    name = "sweep_neel_z.png"
    fig.savefig(SWEEPS / sweep_id / name)
    plt.close(fig)
    return name


def wait_for_run(run_id, timeout_s=60.0):
    """Block until the run leaves queued/running, or the timeout (max 120 s) passes."""
    end = time.time() + max(1.0, min(float(timeout_s), 120.0))
    while True:
        info = run_info(run_id, full=True)
        if not info or info["status"] not in ("queued", "running") or time.time() >= end:
            return info
        time.sleep(1.0)


def wait_for_sweep(sweep_id, timeout_s=60.0, discard_fraction=0.0):
    end = time.time() + max(1.0, min(float(timeout_s), 120.0))
    while True:
        info = sweep_info(sweep_id, discard_fraction)
        if not info:
            return None
        counts = info.get("status_counts", {})
        if not (counts.get("queued") or counts.get("running")) or time.time() >= end:
            return info
        time.sleep(1.0)


# ---------- export
def export_csv(run_id):
    """Whole trajectory as CSV: time, m1, m2 and the Neel vector."""
    import io
    import numpy as np
    if not _valid(run_id, "r"):
        return None
    data = _load_series(run_id)
    if data is None:
        return None
    neel = (data[:, 1:4] - data[:, 4:7]) / 2.0
    buf = io.StringIO()
    np.savetxt(buf, np.column_stack((data[:, 0], data[:, 1:7], neel)), delimiter=",", fmt="%.8e",
               header="t_s,m1x,m1y,m1z,m2x,m2y,m2z,nx,ny,nz", comments="")
    return buf.getvalue()


def export_zip(run_id):
    """All result files of a run plus the trajectory CSV."""
    import io
    import zipfile
    csv = export_csv(run_id)
    if csv is None:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in RESULT_FILES:
            p = RUNS / run_id / f
            if p.exists():
                z.write(p, f)
        z.writestr(f"{run_id}_trajectory.csv", csv)
    return buf.getvalue()


def sweep_file(sweep_id, name):
    if not _valid(sweep_id, "s"):
        return None
    path = SWEEPS / sweep_id / name
    return path if name == "sweep_neel_z.png" and path.exists() else None


def list_sweeps():
    out = []
    for d in sorted(SWEEPS.iterdir(), reverse=True):
        m = _read(d / "meta.json")
        if m:
            done = sum((_meta(r) or {}).get("status") == "done" for r in m["run_ids"])
            out.append({"id": m["id"], "label": m["label"], "parameter": m["parameter"],
                        "runs": len(m["run_ids"]), "done": done, "created": m.get("created")})
    return out


# ---------- uploaded / saved input files

def safe_name(name):
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[:80] or "parameters.txt"
    return name if name.endswith(".txt") else name + ".txt"


def save_input(name, text):
    name = safe_name(name)
    (INPUTS / name).write_text(text)
    return name


def read_input(name):
    path = INPUTS / safe_name(name)
    return path.read_text() if path.exists() else None


def list_inputs():
    return sorted(p.name for p in INPUTS.glob("*.txt"))
