"""SpinMate's own 2D micromagnetic engine (built in): physics checks against analytic results and a run through the tools."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
from spinmate_core import engines, simulations as S, tools  # noqa: E402
import spinmate_core.mm2d as M  # noqa: E402
from spinmate_core.mm2d import solver as F  # noqa: E402

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
def mm2d():
    assert "mm2d" in engines.available()      # built in, no install
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
    from spinmate_core.mm2d import runner
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


def _run(tmp_path, monkeypatch, text):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "parameters.txt").write_text(text)
    from spinmate_core.mm2d import runner
    runner.main()
    return json.loads((tmp_path / "summary.json").read_text())


SW = "--mode=dynamics\n--texture=uniform\n--D=0\n--dx=2e-9\n--Ny=2\n--excite=stripe\n--excite_width=2e-9\n"


def test_spin_wave_wavelength(tmp_path, monkeypatch):
    s = _run(tmp_path, monkeypatch, SW + "--Nx=160\n--excite_x=-80e-9\n--pulse=sine\n--f_rf=80e9\n--B_pulse=0.02,0,0\n"
             "--t_pulse=200e-12\n--t=200e-12\n--table_dt=2e-12\n--alpha=0.01\n--absorb=40e-9\n")
    assert s["texture"] == "spin_waves" and s["wavelength_nm"] == pytest.approx(s["wavelength_theory_nm"], rel=0.03)
    for name in ("spinwave_3d_2d.png", "spinwave_map.png", "spinwave.npz"):
        assert (tmp_path / name).exists()


def test_spin_wave_dispersion(tmp_path, monkeypatch):
    s = _run(tmp_path, monkeypatch, SW + "--Nx=256\n--pulse=sinc\n--f_rf=150e9\n--B_pulse=0.05,0,0\n--t_pulse=40e-12\n"
             "--t=300e-12\n--table_dt=2e-12\n--alpha=0.002\n--absorb=60e-9\n")
    assert s["dispersion_mismatch_pct"] < 3 and (tmp_path / "dispersion.png").exists()


def test_spin_wave_checks():
    e = M.MM2DEngine()
    assert not e.validate("--mode=dynamics\n--pulse=sine\n").ok                       # needs f_rf
    v = e.validate("--mode=dynamics\n--excite=stripe\n--pulse=sine\n--f_rf=100e9\n--table_dt=1e-11\n")
    assert v.ok and any("too coarse" in w for w in v.warnings) and any("absorb" in w for w in v.warnings)


def _skyrmion(N=64):
    f = F.Film(N, N, 1e-9, MS, A, KU, 3.5e-3)
    m, _, _ = F.minimise(f, F.initial(f, "skyrmion", R0=8e-9), tol=5)
    return f, m


def _move(f, m, alpha, T):
    c0 = np.array(F.centre(f, m)); t, dt = 0.0, 1e-14
    while t < T:
        m, used, dt = F.rk45_step(f, m, min(dt, T - t), alpha, tol=1e-6)
        t += used
    L = f.Nx * f.dx
    return ((np.array(F.centre(f, m)) - c0 + L / 2) % L - L / 2) / T, m


def test_stt_with_beta_equal_alpha_moves_with_u():
    """Thiele: with beta = alpha a rigid texture drifts exactly with the electrons, v = u."""
    f, m = _skyrmion()
    f.stt_u, f.stt_beta = np.array([50.0, 0, 0]), 0.1
    v, m1 = _move(f, m, 0.1, 60e-12)
    assert v[0] == pytest.approx(50, rel=0.02) and abs(v[1]) < 0.5
    assert F.topological_charge(m1) == pytest.approx(-1, abs=0.02)


@pytest.mark.parametrize("torque", ["stt", "sot"])
def test_motion_matches_thiele(torque):
    f, m = _skyrmion()
    if torque == "stt":
        f.stt_u, f.stt_beta = np.array([50.0, 0, 0]), 0.2
    else:
        f.sot_h, f.sot_sigma = 1e4, (0, 1, 0)
    th = F.thiele_velocity(f, m, 0.1)
    v, _ = _move(f, m, 0.1, 60e-12)
    assert np.hypot(*v) == pytest.approx(np.hypot(*th), rel=0.04)       # speed
    assert np.hypot(*(v - th)) < 0.12 * np.hypot(*th)                    # direction (the texture deforms a little)
    if torque == "stt":
        assert th[1] > 5                                          # a skyrmion Hall effect


def test_current_run_reports_velocity(tmp_path, monkeypatch):
    s = _run(tmp_path, monkeypatch, "--mode=dynamics\n--texture=skyrmion\n--Nx=48\n--Ny=48\n--R0=6e-9\n--alpha=0.1\n"
             "--j=1e12,0,0\n--theta_SH=0.1\n--t=60e-12\n--table_dt=2e-12\n")
    assert s["sot_sigma"] == [0.0, 1.0, 0.0] and s["sot_field_A_per_m"] > 0
    assert np.hypot(*(np.array(s["velocity_mps"]) - s["thiele_velocity_mps"])) < 0.15 * s["speed_mps"]
    assert -180 <= s["hall_angle_deg"] <= 180 and (tmp_path / "skyrmion_track.png").exists()
    assert (tmp_path / "track.dat").exists()


def test_afm_skyrmion_profile_matches_ferromagnet_without_demag():
    f = F.Film(64, 64, 1e-9, MS, A, 0.6e6, 3.5e-3, demag="none")
    mf, _, _ = F.minimise(f, F.initial(f, "skyrmion", R0=8e-9), tol=5)
    a = F.AFMFilm(64, 64, 1e-9, MS, A, 0.6e6, 3.5e-3, J_afm=2e7)
    ma, _, _ = F.minimise(a, np.stack([mf, -mf], 1), tol=5)
    assert F.texture_metrics(f, a.neel(ma))["rings_nm"] == pytest.approx(F.texture_metrics(f, mf)["rings_nm"], abs=0.05)
    assert F.topological_charge(ma[:, 0]) == pytest.approx(-1, abs=0.02)
    assert F.topological_charge(ma[:, 1]) == pytest.approx(1, abs=0.02)
    assert np.abs(ma[:, 0] + ma[:, 1]).max() < 1e-6                          # exactly antiparallel at rest


@pytest.mark.parametrize("lattice", ["afm", "saf"])
def test_antiferromagnetic_skyrmion_has_no_hall_effect(lattice):
    if lattice == "afm":
        a = F.AFMFilm(64, 64, 1e-9, MS, A, 0.6e6, 3.5e-3, J_afm=2e7)
    else:
        a = F.AFMFilm(64, 64, 1e-9, MS, A, KU, 3.5e-3, J_afm=1e-3 / 1e-9, demag="local")
    m1 = F.initial(a, "skyrmion", R0=8e-9)
    m, _, _ = F.minimise(a, np.stack([m1, -m1], 1), tol=5)
    a.sot_h, a.sot_sigma = 1e4, (0, 1, 0)
    th = F.thiele_velocity(a, m, 0.1)
    assert abs(th[1]) < 1e-6 * abs(th[0])                                   # Magnus forces of m1 and m2 cancel
    def advance(m, T):                       # the AFM texture accelerates first: measure the steady part
        t, dt = 0.0, 1e-15
        while t < T:
            m, used, dt = F.rk45_step(a, m, min(dt, T - t), 0.1, tol=1e-6)
            t += used
        return m
    m = advance(m, 40e-12)
    c0 = np.array(F.centre(a, a.neel(m)))
    m = advance(m, 40e-12)
    v = ((np.array(F.centre(a, a.neel(m))) - c0 + 32e-9) % 64e-9 - 32e-9) / 40e-12
    assert abs(v[1]) < 0.02 * abs(v[0]) and v[0] == pytest.approx(th[0], rel=0.05)


def test_lattice_checks():
    e = M.MM2DEngine()
    assert not e.validate("--lattice=saf\n--J_rkky=1e-3\n").ok
    assert not e.validate("--lattice=afm\n--mode=dynamics\n--excite=stripe\n--pulse=sine\n--f_rf=8e10\n").ok
    assert M.parse("--lattice=afm\n")["lattice"] == "afm"


@pytest.mark.parametrize("alpha", [0.1, 0.5])
def test_thermal_equilibrium_is_boltzmann(alpha):
    """Independent spins (A = 0) at 300 K: <m_z> against the Boltzmann average, for two dampings."""
    Ms, dx, d, T, B = 0.58e6, 3e-9, 1e-9, 300.0, 0.1
    f = F.Film(16, 16, dx, Ms, 0.0, KU, 0.0, B=(0, 0, B))
    exact = F.boltzmann_mz(Ms, dx * dx * d, KU - 0.5 * F.MU0 * Ms ** 2, B / F.MU0, T)
    dt, rng = 0.05 / f.max_freq(), np.random.default_rng(3)
    m, base, acc = F.initial(f, "uniform"), f.H_ext.copy(), []
    n = int(3e-9 / dt)
    for k in range(n):
        f.H_ext = base + F.thermal_sigma(f, alpha, T, dt, d) * rng.normal(size=m.shape)
        m = F.heun(f, m, dt, alpha)
        if k > n // 5 and k % 5 == 0:
            acc.append(m[2].mean())
    assert np.mean(acc) == pytest.approx(exact, abs=0.012)


def test_temperature_run(tmp_path, monkeypatch):
    s = _run(tmp_path, monkeypatch, "--mode=dynamics\n--texture=skyrmion\n--Nx=40\n--Ny=40\n--R0=6e-9\n--alpha=0.3\n"
             "--Temp=300\n--t=40e-12\n--table_dt=1e-12\n")
    assert s["temperature_K"] == 300 and s["thermal_dt_s"] > 0 and s["diffusion_m2ps"] > 0
    assert s["diffusion_thiele_m2ps"] > 0 and s["texture"] == "skyrmion"
    assert (tmp_path / "skyrmion_track.png").exists()
    assert any("Heun" in w for w in M.MM2DEngine().validate("--mode=dynamics\n--Temp=300\n").warnings)


def test_start_from_an_earlier_run(mm2d):
    first = json.loads(tools.call("start_simulation", {"parameters_text": "--Nx=40\n--Ny=40\n--R0=6e-9\n", "engine": "mm2d",
                                                      "label": "seed"})[0])
    assert S.wait_for_run(first["run_id"], 120)["status"] == "done"
    rid = first["run_id"]
    assert not mm2d.validate(f"--start_from={rid}\n--Nx=48\n").ok                     # another grid
    assert not mm2d.validate("--start_from=r9999\n--Nx=40\n--Ny=40\n").ok             # no such run
    assert not mm2d.validate("--start_from=../etc\n").ok
    out = json.loads(tools.call("start_simulation", {
        "parameters_text": f"--Nx=40\n--Ny=40\n--start_from={rid}\n--mode=dynamics\n--t=10e-12\n--table_dt=1e-12\n",
        "engine": "mm2d", "label": "continued"})[0])
    run = S.wait_for_run(out["run_id"], 120)
    assert run["status"] == "done", run.get("error_tail")
    s = run["summary"]
    assert s["started_from"] == rid and s["texture"] == "skyrmion" and s["relax_iterations"] < 200   # already relaxed


def test_temperature_in_relax_mode_explains_0_K():
    w = M.MM2DEngine().validate("--Temp=25\n").warnings
    assert any("0 K" in x and "mode=dynamics" in x for x in w)


# ---- long-range demagnetising field by FFT (demag=full) ----
from spinmate_core.mm2d import demag as DM  # noqa: E402


@pytest.mark.parametrize("N,d", [(8, 1e-9), (24, 3e-9), (64, 2e-9)])
def test_platelet_demag_factor_matches_aharoni(N, d):
    """A uniformly magnetised free platelet: the average field is -N_z Ms, N_z of the prism (Aharoni)."""
    dx = 2e-9
    dem = DM.Demag(N, N, dx, d, periodic=False)
    M = np.zeros((3, N, N)); M[2] = 1.0
    Nz = -dem.field(M)[2].mean()
    assert Nz == pytest.approx(DM.prism_Nz(N * dx, N * dx, d), rel=1e-6)
    M = np.zeros((3, N, N)); M[0] = 1.0
    Nx_ = -dem.field(M)[0].mean()
    assert Nx_ == pytest.approx(DM.prism_Nz(N * dx, d, N * dx), rel=1e-6)    # the prism turned on its side


@pytest.mark.parametrize("periodic", [True, False])
def test_demag_operator_is_symmetric(periodic):
    """<a, N b> = <b, N a>: the field comes from an energy, so the minimiser and LLG conserve it."""
    rng = np.random.default_rng(0)
    dem = DM.Demag(20, 12, 1e-9, 2e-9, periodic=periodic)
    a, b = rng.normal(size=(2, 3, 12, 20))
    assert np.sum(a * dem.field(b)) == pytest.approx(np.sum(b * dem.field(a)), rel=1e-10)


def test_periodic_film_is_an_infinite_film():
    """A uniform infinite film: H = -Ms along z, nothing in the plane."""
    dem = DM.Demag(16, 16, 1e-9, 3e-9, periodic=True)
    for k, want in ((2, -1.0), (0, 0.0)):
        M = np.zeros((3, 16, 16)); M[k] = 1.0
        assert dem.field(M)[k] == pytest.approx(np.full((16, 16), want), abs=1e-12)


def test_neel_wall_stray_field_energy():
    """Volume charges of a Néel wall in a thin film cost mu0 Ms^2 d ln2 / pi per area more than a Bloch wall."""
    def sig(D, dm):
        f = F.Film(400, 4, 0.5e-9, MS, A, 1.0e6, D, demag=dm, thickness=0.5e-9)
        m, _, _ = F.minimise(f, F.initial(f, "domain_wall"), tol=1.0, max_iter=200000)
        return (f.energy(m, 0.5e-9) - f.energy(F.initial(f, "uniform"), 0.5e-9)) / (2 * f.Ny * f.dx * 0.5e-9)
    extra = (sig(2.5e-3, "full") - sig(0.0, "full")) - (sig(2.5e-3, "local") - sig(0.0, "local"))
    assert extra == pytest.approx(F.MU0 * MS ** 2 * 0.5e-9 * np.log(2) / np.pi, rel=0.02)


def test_full_demag_grows_the_skyrmion():
    """The stray field of the reversed core favours a larger skyrmion than the local approximation."""
    R = {}
    for dm in ("local", "full"):
        f = F.Film(96, 96, 1e-9, MS, A, KU, 3.5e-3, demag=dm, thickness=1e-9)
        m, _, tq = F.minimise(f, F.initial(f, "skyrmion"), tol=10, max_iter=20000)
        assert tq < 10
        R[dm] = F.texture_metrics(f, m)["rings_nm"][0]
    assert R["full"] > R["local"] * 1.05


def test_demag_checks():
    v = M.parse("--lattice=afm\n--demag=full")
    assert any("demag=full" in e for e in M.check(v)[0])
    v = M.parse("--thickness=20e-9\n--D=0\n--texture=random")
    assert any("demag=full" in w for w in M.check(v)[1])
    assert M.estimate_s(M.parse("--demag=full")) > M.estimate_s(M.parse(""))
