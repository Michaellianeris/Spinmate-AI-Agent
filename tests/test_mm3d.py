"""SpinMate's own 3D micromagnetic engine (built in): checks against analytic results, a relaxed hopfion and the
report with the texture pictures."""
import json
from pathlib import Path

import numpy as np
import pytest

from spinmate_core import engines, reports, simulations as S, tools
import spinmate_core.mm3d as M
from spinmate_core.mm2d import solver as F2
from spinmate_core.mm3d import solver as F

ROOT = Path(__file__).resolve().parent.parent
A, MS = 10e-12, 500e3


def test_helix_energy():
    """A helix along z with q = D/2A lowers the energy density by D²/4A (bulk DMI, periodic in 3D)."""
    p_cells, dx = 32, 1e-9
    D = 4 * np.pi * A / (p_cells * dx)
    s = F.Slab(4, 4, p_cells, dx, MS, A, 0.0, D, "bulk", boundary="periodic", surfaces="periodic")
    q = 2 * np.pi / (p_cells * dx)
    ph = (q * s.z)[:, None, None] * np.ones((1, 4, 4))
    best = []
    for sign in (1, -1):
        m = np.stack([np.cos(sign * ph), np.sin(sign * ph), np.zeros_like(ph)])
        best.append(s.energy(m))
    e = min(best) / (4 * 4 * p_cells * dx ** 3)
    lattice = 2 * A * (1 - np.cos(q * dx)) / dx ** 2 - D * np.sin(q * dx) / dx     # five-point lattice values
    assert e == pytest.approx(lattice, rel=1e-6)
    assert e == pytest.approx(-D ** 2 / (4 * A), rel=0.02)


def test_matches_mm2d_in_one_layer():
    s3 = F.Slab(16, 16, 1, 1e-9, MS, A, 0.5e6, 2e-3, "interfacial", surfaces="free")
    s2 = F2.Film(16, 16, 1e-9, MS, A, 0.5e6, 2e-3, "interfacial", demag="none")
    m2 = F2.initial(s2, "skyrmion", R0=4e-9)
    h3 = s3.h_internal(m2[:, None])[:, 0]
    assert np.allclose(h3, s2.h_internal(m2), rtol=1e-10, atol=1e-6)


def test_hopf_index_of_the_seed():
    s = F.Slab(48, 48, 48, 1e-9, MS, A, 0.0, 4 * np.pi * A / 32e-9, "bulk", surfaces="periodic")
    assert abs(F.hopf_index(F.initial(s, "hopfion"))) == pytest.approx(0.86, abs=0.08)
    assert abs(F.hopf_index(F.initial(s, "uniform"))) < 1e-9


def test_hopfion_survives_relaxation_in_a_nanodisk():
    p = 16e-9
    s = F.Slab(38, 38, 16, 1e-9, MS, A, 0.0, 4 * np.pi * A / p, "bulk", B=(0, 0, 0.45), boundary="free", disk=36e-9)
    m = np.where(s.mask, F.initial(s, "hopfion"), np.array([0, 0, 1.0])[:, None, None, None])
    E0 = s.energy(m)
    m, it, torque = F.minimise(s, m, tol=5, max_iter=20000)
    assert torque < 5 and s.energy(m) < E0 and abs(F.hopf_index(m)) > 0.7
    assert np.allclose(np.linalg.norm(m, axis=0), 1, atol=1e-12)
    met = F.metrics(s, m)
    assert met["min_mz"] < -0.9 and len(met["rings_nm"]) == 2


def test_parse_validate_and_engine():
    e = engines.get("mm3d")
    assert isinstance(e, engines.Engine) and "mm3d" in engines.available() and e.ready()[0]
    v = e.validate("--Nz=8\n")
    assert v.ok and any("helical period" in w for w in v.warnings)
    assert not e.validate("--disk_diameter=200e-9\n").ok
    assert not e.validate("--texture=knot\n").ok
    assert any("unwind" in w for w in e.validate("--geometry=slab\n").warnings)


def test_run_and_report(monkeypatch):
    params = ("--Nx=38\n--Ny=38\n--Nz=16\n--D=7.854e-3\n--disk_diameter=36e-9\n--B_ext=0,0,0.45\n")
    out = json.loads(tools.call("start_simulation", {"parameters_text": params, "engine": "mm3d", "label": "hopfion"})[0])
    assert out["ok"], out
    run = S.wait_for_run(out["run_id"], 300)
    assert run["status"] == "done", run.get("error_tail")
    s = run["summary"]
    assert s["engine"] == "mm3d" and s["texture"] == "hopfion" and abs(s["hopf_index"]) > 0.7
    assert "texture_3d_2d.png" in run["files"]
    sk = json.loads(tools.call("start_simulation", {"parameters_text": "--Nx=48\n--Ny=48\n--R0=6e-9\n", "engine": "mm2d",
                                                    "label": "skyrmion"})[0])
    assert S.wait_for_run(sk["run_id"], 120)["status"] == "done"
    rep = reports.build([out["run_id"], sk["run_id"]], title="Hopfion and skyrmion")
    page = (reports.REPORTS / rep["report"] / "report.html").read_text()
    # each caption is in the alt text and under the figure
    assert page.count("surface in 3D, coloured by the in-plane direction") == 2
    assert page.count("the final texture, tilted 3D view and top view") == 2
    assert page.count("data:image/png;base64") >= 3                          # logo and the two texture pictures
    assert "Hopf index" in page or "H = " in page
    assert "Micromagnetics of SpinMate" in page and "Two-sublattice macrospin" not in page


def test_page_has_hopfion():
    html = (ROOT / "web" / "index.html").read_text()
    assert '"Hopfion", "Relax a hopfion with mm3d' in html and '["mm2d", "mm3d"].includes(r.summary.engine)' in html
