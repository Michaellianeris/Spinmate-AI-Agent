"""Run catalog in Postgres: one row per run and per sweep, for SQL questions across runs.

Optional. Without DATABASE_URL every function is a no-op and query() says the catalog is off.
Trajectories stay in the run folders; the catalog keeps parameters, summaries and links.
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("catalog")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
QUERY_TIMEOUT_MS = int(os.environ.get("QUERY_TIMEOUT_MS") or 5000)
MAX_ROWS = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id text PRIMARY KEY,
  label text,
  kind text NOT NULL DEFAULT 'run',
  status text,
  created timestamptz,
  started timestamptz,
  finished timestamptz,
  steps integer,
  sweep_id text,
  sweep_value double precision,
  parent_run text,
  t_offset_s double precision,
  chain text[],
  params jsonb,
  changed jsonb,
  t_start_s double precision,
  t_end_s double precision,
  runtime_s double precision,
  order_initial text,
  order_final text,
  m1_dot_m2_initial double precision,
  m1_dot_m2_final double precision,
  neel_z_sign_changes integer,
  first_neel_z_sign_change_s double precision,
  neel_z_abs_mean_second_half double precision,
  freq_m1x_ghz double precision,
  freq_neel_z_ghz double precision,
  summary jsonb,
  updated timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_params_idx ON runs USING gin (params);
CREATE INDEX IF NOT EXISTS runs_sweep_idx ON runs (sweep_id);
CREATE INDEX IF NOT EXISTS runs_parent_idx ON runs (parent_run);
CREATE TABLE IF NOT EXISTS sweeps (
  sweep_id text PRIMARY KEY,
  label text,
  parameter text,
  sweep_values double precision[],
  run_ids text[],
  created timestamptz
);
"""

RUN_COLS = ["run_id", "label", "kind", "status", "created", "started", "finished", "steps",
            "sweep_id", "sweep_value", "parent_run", "t_offset_s", "chain", "params", "changed",
            "t_start_s", "t_end_s", "runtime_s", "order_initial", "order_final",
            "m1_dot_m2_initial", "m1_dot_m2_final", "neel_z_sign_changes",
            "first_neel_z_sign_change_s", "neel_z_abs_mean_second_half",
            "freq_m1x_ghz", "freq_neel_z_ghz", "summary"]

_bg = ThreadPoolExecutor(max_workers=1)   # one writer keeps updates in order
_ready = False
_down_until = 0.0
_params_cache = {}                        # run_id -> (mtime, params dict)
_lock = threading.Lock()


def enabled():
    return bool(DATABASE_URL)


def _connect(autocommit=True):
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=3, autocommit=autocommit)


def _available():
    """False while the database was unreachable in the last 30 s, so runs never wait on it."""
    global _ready, _down_until
    if not enabled() or time.time() < _down_until:
        return False
    if _ready:
        return True
    with _lock:
        if _ready:
            return True
        try:
            with _connect() as c:
                c.execute(SCHEMA)
            _ready = True
        except Exception as e:
            _down_until = time.time() + 30
            log.warning("catalog unavailable: %s", e)
    return _ready


def _ts(v):
    return datetime.fromtimestamp(v, tz=timezone.utc) if isinstance(v, (int, float)) else None


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def _params(run_dir):
    """All resolved parameter values (defaults included) from the original parser."""
    from . import params as P
    path = run_dir / "parameters.txt"
    if not path.exists():
        return None
    key = path.stat().st_mtime
    hit = _params_cache.get(run_dir.name)
    if hit and hit[0] == key:
        return hit[1]
    args, err = P._parse(path.read_text())
    if err:
        return None
    out = {k: (list(v) if isinstance(v, (list, tuple)) else v) for k, v in vars(args).items() if k != "input_file"}
    _params_cache[run_dir.name] = (key, out)
    return out


def _run_row(run_dir):
    m = _read(run_dir / "meta.json")
    if not m:
        return None
    s = _read(run_dir / "summary.json") or {}
    cont = _read(run_dir / "continuation.json") or {}
    return {
        "run_id": run_dir.name, "label": m.get("label") or "", "kind": m.get("kind") or "run",
        "status": m.get("status"), "created": _ts(m.get("created")), "started": _ts(m.get("started")),
        "finished": _ts(m.get("finished")), "steps": m.get("steps"),
        "sweep_id": m.get("sweep_id"), "sweep_value": m.get("sweep_value"),
        "parent_run": m.get("parent_run") or cont.get("parent_run"),
        "t_offset_s": m.get("t_offset_s") or cont.get("t_offset_s"),
        "chain": m.get("chain"),
        "params": json.dumps(_params(run_dir)), "changed": json.dumps(m.get("changed_from_defaults")),
        "t_start_s": s.get("t_start_s"), "t_end_s": m.get("t_end_s") or s.get("t_end_s"),
        "runtime_s": s.get("runtime_s"),
        "order_initial": s.get("order_initial"), "order_final": s.get("order_final"),
        "m1_dot_m2_initial": s.get("m1_dot_m2_initial"), "m1_dot_m2_final": s.get("m1_dot_m2_final"),
        "neel_z_sign_changes": s.get("neel_z_sign_changes"),
        "first_neel_z_sign_change_s": s.get("first_neel_z_sign_change_s"),
        "neel_z_abs_mean_second_half": s.get("neel_z_abs_mean_second_half"),
        "freq_m1x_ghz": s.get("dominant_freq_m1x_GHz"), "freq_neel_z_ghz": s.get("dominant_freq_neel_z_GHz"),
        "summary": json.dumps(s) if s else None,
    }


_UPSERT_RUN = (
    f"INSERT INTO runs ({', '.join(RUN_COLS)}) VALUES ({', '.join('%(' + c + ')s' for c in RUN_COLS)}) "
    f"ON CONFLICT (run_id) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in RUN_COLS if c != "run_id") + ", updated = now()"
)

_UPSERT_SWEEP = (
    "INSERT INTO sweeps (sweep_id, label, parameter, sweep_values, run_ids, created) "
    "VALUES (%(sweep_id)s, %(label)s, %(parameter)s, %(values)s, %(run_ids)s, %(created)s) "
    "ON CONFLICT (sweep_id) DO UPDATE SET label = EXCLUDED.label, parameter = EXCLUDED.parameter, "
    "sweep_values = EXCLUDED.sweep_values, run_ids = EXCLUDED.run_ids, created = EXCLUDED.created"
)


def sync_run_now(run_dir):
    if not _available():
        return False
    row = _run_row(Path(run_dir))
    if not row:
        return False
    with _connect() as c:
        c.execute(_UPSERT_RUN, row)
    return True


def sync_sweep_now(sweep_dir):
    if not _available():
        return False
    m = _read(Path(sweep_dir) / "meta.json")
    if not m:
        return False
    with _connect() as c:
        c.execute(_UPSERT_SWEEP, {"sweep_id": m["id"], "label": m.get("label") or "",
                                  "parameter": m.get("parameter"), "values": m.get("values") or [],
                                  "run_ids": m.get("run_ids") or [], "created": _ts(m.get("created"))})
    return True


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception as e:
        log.warning("catalog write failed: %s", e)


def sync_run(run_dir):
    """Queue an upsert of one run; never blocks or raises."""
    if enabled():
        _bg.submit(_safe, sync_run_now, run_dir)


def sync_sweep(sweep_dir):
    if enabled():
        _bg.submit(_safe, sync_sweep_now, sweep_dir)


def backfill(runs_dir, sweeps_dir):
    """Add every run and sweep already on disk (runs made before the catalog existed)."""
    if not enabled():
        return
    for d in sorted(Path(runs_dir).iterdir()):
        if (d / "meta.json").exists():
            sync_run(d)
    for d in sorted(Path(sweeps_dir).iterdir()):
        if (d / "meta.json").exists():
            sync_sweep(d)


def wait_idle(timeout=30.0):
    """Block until queued writes are done (used by tests and by query())."""
    done = threading.Event()
    _bg.submit(done.set)
    return done.wait(timeout)


# ---------- read-only SQL for the agent

_FORBIDDEN = re.compile(r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|"
                        r"vacuum|analyze|cluster|reindex|comment|lock|listen|notify|call|do|set|reset|"
                        r"prepare|execute|refresh|security|pg_sleep|pg_read|pg_write|lo_import|lo_export|"
                        r"dblink)\b", re.I)


def _check_sql(sql):
    s = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql or "", flags=re.S).strip().rstrip(";").strip()
    if not s:
        return None, "empty query"
    if ";" in s:
        return None, "one statement only"
    if not re.match(r"(select|with)\b", s, re.I):
        return None, "only SELECT (or WITH ... SELECT) queries are allowed"
    bad = _FORBIDDEN.search(re.sub(r"'(?:[^']|'')*'", "''", s))
    if bad:
        return None, f"'{bad.group(0)}' is not allowed in a read-only query"
    return s, None


def _jsonable(v):
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "is_finite"):        # Decimal
        return float(v)
    return v


def query(sql, limit=200):
    """Run one read-only SELECT. Returns columns, rows (at most limit) and the total row count."""
    if not enabled():
        return {"ok": False, "error": "the run catalog is off: set DATABASE_URL (docker compose starts Postgres)"}
    if not _available():
        return {"ok": False, "error": "the run catalog database is not reachable right now"}
    sql, err = _check_sql(sql)
    if err:
        return {"ok": False, "error": err}
    limit = max(1, min(int(limit or 200), MAX_ROWS))
    wait_idle(10)                      # include runs that finished a moment ago
    import psycopg
    try:
        with _connect(autocommit=False) as c:
            with c.transaction():
                c.execute("SET TRANSACTION READ ONLY")
                c.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
                cur = c.execute(sql)
                cols = [d.name for d in cur.description]
                rows = [[_jsonable(v) for v in r] for r in cur.fetchmany(limit + 1)]
    except psycopg.Error as e:
        msg = (getattr(e, "diag", None) and e.diag.message_primary) or str(e)
        return {"ok": False, "error": f"SQL error: {msg}"}
    truncated = len(rows) > limit
    return {"ok": True, "columns": cols, "rows": rows[:limit], "row_count": min(len(rows), limit),
            "truncated": truncated, "sql": sql}


def status():
    if not enabled():
        return {"enabled": False}
    return {"enabled": True, "reachable": _available()}
