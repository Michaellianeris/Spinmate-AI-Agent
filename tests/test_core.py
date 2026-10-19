"""spinmate_core: the package without LLM, the engine interface and the command line."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_old_names_point_to_the_core():
    import app.simulations, app.tools, app.params
    from spinmate_core import params, simulations, tools
    assert app.simulations is simulations and app.tools is tools and app.params is params


def test_core_loads_no_llm_library():
    code = ("import sys, spinmate_core.tools, spinmate_core.reports, spinmate_core.engines; "
            "print([m for m in ('anthropic', 'openai', 'mcp', 'fastapi') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_engine_interface():
    from spinmate_core import engines
    e = engines.get()
    assert isinstance(e, engines.Engine) and "macrospin" in engines.available()
    v = e.validate("--A0=0.248e-12\n--t=1e-10\n")
    assert v.ok and v.steps == 1000 and "A0" in v.changed_from_defaults
    assert not e.validate("--nonsense=1\n").ok
    schema = e.parameter_schema()
    assert schema["A0"]["kind"] == "float" and schema["m1"]["kind"] == "vector"
    with pytest.raises(KeyError):
        engines.get("no-such-engine")


def test_runs_record_their_engine():
    from spinmate_core import simulations as S
    r = S.start_run("--t=1e-11\n", label="engine field")
    assert r["ok"]
    assert S.wait_for_run(r["run_id"], 60)["status"] == "done"
    assert json.loads((S.RUNS / r["run_id"] / "meta.json").read_text())["engine"] == "macrospin"


def test_command_line(tmp_path):
    p = tmp_path / "p.txt"
    p.write_text("--t=1e-11\n")
    ok = subprocess.run([sys.executable, "-m", "spinmate_core", "validate", str(p)], cwd=ROOT,
                        capture_output=True, text=True)
    assert ok.returncode == 0 and json.loads(ok.stdout)["ok"]
    p.write_text("--nonsense=1\n")
    bad = subprocess.run([sys.executable, "-m", "spinmate_core", "validate", str(p)], cwd=ROOT,
                         capture_output=True, text=True)
    assert bad.returncode == 1
