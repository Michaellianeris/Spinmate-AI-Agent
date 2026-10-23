"""Materials (--material=FeGe ...), the OVF and VTK export of the final texture, and the validation report of all
engines."""
import json

import numpy as np
import pytest

import spinmate_core.mm2d as M
import spinmate_core.mm3d as M3
from spinmate_core import materials, ovf, validation as V


def test_material_sets_values_and_user_lines_win():
    v = M.parse("--material=FeGe\n--texture=skyrmion\n")
    assert v["material"] == "FeGe" and v["Msat"] == 384e3 and v["Aex"] == 8.78e-12 and v["D"] == 1.58e-3
    assert v["dmi"] == "bulk" and v["dx"] == 2e-9 and v["B_ext"] == (0.0, 0.0, 0.37)
    v = M.parse("--B_ext=0,0,0.3\n--material=fe-ge\n--D=1.5e-3\n")
    assert v["material"] == "FeGe" and v["B_ext"] == (0.0, 0.0, 0.3) and v["D"] == 1.5e-3
    with pytest.raises(ValueError, match="not known"):
        M.parse("--material=Unobtainium\n")
    v3 = M3.parse("--material=FeGe\n")
    assert v3["dmi"] == "bulk" and v3["Nz"] == 24 and v3["disk_diameter"] == 144e-9
    assert {m["name"] for m in materials.table()} == {"Pt/Co", "Co/Pd", "FeGe", "MnSi", "YIG"}


@pytest.mark.parametrize("name", list(materials.MATERIALS))
def test_every_material_passes_check(name):
    errors, _ = M.check(M.parse(f"--material={name}\n"))
    assert not errors


def test_skyrmion_in_fege(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "parameters.txt").write_text("--material=FeGe\n--texture=skyrmion\n")
    from spinmate_core.mm2d import runner
    runner.main()
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["texture"] == "skyrmion" and s["topological_charge"] == pytest.approx(-1, abs=0.05)
    assert 30 < s["radius_nm"] < 45
    m = np.load(tmp_path / "m_final.npz")["m"]
    i = int(np.argmin(np.abs(np.load(tmp_path / "m_final.npz")["x"] - s["rings_nm"][0] * 1e-9)))
    row = m[:, m.shape[1] // 2]
    assert abs(row[1, i]) > abs(row[0, i])               # bulk DMI: a Bloch wall (m along y on the x axis)
    # the export
    back, cell = ovf.read(tmp_path / "m_final.ovf")
    assert back.shape == (3, 1, 128, 128) and np.allclose(back[:, 0], m, atol=1e-6) and cell == (2e-9, 2e-9, 2e-9)
    assert np.allclose(ovf.read_vtk(tmp_path / "m_final.vtk")[:, 0], m, atol=1e-6)
    raw = (tmp_path / "m_final.ovf").read_bytes()
    assert raw.startswith(b"# OOMMF OVF 2.0") and b"# Begin: Data Binary 4" in raw
    assert np.frombuffer(raw[raw.index(b"Binary 4\n") + 9:][:4], "<f4")[0] == 1234567.0


def test_export_of_a_mask_and_of_3d(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "parameters.txt").write_text("--texture=skyrmion\n--mask=disk\n--boundary=free\n--Nx=48\n--Ny=48\n"
                                             "--R0=6e-9\n")
    from spinmate_core.mm2d import runner
    runner.main()
    back, _ = ovf.read(tmp_path / "m_final.ovf")
    assert np.all(back[:, 0, 0, 0] == 0) and np.allclose(np.linalg.norm(back[:, 0, 24, 24]), 1, atol=1e-6)
    d3 = tmp_path / "d3"; d3.mkdir(); monkeypatch.chdir(d3)
    (d3 / "parameters.txt").write_text("--Nx=38\n--Ny=38\n--Nz=16\n--D=7.854e-3\n--disk_diameter=36e-9\n"
                                       "--B_ext=0,0,0.45\n--animation=0\n")
    from spinmate_core.mm3d import runner as r3
    r3.main()
    b3, cell = ovf.read(d3 / "m_final.ovf")
    assert b3.shape == (3, 16, 38, 38) and cell == (1e-9, 1e-9, 1e-9)
    assert ovf.read_vtk(d3 / "m_final.vtk").shape == (3, 16, 38, 38)


def test_report_groups_the_engines():
    rs = [V.Result("a", "A", "pass", {}, {}, ""), V.Result("b", "B", "pass", {}, {}, "", engine="mm2d"),
          V.Result("c", "C", "fail", {}, {}, "", engine="mm3d")]
    html = V.report_html(rs)
    assert "2 of 3 cases pass" in html and "macrospin 1/1 · mm2d 1/1 · mm3d 0/1" in html
    assert "mm2d: 2D micromagnetics · 1 of 1 pass" in html
    assert {"mm2d_wall", "mm2d_kittel", "mm2d_dispersion", "mm3d_helix", "mm3d_hopfion"} <= set(V.CASES)
