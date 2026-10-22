"""Cross-check of mm2d with mumax3: the .mx3 translation, the OVF files and the comparison (with a stand-in mumax3
that relaxes the translated problem with SpinMate's own solver, so only the plumbing is tested here)."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "spinmate-mumax3"
sys.path.insert(0, str(PLUGIN))

from spinmate_core import compare as C, engines, ovf, simulations as S, tools  # noqa: E402
import spinmate_core.mm2d as M  # noqa: E402
import spinmate_mumax3  # noqa: E402

BASE = "--texture=skyrmion\n--Nx=48\n--Ny=48\n--R0=6e-9\n"


@pytest.fixture
def mumax(monkeypatch):
    monkeypatch.setenv("MUMAX3_BIN", str(ROOT / "tests" / "fake" / "mumax3_fake.py"))
    monkeypatch.setenv("PYTHONPATH", f"{PLUGIN}:{ROOT}")
    engines.register("mumax3", spinmate_mumax3.Mumax3Engine)
    yield


def test_mx3_translation():
    v = M.parse(BASE + "--B_ext=0,0,0.1\n")
    s = C.mx3(v, "/x/m0.ovf")
    keff = 0.8e6 - 0.5 * C.MU0 * 580e3 ** 2
    assert "SetGridSize(48, 48, 1)" in s and "SetPBC(1, 1, 0)" in s and "EnableDemag = false" in s
    assert f"Ku1 = {keff!r}" in s and "Dind = 0.0035" in s and 'm.LoadFile("/x/m0.ovf")' in s and "Minimize()" in s
    s = C.mx3(M.parse(BASE + "--demag=full\n--dmi=bulk\n--boundary=free\n"), "/x/m0.ovf", "/x/mask.png")
    assert "EnableDemag = true" in s and "Ku1 = 800000.0" in s and "Dbulk" in s and "SetPBC" not in s
    assert 'SetGeom(ImageShape("/x/mask.png"))' in s
    s = C.mx3(M.parse(BASE + "--mode=dynamics\n--B_pulse=0.01,0,0\n--t_pulse=10e-12\n--t=50e-12\n"), "/x/m0.ovf")
    assert "B_ext = vector(0.01, 0.0, 0.0)" in s and "Run(1e-11)" in s and "Run(4e-11)" in s


def test_ovf_round_trip(tmp_path):
    m = np.random.default_rng(1).normal(size=(3, 6, 9))
    ovf.write(tmp_path / "a.ovf", m, 1e-9, 1e-9, 2e-9)
    back, cell = ovf.read(tmp_path / "a.ovf")
    assert back.shape == (3, 1, 6, 9) and np.allclose(back[:, 0], m, atol=1e-8) and cell == (1e-9, 1e-9, 2e-9)


def test_inputs_that_cannot_be_compared():
    assert C.problems(M.parse(BASE + "--j=1e11,0,0\n--theta_SH=0.1\n"))
    assert C.problems(M.parse(BASE + "--lattice=saf\n"))
    assert not C.problems(M.parse(BASE))


def test_needs_mumax3(monkeypatch):
    monkeypatch.delenv("MUMAX3_BIN", raising=False)
    monkeypatch.setattr(spinmate_mumax3.shutil, "which", lambda name: None)
    engines.register("mumax3", spinmate_mumax3.Mumax3Engine)
    out = C.start(BASE)
    assert not out["ok"] and "mumax3" in out["error"]


@pytest.mark.parametrize("extra", ["", "--mask=disk\n--boundary=free\n"])
def test_cross_check_chain(mumax, extra):
    out = json.loads(tools.call("compare_with_mumax3", {"parameters_text": BASE + extra}, approved=True)[0])
    assert out["ok"], out
    res = C.result(out["compare_id"], 120)
    while res["status"]["mm2d"] in ("queued", "running") or res["status"]["mumax3"] in ("queued", "running"):
        res = C.result(out["compare_id"], 120)
    assert res["status"] == {"mm2d": "done", "mumax3": "done"}, res
    assert res["agree"], res["checks"]
    assert res["mm2d"]["topological_charge"] == pytest.approx(-1, abs=0.05)
    names = [c["quantity"] for c in res["checks"]]
    assert "topological charge Q" in names and "energy (J)" in names and "radius (nm)" in names
    assert C.plot_file(out["compare_id"])
    again = json.loads(tools.call("get_comparison", {"compare_id": out["compare_id"]})[0])
    assert again["agree"] and again["checks"] == res["checks"]
