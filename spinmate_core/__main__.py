"""Command line, no LLM:
  python -m spinmate_core engines
  python -m spinmate_core validate params.txt
  python -m spinmate_core run params.txt [--label text]     waits and prints the summary"""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="spinmate-core")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("engines", help="list the simulation engines")
    v = sub.add_parser("validate", help="check a parameter file with the engine's own parser")
    v.add_argument("file")
    r = sub.add_parser("run", help="run a parameter file and print the summary")
    r.add_argument("file")
    r.add_argument("--label", default="")
    r.add_argument("--timeout", type=float, default=3600)
    a = ap.parse_args(argv)

    from . import engines
    if a.cmd == "engines":
        for name in engines.available():
            e = engines.get(name)
            print(f"{name}\t{e.title}")
        return 0
    text = Path(a.file).read_text()
    if a.cmd == "validate":
        out = asdict(engines.get().validate(text))
        print(json.dumps(out, indent=1, default=str))
        return 0 if out["ok"] else 1

    from . import simulations as S
    started = S.start_run(text, a.label)
    if not started.get("ok"):
        print(json.dumps(started, indent=1))
        return 1
    run = S.wait_for_run(started["run_id"], a.timeout)
    print(json.dumps({k: run.get(k) for k in ("id", "status", "summary")}, indent=1, default=str))
    return 0 if run.get("status") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
