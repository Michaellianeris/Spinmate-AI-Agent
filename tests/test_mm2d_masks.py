"""Films cut to a shape (mask=disk, strip, racetrack or an uploaded PNG), with the DMI boundary condition at the edges."""
import io
import json

import numpy as np
import pytest
from PIL import Image

import spinmate_core.mm2d as M
from spinmate_core import simulations as S
from spinmate_core.mm2d import geometry as G, solver as F

A, KU, MS, D = 15e-12, 0.8e6, 0.58e6, 3.5e-3
KEFF = KU - 0.5 * F.MU0 * MS ** 2


def test_built_in_shapes_and_png_round_trip(tmp_path):
    G.make_examples(tmp_path)
    for name in G.BUILT_IN:
        assert (G.load(f"{name}.png", 256, 128, tmp_path) == G.built_in(name, 256, 128)).all()
    disk = G.built_in("disk", 64, 64)
    assert disk.mean() == pytest.approx(np.pi / 4, rel=0.05)
    assert G.built_in("strip", 64, 64).mean() == pytest.approx(0.5, abs=0.04)


def test_png_top_row_is_plus_y(tmp_path):
    a = np.full((10, 20), 255, np.uint8); a[:3] = 0                    # film along the top of the picture
    Image.fromarray(a).save(tmp_path / "top.png")
    shape = G.from_png(tmp_path / "top.png", 20, 10)
    assert shape[-3:].all() and not shape[:-3].any()


@pytest.mark.parametrize("dmi", ["interfacial", "bulk"])
def test_edge_tilt_matches_the_boundary_condition(dmi):
    """At a free edge 2A dtheta/dn = D, so sin(theta_edge) = D delta / 2A (Rohart and Thiaville 2013)."""
    dx, N = 0.25e-9, 240
    f = F.Film(4, N, dx, MS, A, KU, D, dmi=dmi, boundary="periodic")
    mk = np.ones((N, 4), bool); mk[:24] = mk[-24:] = False
    f.set_mask(mk)
    m = F.initial(f, "uniform"); m = F._norm(m + 1e-3 * np.random.default_rng(0).normal(size=m.shape) * mk)
    m, _, _ = F.minimise(f, m, tol=0.05, max_iter=200000)
    top = np.where(mk[:, 2])[0][-1]
    th = np.arccos(m[2, top, 2])
    delta = np.sqrt(A / KEFF)
    s0 = np.arccosh(1 / np.sin(th)) * delta - dx / 2                  # the edge is half a cell beyond the last cell
    assert 1 / np.cosh(s0 / delta) == pytest.approx(D * delta / (2 * A), rel=0.01)
    tilt = m[:2, top, 2]                                               # Neel at the edge for interfacial, Bloch for bulk
    assert abs(tilt[1 if dmi == "interfacial" else 0]) > 10 * abs(tilt[0 if dmi == "interfacial" else 1])


def test_skyrmion_in_a_disk_and_empty_cells_stay_put():
    f = F.Film(64, 64, 1e-9, MS, A, KU, D, boundary="free")
    shape = G.built_in("disk", 64, 64)
    f.set_mask(shape)
    m = np.where(shape, F.initial(f, "skyrmion"), np.array([0, 0, 1.0])[:, None, None])
    m, _, tq = F.minimise(f, m, tol=10)
    assert tq < 10 and F.topological_charge(m) == pytest.approx(-1, abs=0.05)
    assert (m[:, ~shape] == np.array([[0], [0], [1.0]])).all()
    assert F.texture_metrics(f, m)["radius_nm"] > 5


def test_mask_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MASKS", tmp_path)
    errs, _ = M.check(M.parse("--mask=nothere.png"))
    assert any("no uploaded PNG" in e for e in errs)
    a = np.full((50, 100), 255, np.uint8); a[10:40, 5:95] = 0
    Image.fromarray(a).save(tmp_path / "bar.png")
    errs, warns = M.check(M.parse("--mask=bar.png\n--Nx=128\n--Ny=128\n--boundary=free"))
    assert not errs and any("stretched" in w for w in warns)
    assert not M.check(M.parse("--mask=racetrack\n--Nx=256\n--Ny=64\n--boundary=free"))[0]


def test_upload_accepts_only_png(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MASKS", tmp_path)
    buf = io.BytesIO(); Image.new("L", (40, 20), 0).save(buf, "PNG")
    name, err = S.save_mask("my shape!.png", buf.getvalue())
    assert err is None and name == "my_shape_.png" and (tmp_path / name).exists()
    buf = io.BytesIO(); Image.new("RGB", (40, 20)).save(buf, "JPEG")
    assert S.save_mask("x.jpg", buf.getvalue())[1] == "the mask must be a PNG"
    assert S.save_mask("x.png", b"hello")[1] == "not a picture"


def test_run_in_a_racetrack(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "parameters.txt").write_text("--texture=skyrmion\n--mask=racetrack\n--boundary=free\n--Nx=128\n"
                                             "--Ny=48\n--R0=6e-9\n")
    from spinmate_core.mm2d import runner
    runner.main()
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["texture"] == "skyrmion" and s["mask"] == "racetrack" and s["film_area_nm2"] < 128 * 48
    assert (tmp_path / "mask.png").exists() and (tmp_path / "texture_3d_2d.png").exists()
