"""SpinMate's own 2D micromagnetic engine: physics checks against analytic results and a run through the tools."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "spinmate-mm2d"
sys.path.insert(0, str(PLUGIN))

from spinmate_core import engines, simulations as S, tools  # noqa: E402
import spinmate_mm2d as M  # noqa: E402
from spinmate_mm2d import solver as F  # noqa: E402

A, KU, MS, TH = 15e-12, 0.8e6, 0.58e6, 1e-9
KEFF = KU - 0.5 * F.MU0 * MS ** 2


def wall(D, dmi="interfacial"):
    f = F.Film(256, 2, 0.5e-9, MS, A, KU, D, dmi=dmi)
    m, _, _ = F.minimise(f, F.initial(f, "domain_wall"), tol=1.0)
    sigma = (f.energy(m, TH) - f.energy(F.initial(f, "uniform"), TH)) / (2 * f.Ny * f.dx * TH)
    i = int(np.argmin(np.abs(f.x + 256 * 0.5e-9 / 4)))
    return f, m, sigma, 1 / abs(np.gradient(m[2, 0], f.dx)[i]), i


@pytest.mark.parametrize("D,dmi", [(0.0, "interfacial"), (2e-3, "interfacial"), (2e-3, "bulk")])
def test_wall_energy_and_width(D, dmi):
    f, m, sigma, delta, i = wall(D, dmi)
    assert sigma == pytest.approx(4 * np.sqrt(A * KEFF) - np.pi * D, rel=3e-3)
    if D == 0:
        assert delta == pytest.approx(np.sqrt(A / KEFF), rel=1e-2)
    else:                                   # DMI picks the wall type: Neel (along x) or Bloch (along y)
        k = 0 if dmi == "interfacial" else 1
        assert abs(m[k, 0, i]) > 0.95
    assert np.allclose(np.linalg.norm(m, axis=0), 1, atol=1e-12)


def test_kittel_frequency():
    B = 0.5
    f = F.Film(4, 4, 1e-9, MS, A, KU, 0, B=(0, 0, B))
    m = F.initial(f, "uniform", m0=(np.sin(0.05), 0, np.cos(0.05)))
    t, dt, ts, mx = 0.0, 1e-14, [], []
    while t < 200e-12:
        m, used, dt = F.rk45_step(f, m, dt, 0.0)
        t += used; ts.append(t); mx.append(m[0].mean())
    mx, ts = np.array(mx), np.array(ts)
    z = np.where((mx[:-1] < 0) & (mx[1:] >= 0))[0]
    fz = ts[z] - mx[z] * (ts[z + 1] - ts[z]) / (mx[z + 1] - mx[z])      # interpolated zero crossings
    f_sim = (len(fz) - 1) / (fz[-1] - fz[0])
    f_th = F.GAMMA0 / (2 * np.pi) * (B / F.MU0 + 2 * KEFF / (F.MU0 * MS))
    assert f_sim == pytest.approx(f_th, rel=2e-3)


def test_energy_is_conserved_without_damping():
    f = F.Film(32, 32, 1e-9, MS, A, KU, 3.5e-3)
    m = F.initial(f, "skyrmion", R0=5e-9)          # not relaxed: it breathes
    E0, t, dt = f.energy(m, TH), 0.0, 1e-15
    while t < 5e-12:
        m, used, dt = F.rk45_step(f, m, dt, 0.0)
        t += used
    assert abs(f.energy(m, TH) - E0) / abs(E0) < 1e-4
    assert np.allclose(np.linalg.norm(m, axis=0), 1, atol=1e-12)


@pytest.mark.parametrize("kind,N,R0,Q,rings", [("skyrmion", 64, 7e-9, -1, 1), ("skyrmionium", 80, 10e-9, 0, 2)])
def test_skyrmion_and_skyrmionium(kind, N, R0, Q, rings):
    f = F.Film(N, N, 1e-9, MS, A, KU, 3.5e-3)
    m, _, torque = F.minimise(f, F.initial(f, kind, R0=R0), tol=10)
    assert torque < 10 and F.topological_charge(m) == pytest.approx(Q, abs=0.02)
    met = F.texture_metrics(f, m)
    assert len(met["rings_nm"]) == rings and not met["crosses_film"]
    assert f.energy(m, TH) < f.energy(F.initial(f, kind, R0=R0), TH)


def test_parse_and_validate():
    vals = M.parse("--Nx=64\n--Dbulk=2e-3\n--B_ext=0,0,0.1\n--mode=dynamics\n")
    assert vals["Nx"] == 64 and vals["dmi"] == "bulk" and vals["D"] == 2e-3 and vals["B_ext"] == (0, 0, 0.1)
    for bad in ("--Nonsense=1\n", "--mode=fast\n", "--Nx=1.5\n", "Nx 64\n"):
        with pytest.raises(ValueError):
            M.parse(bad)
    e = M.MM2DEngine()
    assert isinstance(e, engines.Engine) and e.ready()[0]
    assert e.parameter_schema()["texture"]["choices"][0] == "skyrmion"
    v = e.validate("--dx=4e-9\n--D=6e-3\n")
    assert v.ok and any("coarse" in w for w in v.warnings) and any("critical" in w for w in v.warnings)
    assert not e.validate("--Nx=2\n").ok


@pytest.fixture
def mm2d(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(PLUGIN))
    engines.register("mm2d", M.MM2DEngine)
    yield engines.get("mm2d")


def test_run_through_the_tools(mm2d):
    out = json.loads(tools.call("start_simulation", {"parameters_text": "--Nx=48\n--Ny=48\n--R0=6e-9\n",
                                                    "engine": "mm2d", "label": "mm2d test"})[0])
    assert out["ok"], out
    run = S.wait_for_run(out["run_id"], 120)
    assert run["status"] == "done", run.get("error_tail")
    s = run["summary"]
    assert s["engine"] == "mm2d" and s["texture"] == "skyrmion" and s["topological_charge"] == pytest.approx(-1, abs=0.02)
    for name in ("texture_3d.png", "texture_2d.png", "m_final.npz", "energy.dat"):
        assert name in run["files"] and S.run_file(out["run_id"], name)


def test_dynamics_run_finds_the_kittel_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "parameters.txt").write_text("--mode=dynamics\n--texture=uniform\n--Nx=8\n--Ny=8\n--D=0\n"
                                             "--B_ext=0,0,0.2\n--B_pulse=0.01,0,0\n--t_pulse=2e-12\n--t=500e-12\n"
                                             "--alpha=0.001\n")
    from spinmate_mm2d import runner
    runner.main()
    s = json.loads((tmp_path / "summary.json").read_text())
    f_th = F.GAMMA0 / (2 * np.pi) * (0.2 / F.MU0 + 2 * KEFF / (F.MU0 * MS)) / 1e9
    assert s["peak_frequency_mx_GHz"] == pytest.approx(f_th, abs=2.5) and s["texture"] == "uniform"
    assert (tmp_path / "Two_Spin_Dynamics.png").exists()


def test_page_shows_the_texture():
    html = (ROOT / "web" / "index.html").read_text()
    for el in ('class="rc-texture"', 's.engine === "mm2d"', "function mm2dView", "texture_3d_2d.png", "e.example"):
        assert el in html
    assert "mm2d" in (ROOT / "spinmate_mcp" / "physics.md").read_text()
