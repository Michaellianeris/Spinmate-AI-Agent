"""OVF 2.0 files (the format of OOMMF and mumax3): write text or binary 4, read text and binary 4/8; and legacy
VTK files for ParaView.

Arrays here are m with shape (3, Nz, Ny, Nx) or (3, Ny, Nx); in the file x runs fastest, then y, then z."""
import re

import numpy as np


def write(path, m, dx, dy, dz, title="m", binary=False):
    """binary=True writes "Data Binary 4" (little-endian float32 after the check value 1234567), as mumax3 does."""
    m = np.asarray(m, float)
    if m.ndim == 3:
        m = m[:, None]
    _, nz, ny, nx = m.shape
    head = ["# OOMMF OVF 2.0", "# Segment count: 1", "# Begin: Segment", "# Begin: Header", f"# Title: {title}",
            "# meshtype: rectangular", "# meshunit: m",
            "# xmin: 0", "# ymin: 0", "# zmin: 0", f"# xmax: {nx * dx!r}", f"# ymax: {ny * dy!r}", f"# zmax: {nz * dz!r}",
            "# valuedim: 3", "# valuelabels: m_x m_y m_z", "# valueunits: 1 1 1",
            f"# xbase: {dx / 2!r}", f"# ybase: {dy / 2!r}", f"# zbase: {dz / 2!r}",
            f"# xnodes: {nx}", f"# ynodes: {ny}", f"# znodes: {nz}",
            f"# xstepsize: {dx!r}", f"# ystepsize: {dy!r}", f"# zstepsize: {dz!r}",
            "# End: Header", "# Begin: Data " + ("Binary 4" if binary else "Text")]
    data = np.moveaxis(m, 0, -1).reshape(-1, 3)
    if binary:
        with open(path, "wb") as f:
            f.write(("\n".join(head) + "\n").encode())
            f.write(np.float32(1234567.0).astype("<f4").tobytes())
            f.write(data.astype("<f4").tobytes())
            f.write(b"\n# End: Data Binary 4\n# End: Segment\n")
        return
    with open(path, "w") as f:
        f.write("\n".join(head) + "\n")
        np.savetxt(f, data, fmt="%.10g")
        f.write("# End: Data Text\n# End: Segment\n")


def read(path):
    """Returns (m with shape (3, Nz, Ny, Nx), cell sizes (dx, dy, dz))."""
    raw = open(path, "rb").read()
    head_end = raw.index(b"# Begin: Data")
    head = raw[:head_end].decode("latin-1")
    num = lambda key: float(re.search(rf"#\s*{key}:\s*(\S+)", head).group(1))
    nx, ny, nz = int(num("xnodes")), int(num("ynodes")), int(num("znodes"))
    cell = (num("xstepsize"), num("ystepsize"), num("zstepsize"))
    line_end = raw.index(b"\n", head_end)
    kind = raw[head_end:line_end].decode().split("Data", 1)[1].strip()
    n = nx * ny * nz * 3
    if kind.lower().startswith("text"):
        body = raw[line_end + 1:].decode("latin-1")
        body = "\n".join(l for l in body.splitlines() if not l.startswith("#"))
        data = np.array(body.split(), float)[:n]
    else:
        size = int(kind.split()[-1])
        dt = "<f4" if size == 4 else "<f8"
        start = line_end + 1 + size                     # the first value is a check number
        data = np.frombuffer(raw[start:start + n * size], dt).astype(float)
    m = np.moveaxis(data.reshape(nz, ny, nx, 3), -1, 0)
    return m, cell


def write_vtk(path, m, dx, dy, dz, title="SpinMate m"):
    """Legacy VTK STRUCTURED_POINTS (binary) with the vectors m and the scalar m_z: ParaView, VisIt, Mayavi."""
    m = np.asarray(m, float)
    if m.ndim == 3:
        m = m[:, None]
    _, nz, ny, nx = m.shape
    n = nx * ny * nz
    head = (f"# vtk DataFile Version 3.0\n{title}\nBINARY\nDATASET STRUCTURED_POINTS\n"
            f"DIMENSIONS {nx} {ny} {nz}\nORIGIN {dx / 2!r} {dy / 2!r} {dz / 2!r}\nSPACING {dx!r} {dy!r} {dz!r}\n"
            f"POINT_DATA {n}\nVECTORS m float\n")
    with open(path, "wb") as f:
        f.write(head.encode())
        f.write(np.moveaxis(m, 0, -1).reshape(-1, 3).astype(">f4").tobytes())     # VTK binary is big-endian
        f.write(b"\nSCALARS m_z float 1\nLOOKUP_TABLE default\n")
        f.write(m[2].reshape(-1).astype(">f4").tobytes())
        f.write(b"\n")


def read_vtk(path):
    """The vectors of a file written by write_vtk: (3, Nz, Ny, Nx)."""
    raw = open(path, "rb").read()
    dims = [int(v) for v in re.search(rb"DIMENSIONS (\d+) (\d+) (\d+)", raw).groups()]
    start = raw.index(b"VECTORS m float\n") + len(b"VECTORS m float\n")
    n = dims[0] * dims[1] * dims[2]
    data = np.frombuffer(raw[start:start + 12 * n], ">f4").astype(float)
    return np.moveaxis(data.reshape(dims[2], dims[1], dims[0], 3), -1, 0)
