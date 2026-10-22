"""OVF 2.0 files (the format of OOMMF and mumax3): write text, read text and binary 4/8.

Arrays here are m with shape (3, Nz, Ny, Nx) or (3, Ny, Nx); in the file x runs fastest, then y, then z."""
import re

import numpy as np


def write(path, m, dx, dy, dz, title="m"):
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
            "# End: Header", "# Begin: Data Text"]
    data = np.moveaxis(m, 0, -1).reshape(-1, 3)
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
