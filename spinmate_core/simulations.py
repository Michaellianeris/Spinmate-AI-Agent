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
MASKS = WORKSPACE / "masks"                  # PNG shapes of mm2d films (mask=...)
for d in (RUNS, SWEEPS, INPUTS, MASKS):
    d.mkdir(parents=True, exist_ok=True)

MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL") or max(1, (os.cpu_count() or 2) - 1))
MAX_SWEEP_RUNS = int(os.environ.get("MAX_SWEEP_RUNS", "400"))
RESULT_FILES = ["Two_Spin_Dynamics.png", "output1.dat", "output2.dat", "neel_z.dat",
                "parameters.txt", "summary.json", "simulation_parameters.log", "stderr.log",
                "continuation.json", "chain.json",
                # engine plug-ins (mm2d, mumax3)
                "texture_3d_2d.png", "texture_3d.png", "texture_2d.png", "m_final.npz", "energy.dat", "input.mx3",
                "spinwave_3d_2d.png", "spinwave_3d.png", "spinwave_2d.png", "spinwave_map.png", "spinwave_profile.png", "dispersion.png", "skyrmion_track.png", "track.dat",
                "spinwave.npz", "animation.gif", "animation.mp4", "rotate.gif", "rotate.mp4", "surface.json", "mask.png",
                "table.txt"]

_lock = threading.Lock()
_neel_cache = {}      # (run_id, discard) -> mean |n_z|; finished runs do not change
_plot_key = {}        # sweep_id -> (done count, discard) of the last plot drawn
_series_cache = {}    # run_id -> (mtime key, array); newest few only
_procs = {}
# the desktop .exe has no python -m: it starts runs as "SpinMate.exe --runner"
RUNNER_CMD = [sys.executable, "--runner"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "spinmate_core.runner"]
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
        if not s or "neel_z_switches" in s or s.get("engine") or not (d / "output1.dat").exists():
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
    from . import engines   # here, not at the top: the macrospin engine imports this module
    cmd = engines.get((_meta(run_id) or {}).get("engine")).run_command()
    path = os.pathsep.join(x for x in (str(REPO), os.environ.get("PYTHONPATH", "")) if x)   # plug-ins outside site-packages
    env = dict(os.environ, PYTHONPATH=path, MPLBACKEND="Agg")
    _update(run_id, status="running", started=time.time())
    with open(run_dir / "stdout.log", "w") as out, open(run_dir / "stderr.log", "w") as err:
        proc = subprocess.Popen(cmd, cwd=run_dir, stdout=out, stderr=err, env=env,
                                creationflags=NO_WINDOW)
        _procs[run_id] = proc
        code = proc.wait()
    _procs.pop(run_id, None)
    if (_meta(run_id) or {}).get("status") == "cancelled":
        return
    ok = code == 0 and (run_dir / "summary.json").exists()
    _update(run_id, status="done" if ok else "failed", finished=time.time(), returncode=code)


def _check(parameters_text, engine):
    """Validation result as a dict; the macrospin path is the original one."""
    if not engine or engine == "macrospin":
        return P.validate(parameters_text, sec_per_step())
    from dataclasses import asdict
    from . import engines
    try:
        v = asdict(engines.get(engine).validate(parameters_text))
    except KeyError as e:
        return {"ok": False, "error": str(e.args[0])}
    extra = v.pop("extra") or {}
    v["cleaned_text"] = extra.get("cleaned_text", parameters_text)
    return v


def start_run(parameters_text, label="", sweep_id=None, sweep_value=None, parent_run=None, t_offset=None,
              engine=None, sweep_value2=None):
    check = _check(parameters_text, engine)
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
        **({"sweep_value2": sweep_value2} if sweep_value2 is not None else {}),
        "parent_run": parent_run, "t_offset_s": t_offset, "engine": engine or "macrospin",
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
    if m.get("engine", "macrospin") != "macrospin":
        return {"ok": False, "error": f"continue_run works with the macrospin engine; {run_id} ran on {m['engine']}"}
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


def estimate(parameters_text, runs=1, engine=None):
    """Wall time of `runs` copies of this run on the queue, or None if the text is invalid."""
    check = _check(parameters_text, engine)
    if not check["ok"]:
        return None
    waves = -(-max(1, runs) // MAX_PARALLEL)
    return check["estimated_runtime_s"] * waves


# ---------- sweeps

MICRO = ("mm2d", "mm3d")


def _micro_override(engine, text, name, value):
    """Parameter text of a micromagnetic engine with name = value; B_ext.z sets one component of a vector."""
    import importlib
    mod = importlib.import_module(f"spinmate_core.{engine}")
    base, comp = (name.split(".") + [None])[:2]
    kind = mod.PARAMS[base][1]
    if comp:
        vec = list(mod.parse(text)[base])
        vec["xyz".index(comp)] = float(value)
        line = f"--{base}={','.join(repr(float(c)) for c in vec)}"
    else:
        line = f"--{base}={int(value) if kind == 'int' else repr(float(value))}"
    return (text or "").rstrip("\n") + "\n" + line + "\n"


def micro_sweepable(engine):
    """Parameters of mm2d / mm3d that can be swept: numbers, and the components of vectors (B_ext.z)."""
    import importlib
    mod = importlib.import_module(f"spinmate_core.{engine}")
    out = []
    for k, v in mod.PARAMS.items():
        if v[1] in ("float", "int") and k not in ("max_iter", "seed", "relax_first", "animation"):
            out.append(k)
        elif v[1] == "vector":
            out += [f"{k}.{c}" for c in "xyz"]
    return out


def _start_micro_sweep(engine, parameters_text, parameter, values, label, parameter2, values2):
    ok = micro_sweepable(engine)
    for name in filter(None, (parameter, parameter2)):
        if name not in ok:
            return {"ok": False, "error": f"'{name}' cannot be swept in {engine}; one of: {', '.join(ok)}"}
    values = [float(v) for v in values]
    values2 = [float(v) for v in values2] if parameter2 else [None]
    if not values or not values2:
        return {"ok": False, "error": "no values given"}
    n = len(values) * len(values2)
    if n > MAX_SWEEP_RUNS:
        return {"ok": False, "error": f"{n} runs exceeds MAX_SWEEP_RUNS={MAX_SWEEP_RUNS}"}
    texts, est = [], 0.0
    for v2 in values2:                                # check every point before starting any run
        for v in values:
            text = _micro_override(engine, parameters_text, parameter, v)
            if parameter2:
                text = _micro_override(engine, text, parameter2, v2)
            c = _check(text, engine)
            if not c["ok"]:
                where = f"{parameter}={v:g}" + (f", {parameter2}={v2:g}" if parameter2 else "")
                return {"ok": False, "error": f"{where}: {c['error']}"}
            texts.append((v, v2, text)); est += c["estimated_runtime_s"] or 0
    sweep_id = _next_id(SWEEPS, "s")
    run_ids = []
    for v, v2, text in texts:
        name = f"{label or sweep_id} {parameter}={v:g}" + (f" {parameter2}={v2:g}" if parameter2 else "")
        r = start_run(text, label=name, sweep_id=sweep_id, sweep_value=v, engine=engine, sweep_value2=v2)
        run_ids.append(r["run_id"])
    _write(SWEEPS / sweep_id / "meta.json", {
        "id": sweep_id, "label": label, "parameter": parameter, "values": [t[0] for t in texts],
        "parameter2": parameter2, "values2": [t[1] for t in texts] if parameter2 else None,
        "run_ids": run_ids, "created": time.time(), "engine": engine,
    })
    catalog.sync_sweep(SWEEPS / sweep_id)
    return {"ok": True, "sweep_id": sweep_id, "runs": len(run_ids), "parallel": MAX_PARALLEL,
            "estimated_wall_time_s": round(est / MAX_PARALLEL, 1)}


def start_sweep(parameters_text, parameter, values, label="", engine=None, parameter2=None, values2=None):
    if engine in MICRO:
        return _start_micro_sweep(engine, parameters_text, parameter, values, label, parameter2, values2 or [])
    if engine and engine != "macrospin":
        return {"ok": False, "error": f"sweeps run on macrospin, mm2d and mm3d, not on {engine}"}
    if parameter2:
        return {"ok": False, "error": "a two-parameter sweep (phase diagram) is for mm2d and mm3d"}
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
    if m.get("engine") in MICRO:
        return _micro_sweep_info(sweep_id, m)
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


PHASE = {"skyrmion": "skyrmion", "bubble": "skyrmion", "antiskyrmion": "skyrmion", "domains": "stripes",
         "skyrmionium": "skyrmionium", "uniform": "uniform", "unsettled": "unsettled"}
PHASE_COLOURS = {"uniform": "#C9C2DA", "skyrmion": "#1F4FD1", "skyrmions": "#5B8CFF", "stripes": "#D1495B",
                 "skyrmionium": "#8E44AD", "hopfion": "#1F4FD1", "hopfion2": "#5B8CFF", "skyrmion_tube": "#2A9D8F",
                 "chiral_bobber": "#E9A23B", "toron": "#D1495B", "unsettled": "#888888", "failed": "#000000"}


def phase_of(summary):
    """The phase a micromagnetic run ended in, for sweeps and phase diagrams."""
    t = (summary or {}).get("texture") or "unsettled"
    if t.startswith("Q="):
        return "skyrmions" if abs(int(t[2:])) > 1 else "skyrmion" if t[2:] in ("1", "-1") else "stripes"
    return PHASE.get(t, t)


def _micro_sweep_info(sweep_id, m):
    rows, counts = [], {}
    vals2 = m.get("values2") or [None] * len(m["run_ids"])
    for rid, v, v2 in zip(m["run_ids"], m["values"], vals2):
        r = run_info(rid) or {"status": "missing"}
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        row = {"run_id": rid, "value": v, "status": r["status"]}
        if v2 is not None:
            row["value2"] = v2
        if r["status"] == "done":
            s = r.get("summary") or {}
            row["phase"] = phase_of(s)
            row["texture"] = s.get("texture")
            for k in ("topological_charge", "hopf_index", "radius_nm", "rings_nm", "bloch_point_count",
                      "energy_final_J", "relaxed", "speed_mps", "hall_angle_deg", "wavelength_nm"):
                if s.get(k) is not None:
                    row[k] = s[k]
        elif r["status"] == "failed":
            row["phase"] = "failed"
        rows.append(row)
    info = dict(m, status_counts=counts, rows=rows)
    if counts.get("done") and not counts.get("running") and not counts.get("queued"):
        key = ("micro", counts["done"])
        if _plot_key.get(sweep_id) != key or not (SWEEPS / sweep_id / "sweep_plot.png").exists():
            _micro_sweep_plot(sweep_id, m, rows)
            _plot_key[sweep_id] = key
        info["plot"] = "sweep_plot.png"
    return info


def _axis(engine, name, values):
    """Axis label with the unit and an SI prefix chosen from the values, and the tick formatter."""
    import importlib
    import math
    from matplotlib.ticker import FuncFormatter
    mod = importlib.import_module(f"spinmate_core.{engine}")
    unit = mod.PARAMS.get(name.split(".")[0], (0, 0, ""))[2]
    unit = {"J/m^2": "J/m²", "J/m^3": "J/m³", "A/m^2": "A/m²"}.get(unit, unit)
    big = max((abs(v) for v in values), default=0) or 1
    e3 = int(math.floor(math.log10(big) / 3)) * 3
    prefix = {-12: "p", -9: "n", -6: "µ", -3: "m", 0: "", 3: "k", 6: "M", 9: "G"}.get(e3)
    if prefix is None or not unit:
        e3, prefix = 0, ""
    scale = 10.0 ** e3
    label = f"{name} ({prefix}{unit})" if unit else name
    return label, FuncFormatter(lambda v, _: f"{v / scale:g}")


def _critical_D(m):
    """4 sqrt(A Keff) / pi of the first run of an mm2d sweep (local demag, ferromagnet), or None."""
    try:
        from .mm2d import parse
        v = parse((RUNS / m["run_ids"][0] / "parameters.txt").read_text())
        if v["lattice"] != "ferro" or v["demag"] != "local":
            return None
        keff = v["Ku"] - 0.5 * 4e-7 * 3.141592653589793 * v["Msat"] ** 2
        return 4 * (v["Aex"] * keff) ** 0.5 / 3.141592653589793 if keff > 0 else None
    except Exception:
        return None


def _micro_sweep_plot(sweep_id, m, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    engine, p1, p2 = m["engine"], m["parameter"], m.get("parameter2")
    done = [r for r in rows if r.get("phase")]
    phases = sorted({r["phase"] for r in done}, key=lambda k: list(PHASE_COLOURS).index(k) if k in PHASE_COLOURS else 99)
    col = lambda ph: PHASE_COLOURS.get(ph, "#444444")
    l1, f1 = _axis(engine, p1, [r["value"] for r in done])
    if p2:                                            # phase diagram: one coloured cell per run
        xs, ys = sorted({r["value"] for r in done}), sorted({r["value2"] for r in done})
        fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=150)
        import numpy as np
        def edges(v):
            v = np.asarray(v, float)
            if len(v) == 1:
                return np.array([v[0] - 0.5, v[0] + 0.5]) if v[0] == 0 else v[0] * np.array([0.95, 1.05])
            mid = (v[1:] + v[:-1]) / 2
            return np.concatenate([[v[0] - (mid[0] - v[0])], mid, [v[-1] + (v[-1] - mid[-1])]])
        ex, ey = edges(xs), edges(ys)
        for r in done:
            i, j = xs.index(r["value"]), ys.index(r["value2"])
            ax.add_patch(plt.Rectangle((ex[i], ey[j]), ex[i + 1] - ex[i], ey[j + 1] - ey[j], facecolor=col(r["phase"]),
                                       edgecolor="white", lw=0.6))
            if r.get("radius_nm") and r["phase"] == "skyrmion":
                ax.text((ex[i] + ex[i + 1]) / 2, (ey[j] + ey[j + 1]) / 2, f"{r['radius_nm']:.0f}", ha="center",
                        va="center", fontsize=6.5, color="white")
        ax.set_xlim(ex[0], ex[-1]); ax.set_ylim(ey[0], ey[-1])
        l2, f2 = _axis(engine, p2, [r["value2"] for r in done])
        ax.xaxis.set_major_formatter(f1); ax.yaxis.set_major_formatter(f2)
        ax.set_xlabel(l1); ax.set_ylabel(l2)
        ax.set_title("phase diagram (numbers: skyrmion radius, nm)", fontsize=9)
        dc = _critical_D(m) if engine == "mm2d" and p1 == "D" else None
        if dc and ex[0] < dc < ex[-1]:                # the stripe instability of the uniform state at B = 0
            ax.axvline(dc, color="#14111F", ls="--", lw=1.1)
            ax.text(dc, ey[-1], " D_c", va="top", fontsize=8, color="#14111F")
        ax.legend(handles=[Patch(color=col(ph), label=ph.replace("_", " ")) for ph in phases], fontsize=8,
                  loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    else:                                             # radius (or Q) against the parameter, coloured by phase
        fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=150)
        size = [(r["value"], r.get("radius_nm") or (max(r["rings_nm"]) if r.get("rings_nm") else 0.0), r["phase"])
                for r in done]
        size.sort()
        ax.plot([p[0] for p in size], [p[1] for p in size], "-", color="#B9B0CC", lw=1, zorder=1)
        for ph in phases:
            pts = [p for p in size if p[2] == ph]
            ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=28, color=col(ph), label=ph.replace("_", " "), zorder=2)
        ax.xaxis.set_major_formatter(f1)
        ax.set_xlabel(l1); ax.set_ylabel("radius (nm)" if engine == "mm2d" else "outer ring (nm)")
        ax.grid(True, lw=0.4, alpha=0.5)
        ax.legend(fontsize=8, frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1))
    fig.tight_layout()
    fig.savefig(SWEEPS / sweep_id / "sweep_plot.png")
    plt.close(fig)


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
    return path if name in ("sweep_neel_z.png", "sweep_plot.png") and path.exists() else None


def list_sweeps():
    out = []
    for d in sorted(SWEEPS.iterdir(), reverse=True):
        m = _read(d / "meta.json")
        if m:
            done = sum((_meta(r) or {}).get("status") == "done" for r in m["run_ids"])
            out.append({"id": m["id"], "label": m["label"], "parameter": m["parameter"],
                        "parameter2": m.get("parameter2"), "engine": m.get("engine") or "macrospin",
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


MAX_MASK_BYTES = 2_000_000


def save_mask(name, raw):
    """Stores an uploaded PNG mask (the shape of an mm2d film). Returns (name, error)."""
    import io
    from PIL import Image
    if len(raw) > MAX_MASK_BYTES:
        return None, "the picture is larger than 2 MB"
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            fmt, size = im.format, im.size
    except Exception:
        return None, "not a picture"
    if fmt != "PNG":
        return None, "the mask must be a PNG"
    if max(size) > 4096:
        return None, "the picture is larger than 4096 pixels"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name or "mask").stem)[:60] or "mask"
    name = stem + ".png"
    (MASKS / name).write_bytes(raw)
    return name, None


def list_masks():
    from .mm2d import geometry
    return {"built_in": list(geometry.BUILT_IN), "uploaded": sorted(p.name for p in MASKS.glob("*.png"))}
