"""The fast backends of the micromagnetic engines: numba on the CPU and the CUDA kernels (in numba's CUDA simulator)
give the same fields, steps, energies and runs as the reference NumPy code."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from spinmate_core import fastmag
from spinmate_core.fastmag import stepper as ST
from spinmate_core.mm2d import geometry, solver as S
from spinmate_core.mm3d import solver as S3

ROOT = Path(__file__).resolve().parent.parent


def films():
    out = {}
    f = S.Film(24, 20, 1e-9, 5.8e5, 15e-12, 0.8e6, 3.5e-3, B=(0.01, 0.02, 0.1))
    out["interfacial, periodic"] = f
    out["bulk, free"] = S.Film(24, 20, 1e-9, 5.8e5, 15e-12, 0.8e6, 3.5e-3, dmi="bulk", boundary="free")
    f = S.Film(24, 20, 1e-9, 5.8e5, 15e-12, 0.8e6, 3.5e-3, boundary="free")
    f.set_mask(geometry.built_in("racetrack", 24, 20))
    f.stt_u, f.stt_beta = np.array([30.0, 10.0, 0.0]), 0.2
    f.sot_h, f.sot_sigma = 2e3, np.array([0.0, 1.0, 0.0])
    out["racetrack, STT and SOT"] = f
    out["full demag"] = S.Film(16, 16, 1e-9, 5.8e5, 15e-12, 0.8e6, 3.5e-3, demag="full", thickness=3e-9)
    out["synthetic AFM"] = S.AFMFilm(16, 16, 1e-9, 5.8e5, 15e-12, 0.8e6, 3.5e-3, J_afm=2e7, demag="local")
    return out


def state(f, seed=0):
    rng = np.random.default_rng(seed)
    shape = (3, 2, f.Ny, f.Nx) if hasattr(f, "J_afm") else (3, f.Ny, f.Nx)
    m = S._norm(rng.normal(size=shape))
    if f.mask is not None:
        m = np.where(f.mask, m, np.array([0, 0, 1.0]).reshape((3,) + (1,) * (m.ndim - 1)))
    return m


@pytest.mark.parametrize("name", list(films()))
def test_cpu_matches_numpy(name):
    f = films()[name]
    m = state(f)
    alpha = np.full((f.Ny, f.Nx), 0.05)
    a, b = ST.for_mm2d(f, "numpy", alpha), ST.for_mm2d(f, "cpu", alpha)
    out = np.zeros(b.shape5)
    b.rhs(b.put(m), out)
    ref = S.llg_rhs(f, m, alpha)
    assert np.abs(b.get(out) - ref).max() < 1e-12 * np.abs(ref).max()
    assert b.energy(b.put(m)) == pytest.approx(a.energy(m), rel=1e-12)
    ma, mb, da, db = m.copy(), b.put(m), 1e-15, 1e-15
    for _ in range(20):
        ma, _, da = a.rk45(ma, da, 1e-6)
        mb, _, db = b.rk45(mb, db, 1e-6)
    assert np.abs(ma - b.get(mb)).max() < 1e-10 and db == pytest.approx(da, rel=1e-9)


def test_cpu_matches_numpy_in_3d():
    for disk, surfaces, dmi in ((9e-9, "pinned", "bulk"), (0, "free", "interfacial"), (0, "periodic", "bulk")):
        sl = S3.Slab(10, 10, 6, 1e-9, 5e5, 1e-11, 1e4, 5.236e-3, dmi=dmi, B=(0, 0.1, 0.3),
                     boundary="free" if disk else "periodic", surfaces=surfaces, disk=disk)
        m = S._norm(np.random.default_rng(3).normal(size=(3, 6, 10, 10)))
        b = ST.for_mm3d(sl, "cpu")
        out = np.zeros(b.shape5)
        b.rhs(b.put(m), out)
        ref = S.llg_rhs(sl, m, 0.0)
        assert np.abs(b.get(out) - ref).max() < 1e-12 * np.abs(ref).max()
        assert b.energy(b.put(m)) == pytest.approx(sl.energy(m), rel=1e-12)


def test_minimiser_reaches_the_same_texture():
    f = S.Film(48, 48, 1e-9, 5.8e5, 15e-12, 0.8e6, 3.5e-3)
    m0 = S.initial(f, "skyrmion", R0=8e-9)
    a, b = ST.for_mm2d(f, "numpy"), ST.for_mm2d(f, "cpu")
    ma, _, ta = a.minimise(m0.copy(), 1.0, 20000)
    mb, _, tb = b.minimise(b.put(m0), 1.0, 20000)
    assert ta < 1 and tb < 1
    assert S.texture_metrics(f, b.get(mb))["radius_nm"] == pytest.approx(S.texture_metrics(f, ma)["radius_nm"], rel=1e-3)


def test_backend_choice():
    assert fastmag.choose("numpy") == ("numpy", "")
    b, note = fastmag.choose("gpu")
    if not fastmag.gpu_available()[0]:
        assert b == "cpu" and "GPU" in note
    assert fastmag.choose("gpu", needs_fft=True)[0] in ("cpu", "numpy")


def test_gpu_kernels_in_the_cuda_simulator():
    """The CUDA kernels and the GPU stepper (NumPy standing in for CuPy) against the CPU backend."""
    code = r'''
import numpy as np
from spinmate_core.mm2d import solver as S, geometry
from spinmate_core.mm3d import solver as S3
from spinmate_core.fastmag import stepper as ST
f = S.Film(10, 8, 1e-9, 5.8e5, 15e-12, 0.8e6, 3.5e-3, boundary="free", B=(0.01, 0.02, 0.1))
f.set_mask(geometry.built_in("racetrack", 10, 8))
f.stt_u, f.stt_beta, f.sot_h, f.sot_sigma = np.array([30.0, 10.0, 0]), 0.2, 2e3, np.array([0, 1.0, 0])
m = S._norm(np.random.default_rng(0).normal(size=(3, 8, 10)))
m = np.where(f.mask, m, np.array([0, 0, 1.0])[:, None, None])
c, g = ST.for_mm2d(f, "cpu", 0.05), ST.for_mm2d(f, "gpu", 0.05)
mc, mg, dc, dg = c.put(m), g.put(m), 1e-15, 1e-15
for _ in range(3):
    mc, _, dc = c.rk45(mc, dc, 1e-6)
    mg, _, dg = g.rk45(mg, dg, 1e-6)
out = {"rk45": float(np.abs(c.get(mc) - g.get(mg)).max()), "energy": [c.energy(mc), g.energy(mg)]}
xc, _, tc = c.minimise(c.put(m), 1e3, 40)
xg, _, tg = g.minimise(g.put(m), 1e3, 40)
out["minimise"] = float(np.abs(c.get(xc) - g.get(xg)).max())
sl = S3.Slab(6, 6, 4, 1e-9, 5e5, 1e-11, 0, 5.236e-3, B=(0, 0, 0.3), boundary="free", surfaces="pinned", disk=5e-9)
m3 = S._norm(np.random.default_rng(1).normal(size=(3, 4, 6, 6)))
c3, g3 = ST.for_mm3d(sl, "cpu"), ST.for_mm3d(sl, "gpu")
a, b = np.zeros(c3.shape5), np.zeros(g3.shape5)
c3.rhs(c3.put(m3), a); g3.rhs(g3.put(m3), b)
out["rhs3d"] = float(np.abs(a - b).max() / np.abs(a).max())
print("RESULT" + __import__("json").dumps(out))
'''
    env = dict(os.environ, NUMBA_ENABLE_CUDASIM="1", PYTHONPATH=str(ROOT))
    res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=600)
    assert res.returncode == 0, res.stderr[-2000:]
    out = json.loads(res.stdout.split("RESULT")[-1])
    assert out["rk45"] < 1e-10 and out["minimise"] < 1e-8 and out["rhs3d"] < 1e-12
    assert out["energy"][0] == pytest.approx(out["energy"][1], rel=1e-10)


def test_runs_agree_across_backends(tmp_path, monkeypatch):
    from spinmate_core.mm2d import runner
    res = {}
    for bk in ("numpy", "cpu"):
        d = tmp_path / bk; d.mkdir(); monkeypatch.chdir(d)
        (d / "parameters.txt").write_text("--mode=dynamics\n--texture=skyrmion\n--Nx=48\n--Ny=48\n--R0=6e-9\n"
                                          "--alpha=0.1\n--j=1e12,0,0\n--theta_SH=0.1\n--t=40e-12\n--table_dt=2e-12\n"
                                          f"--animation=0\n--backend={bk}\n")
        runner.main()
        res[bk] = json.loads((d / "summary.json").read_text())
    assert res["cpu"]["backend"] == "cpu"
    assert res["cpu"]["topological_charge"] == res["numpy"]["topological_charge"]
    assert np.allclose(res["cpu"]["velocity_mps"], res["numpy"]["velocity_mps"], rtol=0.02, atol=0.5)
    assert res["cpu"]["energy_final_J"] == pytest.approx(res["numpy"]["energy_final_J"], rel=1e-4)


def test_cpu_conserves_energy_without_damping():
    """alpha = 0: the adaptive Dormand-Prince steps keep the energy of a breathing skyrmion (the step is right)."""
    f = S.Film(32, 32, 1e-9, 5.8e5, 15e-12, 0.8e6, 3.5e-3)
    st = ST.for_mm2d(f, "cpu", 0.0)
    m = st.put(S.initial(f, "skyrmion", R0=5e-9))     # not relaxed: it breathes
    e0, t, dt = st.energy(m), 0.0, 1e-15
    while t < 5e-12:
        m, used, dt = st.rk45(m, dt, 1e-7)
        t += used
    assert abs(st.energy(m) - e0) < 1e-4 * abs(e0)
    assert np.allclose(np.linalg.norm(st.get(m), axis=0), 1, atol=1e-12)
