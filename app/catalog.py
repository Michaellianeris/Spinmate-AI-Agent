"""Run catalog: one row per run and per sweep, for SQL questions across runs.

Postgres in Docker (DATABASE_URL=postgresql://...), SQLite in the desktop app (DATABASE_URL=sqlite:///path).
Optional: without DATABASE_URL every function is a no-op and query() says the catalog is off.
Trajectories stay in the run folders; the catalog keeps parameters, summaries and links.
"""
import contextlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("catalog")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
QUERY_TIMEOUT_MS = int(os.environ.get("QUERY_TIMEOUT_MS") or 5000)
MAX_ROWS = 500

SCHEMA_PG = """
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
  neel_z_switches integer,
  first_neel_z_switch_s double precision,
  settle_time_s double precision,
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

SCHEMA_SQLITE = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, label TEXT, kind TEXT NOT NULL DEFAULT 'run', status TEXT,
  created TEXT, started TEXT, finished TEXT, steps INTEGER, sweep_id TEXT, sweep_value REAL,
  parent_run TEXT, t_offset_s REAL, chain TEXT, params TEXT, changed TEXT,
  t_start_s REAL, t_end_s REAL, runtime_s REAL, order_initial TEXT, order_final TEXT,
  m1_dot_m2_initial REAL, m1_dot_m2_final REAL, neel_z_sign_changes INTEGER,
  first_neel_z_sign_change_s REAL, neel_z_abs_mean_second_half REAL,
  freq_m1x_ghz REAL, freq_neel_z_ghz REAL, neel_z_switches INTEGER, first_neel_z_switch_s REAL,
  settle_time_s REAL, summary TEXT,
  updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS runs_sweep_idx ON runs (sweep_id);
CREATE INDEX IF NOT EXISTS runs_parent_idx ON runs (parent_run);
CREATE TABLE IF NOT EXISTS sweeps (
  sweep_id TEXT PRIMARY KEY, label TEXT, parameter TEXT, sweep_values TEXT, run_ids TEXT, created TEXT
);
"""

RUN_COLS = ["run_id", "label", "kind", "status", "created", "started", "finished", "steps",
            "sweep_id", "sweep_value", "parent_run", "t_offset_s", "chain", "params", "changed",
            "t_start_s", "t_end_s", "runtime_s", "order_initial", "order_final",
            "m1_dot_m2_initial", "m1_dot_m2_final", "neel_z_sign_changes",
            "first_neel_z_sign_change_s", "neel_z_abs_mean_second_half",
            "freq_m1x_ghz", "freq_neel_z_ghz", "neel_z_switches", "first_neel_z_switch_s",
            "settle_time_s", "summary"]

# columns added after the first release: (name, postgres type, sqlite type)
ADDED_COLS = [("neel_z_switches", "integer", "INTEGER"), ("first_neel_z_switch_s", "double precision", "REAL"),
              ("settle_time_s", "double precision", "REAL")]

_bg = ThreadPoolExecutor(max_workers=1)   # one writer keeps updates in order
_ready = False
_down_until = 0.0
_params_cache = {}                        # run_id -> (mtime, params dict)
_lock = threading.Lock()


def enabled():
    return bool(DATABASE_URL)


def _sqlite_path():
    return DATABASE_URL[len("sqlite:///"):] if DATABASE_URL.startswith("sqlite:///") else None


def dialect():
    return "sqlite" if _sqlite_path() else "postgres"


def _connect(autocommit=True):
    path = _sqlite_path()
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return contextlib.closing(sqlite3.connect(path, timeout=10, isolation_level=None))
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=3, autocommit=autocommit)


def _execute(sql, params=None):
    """One write statement (or the schema script) with autocommit."""
    with _connect() as c:
        if params is None:
            c.executescript(sql) if _sqlite_path() else c.execute(sql)
        else:
            c.execute(sql, params)


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
            _execute(SCHEMA_SQLITE if _sqlite_path() else SCHEMA_PG)
            _migrate()
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
        "neel_z_switches": s.get("neel_z_switches"), "first_neel_z_switch_s": s.get("first_neel_z_switch_s"),
        "settle_time_s": s.get("settle_time_s"),
        "summary": json.dumps(s) if s else None,
    }


def _migrate():
    """Add columns that older catalogs do not have yet."""
    if _sqlite_path():
        with _connect() as c:
            have = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
            for name, _, lite in ADDED_COLS:
                if name not in have:
                    c.execute(f"ALTER TABLE runs ADD COLUMN {name} {lite}")
    else:
        for name, pg, _ in ADDED_COLS:
            _execute(f"ALTER TABLE runs ADD COLUMN IF NOT EXISTS {name} {pg}")


def _upsert(table, key, cols):
    ph = (lambda c: f":{c}") if _sqlite_path() else (lambda c: f"%({c})s")
    return (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(ph(c) for c in cols)}) "
            f"ON CONFLICT ({key}) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in cols if c != key)
            + (", updated = CURRENT_TIMESTAMP" if table == "runs" else ""))


SWEEP_COLS = ["sweep_id", "label", "parameter", "sweep_values", "run_ids", "created"]


def _for_sqlite(row):
    """SQLite has no timestamptz or arrays: ISO text and JSON text instead."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat(timespec="seconds")
        elif isinstance(v, list):
            v = json.dumps(v)
        out[k] = v
    return out


def sync_run_now(run_dir):
    if not _available():
        return False
    row = _run_row(Path(run_dir))
    if not row:
        return False
    _execute(_upsert("runs", "run_id", RUN_COLS), _for_sqlite(row) if _sqlite_path() else row)
    return True


def sync_sweep_now(sweep_dir):
    if not _available():
        return False
    m = _read(Path(sweep_dir) / "meta.json")
    if not m:
        return False
    row = {"sweep_id": m["id"], "label": m.get("label") or "", "parameter": m.get("parameter"),
           "sweep_values": m.get("values") or [], "run_ids": m.get("run_ids") or [], "created": _ts(m.get("created"))}
    _execute(_upsert("sweeps", "sweep_id", SWEEP_COLS), _for_sqlite(row) if _sqlite_path() else row)
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


def delete_runs_now(run_ids):
    if not _available() or not run_ids:
        return False
    ph = "?" if _sqlite_path() else "%s"
    with _connect() as c:
        for rid in run_ids:
            c.execute(f"DELETE FROM runs WHERE run_id = {ph}", (rid,))
    return True


def delete_runs(run_ids):
    """Queue removal of deleted runs from the catalog."""
    if enabled():
        _bg.submit(_safe, delete_runs_now, list(run_ids))


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
    if _sqlite_path():
        cols, rows, err = _query_sqlite(sql, limit)
        if err:
            return {"ok": False, "error": f"SQL error: {err}"}
        truncated = len(rows) > limit
        return {"ok": True, "columns": cols, "rows": rows[:limit], "row_count": min(len(rows), limit),
                "truncated": truncated, "sql": sql}
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


def _query_sqlite(sql, limit):
    """Read-only connection (mode=ro, query_only) with a time limit through the progress handler."""
    uri = Path(_sqlite_path()).resolve().as_uri() + "?mode=ro"
    deadline = time.monotonic() + QUERY_TIMEOUT_MS / 1000
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5)) as c:
            c.execute("PRAGMA query_only = ON")
            c.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            cur = c.execute(sql)
            cols = [d[0] for d in cur.description or []]
            rows = [list(r) for r in cur.fetchmany(limit + 1)]
        return cols, rows, None
    except sqlite3.Error as e:
        msg = str(e)
        if "interrupted" in msg:
            msg = f"query took longer than {QUERY_TIMEOUT_MS} ms"
        return None, None, msg


def status():
    if not enabled():
        return {"enabled": False}
    return {"enabled": True, "dialect": dialect(), "reachable": _available()}
