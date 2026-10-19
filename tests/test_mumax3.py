"""The mumax3 plug-in through the engine interface, the run queue and the tools (with a stand-in binary)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "spinmate-mumax3"
sys.path.insert(0, str(PLUGIN))

from spinmate_core import engines, simulations as S, tools  # noqa: E402
import spinmate_mumax3 as M  # noqa: E402


@pytest.fixture
def mumax(monkeypatch):
    monkeypatch.setenv("MUMAX3_BIN", str(ROOT / "tests" / "fake" / "mumax3_fake.py"))
    monkeypatch.setenv("PYTHONPATH", str(PLUGIN))
    engines.register("mumax3", M.Mumax3Engine)
    yield engines.get("mumax3")


def test_parse_and_script():
    vals = M.parse("--Nx=32\n--Msat=8e5\n--B_ext=0,0,0.1\n")
    assert vals["Nx"] == 32 and vals["B_ext"] == (0.0, 0.0, 0.1)
    mx3 = M.to_mx3(vals)
    assert "SetGridSize(32, 64, 1)" in mx3 and "B_ext = vector(0.0, 0.0, 0.1)" in mx3 and "Run(1e-09)" in mx3
    with pytest.raises(ValueError):
        M.parse("--Nonsense=1\n")
    raw = "SetGridSize(8, 8, 1)\nSetCellSize(1e-9, 1e-9, 1e-9)\nMsat = 8e5\nRun(1e-10)\n"
    assert M.is_script(raw) and M.script(raw).startswith("TableAutoSave")


def test_engine_interface(mumax):
    assert isinstance(mumax, engines.Engine) and "mumax3" in engines.available()
    v = mumax.validate("--Nx=16\n--t=1e-10\n")
    assert v.ok and v.changed_from_defaults == {"Nx": 16, "t": 1e-10} and v.steps == 100
    assert not mumax.validate("--Nx=abc\n").ok


def test_run_through_the_tools(mumax):
    out = json.loads(tools.call("start_simulation", {"parameters_text": "--t=1e-10\n", "engine": "mumax3",
                                                    "label": "mumax3 test"})[0])
    assert out["ok"], out
    run = S.wait_for_run(out["run_id"], 60)
    assert run["status"] == "done", run.get("error_tail")
    s = run["summary"]
    assert s["engine"] == "mumax3" and s["points"] == 101 and len(s["m_final"]) == 3
    meta = json.loads((S.RUNS / out["run_id"] / "meta.json").read_text())
    assert meta["engine"] == "mumax3" and (S.RUNS / out["run_id"] / "input.mx3").exists()
    names = [e["name"] for e in json.loads(tools.call("list_engines", {})[0])["engines"]]
    assert {"macrospin", "mumax3"} <= set(names)
    cont = S.continue_run(out["run_id"])
    assert not cont["ok"] and "macrospin" in cont["error"]


def test_unknown_engine_is_an_error():
    out = S.start_run("--t=1e-11\n", engine="no-such-engine")
    assert not out["ok"] and "no engine" in out["error"]


def test_engines_endpoint_and_readiness(mumax, monkeypatch):
    from fastapi.testclient import TestClient
    from app.server import app
    http = TestClient(app)
    eng = {e["name"]: e for e in http.get("/api/engines").json()["engines"]}
    assert eng["macrospin"]["ready"] and eng["macrospin"]["default"] and eng["mumax3"]["ready"]
    monkeypatch.delenv("MUMAX3_BIN")
    monkeypatch.setattr(M.shutil, "which", lambda name: None)
    eng = {e["name"]: e for e in http.get("/api/engines").json()["engines"]}
    assert not eng["mumax3"]["ready"] and "MUMAX3_BIN" in eng["mumax3"]["note"]
    # the page validates and starts runs on the engine it chose
    r = http.post("/api/validate", json={"parameters_text": "--Nx=16\n--t=1e-10\n", "engine": "mumax3"}).json()
    assert r["ok"] and r["steps"] == 100 and any("MUMAX3_BIN" in w for w in r["warnings"])
