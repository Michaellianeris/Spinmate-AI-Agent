"""Sweeps and phase diagrams of the micromagnetic engines, and the animations (GIF, MP4)."""
import json

import numpy as np
import pytest
from PIL import Image

from spinmate_core import simulations as S, tools
from spinmate_core.mm2d import render

BASE = "--mode=relax\n--texture=skyrmion\n--Nx=48\n--Ny=48\n--R0=6e-9\n"


def _wait(sid):
    info = S.wait_for_sweep(sid, 120)
    while info["status_counts"].get("queued") or info["status_counts"].get("running"):
        info = S.wait_for_sweep(sid, 120)
    return info


def test_override_sets_numbers_and_vector_components():
    t = S._micro_override("mm2d", BASE + "--B_ext=0.1,0,0.2\n", "B_ext.z", 0.5)
    from spinmate_core.mm2d import parse
    assert parse(t)["B_ext"] == (0.1, 0.0, 0.5)
    assert parse(S._micro_override("mm2d", BASE, "Nx", 64))["Nx"] == 64
    assert "B_ext.z" in S.micro_sweepable("mm2d") and "D" in S.micro_sweepable("mm3d")


def test_bad_sweeps_are_refused_before_any_run():
    out = S.start_sweep(BASE, "texture", [1], engine="mm2d")
    assert not out["ok"] and "cannot be swept" in out["error"]
    out = S.start_sweep(BASE, "Nx", [48, 1], engine="mm2d")         # Nx=1 is invalid: nothing starts
    assert not out["ok"] and "Nx=1" in out["error"]
    assert not S.start_sweep("", "A0", [1], parameter2="B", values2=[1])["ok"]   # macrospin: one parameter


def test_skyrmion_radius_shrinks_with_field():
    out = json.loads(tools.call("start_sweep", {"parameters_text": BASE, "parameter": "B_ext.z",
                                                "values": [0.0, 0.2, 0.4], "engine": "mm2d"}, approved=True)[0])
    assert out["ok"], out
    info = _wait(out["sweep_id"])
    rows = sorted(info["rows"], key=lambda r: r["value"])
    assert [r["phase"] for r in rows] == ["skyrmion"] * 3
    radii = [r["radius_nm"] for r in rows]
    assert radii[0] > radii[1] > radii[2]
    assert info["plot"] == "sweep_plot.png" and S.sweep_file(out["sweep_id"], "sweep_plot.png")


def test_phase_diagram_finds_uniform_and_skyrmion():
    out = S.start_sweep(BASE, "D", [2e-3, 3.5e-3], engine="mm2d", parameter2="B_ext.z", values2=[0.0, 0.3])
    assert out["ok"] and out["runs"] == 4
    info = _wait(out["sweep_id"])
    phase = {(r["value"], r["value2"]): r["phase"] for r in info["rows"]}
    assert phase[(2e-3, 0.3)] == "uniform" and phase[(3.5e-3, 0.0)] == "skyrmion"
    assert S.sweep_file(out["sweep_id"], "sweep_plot.png")
    assert S._critical_D(info) == pytest.approx(3.78e-3, rel=0.01)      # 4 sqrt(A Keff) / pi, the dashed line


@pytest.mark.parametrize("texture,phase", [("domains", "stripes"), ("bubble", "skyrmion"), ("Q=-3", "skyrmions"),
                                           ("hopfion2", "hopfion2")])
def test_phase_names(texture, phase):
    assert S.phase_of({"texture": texture}) == phase


def test_animation_writes_a_gif(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    x = np.linspace(-10, 10, 20)
    frames = np.zeros((5, 3, 20, 20)); frames[:, 2] = 1
    files = render.save_animation(frames, np.arange(5) * 1e-12, x, x, "texture", "test",
                                  track=np.array([[0, 0, 0], [4e-12, 1e-9, 1e-9]]))
    assert "animation.gif" in files and Image.open(tmp_path / "animation.gif").n_frames == 5
