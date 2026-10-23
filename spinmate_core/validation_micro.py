"""Physics validation of the micromagnetic engines (mm2d, mm3d): each case runs SpinMate's own solver and compares
with an analytic result, as the macrospin cases do. Run with python -m spinmate_core.validation."""
import json
import tempfile
from pathlib import Path

import numpy as np

A, KU, MS = 15e-12, 0.8e6, 0.58e6


def _R(*a, **k):
    from .validation import Result
    return Result(*a, **k)


def _keff():
    from .mm2d.solver import MU0
    return KU - 0.5 * MU0 * MS ** 2


def _ok(x):
    return "pass" if x else "fail"


# ---------------------------------------------------------------- mm2d
def wall():
    from .mm2d import solver as F
    D, keff = 2e-3, _keff()
    f = F.Film(256, 2, 0.5e-9, MS, A, KU, D)
    m, _, _ = F.minimise(f, F.initial(f, "domain_wall"), tol=1.0)
    sigma = (f.energy(m, 1e-9) - f.energy(F.initial(f, "uniform"), 1e-9)) / (2 * f.Ny * f.dx * 1e-9)
    th = 4 * np.sqrt(A * keff) - np.pi * D
    i = int(np.argmin(np.abs(f.x + 256 * 0.5e-9 / 4)))
    neel = abs(m[0, 0, i]) > 0.95
    rel = abs(sigma - th) / th
    return _R("mm2d_wall", "Domain wall energy with interfacial DMI: 4√(A K_eff) − π|D|, a Néel wall",
              _ok(rel < 3e-3 and neel), {"σ (mJ/m²)": sigma * 1e3, "Néel": str(neel)},
              {"σ (mJ/m²)": th * 1e3, "Néel": "True"}, "0.3 %", note="A = 15 pJ/m, D = 2 mJ/m², 0.5 nm cells",
              engine="mm2d")


def kittel():
    from .mm2d import solver as F
    B, keff = 0.5, _keff()
    f = F.Film(4, 4, 1e-9, MS, A, KU, 0, B=(0, 0, B))
    m = F.initial(f, "uniform", m0=(np.sin(0.05), 0, np.cos(0.05)))
    t, dt, ts, mx = 0.0, 1e-14, [], []
    while t < 200e-12:
        m, used, dt = F.rk45_step(f, m, dt, 0.0)
        t += used; ts.append(t); mx.append(m[0].mean())
    mx, ts = np.array(mx), np.array(ts)
    z = np.where((mx[:-1] < 0) & (mx[1:] >= 0))[0]
    fz = ts[z] - mx[z] * (ts[z + 1] - ts[z]) / (mx[z + 1] - mx[z])
    f_sim = (len(fz) - 1) / (fz[-1] - fz[0])
    f_th = F.GAMMA0 / (2 * np.pi) * (B / F.MU0 + 2 * keff / (F.MU0 * MS))
    return _R("mm2d_kittel", "Uniform precession of a perpendicular film (Kittel): f = γ₀/2π (H + 2K_eff/μ₀M_s)",
              _ok(abs(f_sim - f_th) / f_th < 2e-3), {"f (GHz)": f_sim / 1e9}, {"f (GHz)": f_th / 1e9}, "0.2 %",
              note="B = 0.5 T, no damping", engine="mm2d")


def dispersion():
    import os
    from .mm2d import runner
    text = ("--mode=dynamics\n--texture=uniform\n--D=0\n--dx=2e-9\n--Ny=2\n--excite=stripe\n--excite_width=2e-9\n"
            "--Nx=256\n--pulse=sinc\n--f_rf=150e9\n--B_pulse=0.05,0,0\n--t_pulse=40e-12\n--t=300e-12\n"
            "--table_dt=2e-12\n--alpha=0.002\n--absorb=60e-9\n--animation=0\n")
    here = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            Path("parameters.txt").write_text(text)
            runner.main()
            s = json.loads(Path("summary.json").read_text())
        finally:
            os.chdir(here)
    mis = s.get("dispersion_mismatch_pct", np.inf)
    return _R("mm2d_dispersion", "Spin-wave dispersion f(k) from a broadband (sinc) antenna pulse",
              _ok(mis < 3), {"median mismatch (%)": mis},
              {"f(k)": "γ₀/2π (H_K,eff + 2A k²/μ₀M_s)"}, "3 %", note="256 cells of 2 nm, sinc up to 150 GHz",
              engine="mm2d")


def stt():
    from .mm2d import solver as F
    f = F.Film(64, 64, 1e-9, MS, A, KU, 3.5e-3)
    m, _, _ = F.minimise(f, F.initial(f, "skyrmion", R0=8e-9), tol=5)
    f.stt_u, f.stt_beta = np.array([50.0, 0, 0]), 0.1
    c0, t, dt, T = np.array(F.centre(f, m)), 0.0, 1e-14, 60e-12
    while t < T:
        m, used, dt = F.rk45_step(f, m, min(dt, T - t), 0.1, tol=1e-6)
        t += used
    L = f.Nx * f.dx
    v = ((np.array(F.centre(f, m)) - c0 + L / 2) % L - L / 2) / T
    return _R("mm2d_thiele", "Skyrmion under spin-transfer torque with β = α moves with the electrons (Thiele: v = u)",
              _ok(abs(v[0] - 50) / 50 < 0.02 and abs(v[1]) < 0.5), {"v (m/s)": f"({v[0]:.2f}, {v[1]:.2f})"},
              {"v (m/s)": "(50, 0)"}, "2 %", engine="mm2d")


def edge():
    from .mm2d import solver as F
    dx, N, D, keff = 0.25e-9, 240, 3.5e-3, _keff()
    f = F.Film(4, N, dx, MS, A, KU, D)
    mk = np.ones((N, 4), bool); mk[:24] = mk[-24:] = False
    f.set_mask(mk)
    m = F.initial(f, "uniform"); m = F._norm(m + 1e-3 * np.random.default_rng(0).normal(size=m.shape) * mk)
    m, _, _ = F.minimise(f, m, tol=0.05, max_iter=200000)
    top = np.where(mk[:, 2])[0][-1]
    th, delta = np.arccos(m[2, top, 2]), np.sqrt(A / keff)
    s0 = np.arccosh(1 / np.sin(th)) * delta - dx / 2
    got, want = 1 / np.cosh(s0 / delta), D * delta / (2 * A)
    return _R("mm2d_edge", "Tilt of m at a free edge with DMI (boundary condition 2A ∂θ/∂n = D)",
              _ok(abs(got - want) / want < 0.01), {"sin θ_edge": got}, {"sin θ_edge": want}, "1 %", engine="mm2d")


def demag():
    from .mm2d import demag as DM, solver as F
    dem = DM.Demag(24, 24, 2e-9, 3e-9, periodic=False)
    M = np.zeros((3, 24, 24)); M[2] = 1.0
    nz, nz_th = -dem.field(M)[2].mean(), DM.prism_Nz(48e-9, 48e-9, 3e-9)

    def sig(Dm, dm):
        f = F.Film(400, 4, 0.5e-9, MS, A, 1.0e6, Dm, demag=dm, thickness=0.5e-9)
        m, _, _ = F.minimise(f, F.initial(f, "domain_wall"), tol=1.0, max_iter=200000)
        return (f.energy(m, 0.5e-9) - f.energy(F.initial(f, "uniform"), 0.5e-9)) / (2 * f.Ny * f.dx * 0.5e-9)
    extra = (sig(2.5e-3, "full") - sig(0.0, "full")) - (sig(2.5e-3, "local") - sig(0.0, "local"))
    th = F.MU0 * MS ** 2 * 0.5e-9 * np.log(2) / np.pi
    return _R("mm2d_demag", "Long-range demag by FFT: platelet factor (Aharoni) and the stray-field energy of a Néel wall",
              _ok(abs(nz - nz_th) / nz_th < 1e-6 and abs(extra - th) / th < 0.02),
              {"N_z": nz, "Néel − Bloch (mJ/m²)": extra * 1e3}, {"N_z": nz_th, "Néel − Bloch (mJ/m²)": th * 1e3},
              "1e-6, 2 %", note="48 × 48 × 3 nm platelet; 0.5 nm film, μ₀M_s²d·ln2/π", engine="mm2d")


def backend():
    from .fastmag import cpu_available, stepper as ST
    from .mm2d import geometry, solver as F
    ok, why = cpu_available()
    if not ok:
        return _R("mm2d_backend", "The numba CPU backend gives the fields of the NumPy code", "fail", {}, {}, "",
                  note=why, engine="mm2d")
    f = F.Film(24, 20, 1e-9, MS, A, KU, 3.5e-3, boundary="free", B=(0.01, 0, 0.1))
    f.set_mask(geometry.built_in("racetrack", 24, 20))
    f.sot_h, f.sot_sigma = 2e3, np.array([0.0, 1.0, 0.0])
    m = F._norm(np.random.default_rng(0).normal(size=(3, 20, 24)))
    st = ST.for_mm2d(f, "cpu", 0.05)
    out = np.zeros(st.shape5)
    st.rhs(st.put(m), out)
    ref = F.llg_rhs(f, m, 0.05)
    err = float(np.abs(st.get(out) - ref).max() / np.abs(ref).max())
    return _R("mm2d_backend", "The fast CPU backend (numba) gives the dm/dt of the NumPy code",
              _ok(err < 1e-12), {"largest relative difference": err}, {"largest relative difference": 0.0}, "1e-12",
              note="racetrack with DMI edges and SOT", engine="mm2d")


# ---------------------------------------------------------------- mm3d
def helix():
    from .mm3d import solver as F
    p_cells, dx, A3 = 32, 1e-9, 10e-12
    D = 4 * np.pi * A3 / (p_cells * dx)
    s = F.Slab(4, 4, p_cells, dx, 500e3, A3, 0.0, D, "bulk", boundary="periodic", surfaces="periodic")
    q = 2 * np.pi / (p_cells * dx)
    ph = (q * s.z)[:, None, None] * np.ones((1, 4, 4))
    e = min(s.energy(np.stack([np.cos(k * ph), np.sin(k * ph), np.zeros_like(ph)])) for k in (1, -1)) / (16 * p_cells * dx ** 3)
    th = -D ** 2 / (4 * A3)
    return _R("mm3d_helix", "Helix of a chiral magnet: energy −D²/4A at q = D/2A", _ok(abs(e - th) / abs(th) < 0.02),
              {"e (kJ/m³)": e / 1e3}, {"e (kJ/m³)": th / 1e3}, "2 %", note="32 cells per period", engine="mm3d")


def hopfion():
    from .mm3d import solver as F
    A3, p = 10e-12, 16e-9
    s = F.Slab(38, 38, 16, 1e-9, 500e3, A3, 0.0, 4 * np.pi * A3 / p, "bulk", B=(0, 0, 0.45), boundary="free", disk=36e-9)
    m = np.where(s.mask, F.initial(s, "hopfion"), np.array([0, 0, 1.0])[:, None, None, None])
    m, it, tq = F.minimise(s, m, tol=5, max_iter=20000)
    H = F.hopf_index(m)
    return _R("mm3d_hopfion", "A hopfion in a chiral nanodisk keeps its Hopf index through the relaxation",
              _ok(tq < 5 and abs(H) > 0.7), {"Hopf index H": H}, {"Hopf index H": "±1 (≈ 0.86-0.9 on the grid)"},
              "|H| > 0.7", note="36 nm disk, 16 nm thick, p = 16 nm", engine="mm3d")


def bloch_point():
    from .mm3d import solver as F
    s = F.Slab(10, 10, 10, 1e-9, 500e3, 10e-12, boundary="free", surfaces="free")
    Z, Y, X = np.meshgrid(s.z, s.y, s.x, indexing="ij")
    r = np.sqrt(X ** 2 + Y ** 2 + Z ** 2)
    got = [F.bloch_points(s, np.stack([k * X, Y, Z]) / r)[0][3] for k in (1, -1)]
    return _R("mm3d_bloch", "Bloch points: the solid angle of m around a hedgehog is ±4π", _ok(got == [1, -1]),
              {"charges": str(got)}, {"charges": "[1, -1]"}, "exact", engine="mm3d")


CASES = {"mm2d_wall": wall, "mm2d_kittel": kittel, "mm2d_dispersion": dispersion, "mm2d_thiele": stt,
         "mm2d_edge": edge, "mm2d_demag": demag, "mm2d_backend": backend,
         "mm3d_helix": helix, "mm3d_hopfion": hopfion, "mm3d_bloch": bloch_point}
FAST = ("mm2d_kittel", "mm2d_backend", "mm3d_helix", "mm3d_bloch")
