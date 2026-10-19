"""SpinMate desktop app: starts the local server and opens the browser.

Built into SpinMate.exe with PyInstaller (desktop/spinmate.spec). Modes:
  SpinMate.exe              start the app (data in %LOCALAPPDATA%\\SpinMate)
  SpinMate.exe --runner     run one simulation in the current folder (started by the app itself)
  SpinMate.exe --selftest   short run + catalog query, exit code 0 when everything works
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_PORT = 8421


def data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "SpinMate"


def load_env(path):
    """KEY=VALUE lines of a .env file; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def configure(base):
    base.mkdir(parents=True, exist_ok=True)
    env = base / ".env"
    if not env.exists():
        env.write_text("# SpinMate settings. Restart the app after a change.\n"
                       "ANTHROPIC_API_KEY=\nOPENAI_API_KEY=\n", encoding="utf-8")
    load_env(env)
    os.environ.setdefault("WORKSPACE", str(base / "data"))
    os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(base / "catalog.db"))
    os.environ.setdefault("MPLBACKEND", "Agg")


def running_at(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def free_port(start):
    for port in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port")


def serve():
    base = data_dir()
    configure(base)
    if running_at(APP_PORT):            # already open: just show it
        webbrowser.open(f"http://127.0.0.1:{APP_PORT}/")
        return
    port = free_port(APP_PORT)
    url = f"http://127.0.0.1:{port}/"
    print(f"SpinMate is running at {url}", flush=True)
    print(f"Data and settings: {base}")
    print("Close this window to stop SpinMate.", flush=True)

    def open_when_ready():
        for _ in range(100):
            if running_at(port):
                webbrowser.open(url)
                return
            time.sleep(0.2)
    threading.Thread(target=open_when_ready, daemon=True).start()

    import uvicorn
    from app.server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def selftest():
    """End-to-end check of the bundle: the server module, one real run through --runner and a query."""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="spinmate-selftest-"))
    os.environ["WORKSPACE"] = str(tmp / "data")
    os.environ["DATABASE_URL"] = "sqlite:///" + str(tmp / "catalog.db")
    os.environ["MPLBACKEND"] = "Agg"
    from app import catalog, server  # noqa: F401  (import check of the web app)
    from app import simulations as S
    r = S.start_run("--A0=0.248e-12\n--t=2e-10\n", label="selftest")
    assert r["ok"], r
    run = S.wait_for_run(r["run_id"], 120)
    if run["status"] != "done":
        err = (Path(os.environ["WORKSPACE"]) / "runs" / r["run_id"] / "stderr.log")
        print(err.read_text() if err.exists() else "no stderr.log")
        raise SystemExit(f"selftest run ended {run['status']}")
    assert run["summary"]["order_final"] == "FM", run["summary"]
    assert (Path(os.environ["WORKSPACE"]) / "runs" / r["run_id"] / "Two_Spin_Dynamics.png").exists()
    q = catalog.query("SELECT run_id, order_final FROM runs")
    assert q["ok"] and q["rows"] == [[r["run_id"], "FM"]], q
    print(json.dumps({"selftest": "ok", "run": r["run_id"], "order": run["summary"]["order_initial"] + "->FM"}))


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--runner":
        from spinmate_core import runner
        runner.main()
    elif arg == "--selftest":
        selftest()
    else:
        serve()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
