"""Pictures of a 3D texture: the m_z = 0 surface coloured by the in-plane direction, and two cuts with arrows."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                   # noqa: E402
from matplotlib.patches import FancyArrow                         # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection           # noqa: E402

from ..mm2d.render import INK, _wheel, colour                     # noqa: E402

LIGHT = np.array([-0.4, -0.6, 0.7]) / np.linalg.norm([-0.4, -0.6, 0.7])


def _surface(ax, m, x, y, z, mask):
    from skimage.measure import marching_cubes
    mz = m[2].copy()
    if mask is not None:
        mz[~mask] = 1.0
    if mz.min() >= 0 or mz.max() <= 0:
        return False
    verts, faces, _, _ = marching_cubes(np.transpose(mz, (2, 1, 0)), 0.0)    # (x, y, z) index space
    d = x[1] - x[0]
    idx = np.clip(np.round(verts).astype(int), 0, [len(x) - 1, len(y) - 1, len(z) - 1])
    vm = m[:, idx[:, 2], idx[:, 1], idx[:, 0]]
    pts = np.stack([x[0] + verts[:, 0] * d, y[0] + verts[:, 1] * d, z[0] + verts[:, 2] * d], 1)
    fn = np.cross(pts[faces[:, 1]] - pts[faces[:, 0]], pts[faces[:, 2]] - pts[faces[:, 0]])
    fn /= np.linalg.norm(fn, axis=1, keepdims=True) + 1e-30
    shade = 0.45 + 0.55 * np.abs(fn @ LIGHT)
    fc = np.clip(colour(*vm[:, faces].mean(2)) * shade[:, None], 0, 1)
    ax.add_collection3d(Poly3DCollection(pts[faces], facecolors=fc, edgecolors="none"))
    return pts


def _outline(ax, x, y, z, mask):
    if mask is not None:                                           # the disk: two circles and four lines
        r = (np.sqrt(mask[0].sum() / np.pi)) * (x[1] - x[0])
        t = np.linspace(0, 2 * np.pi, 120)
        for zz in (z[0], z[-1]):
            ax.plot(r * np.cos(t), r * np.sin(t), np.full_like(t, zz), color="#B9B0CC", lw=0.9)
        for a in (0.25, 0.75, 1.25, 1.75):
            ax.plot([r * np.cos(a * np.pi)] * 2, [r * np.sin(a * np.pi)] * 2, [z[0], z[-1]], color="#D9D3E6", lw=0.7)
    else:
        X, Y, Z = (x[0], x[-1]), (y[0], y[-1]), (z[0], z[-1])
        for a in X:
            for b in Y:
                ax.plot([a, a], [b, b], Z, color="#D9D3E6", lw=0.7)
        for zz in Z:
            ax.plot([X[0], X[1], X[1], X[0], X[0]], [Y[0], Y[0], Y[1], Y[1], Y[0]], [zz] * 5, color="#B9B0CC", lw=0.9)


def _cut(ax, mm, u, v, a, b, inside, la, lb, step):
    """mm (3, nv, nu) on a plane with coordinates u (cols) and v (rows); arrows use components a, b."""
    rgb = colour(*mm)
    if inside is not None:
        rgb = np.where(inside[..., None], rgb, 1.0)
    du = u[1] - u[0]
    ax.imshow(rgb, extent=[u[0] - du / 2, u[-1] + du / 2, v[0] - du / 2, v[-1] + du / 2], origin="lower",
              interpolation="bicubic", aspect="equal")
    L = 0.95 * step * du
    for i in range(step // 2, len(v), step):
        for j in range(step // 2, len(u), step):
            if inside is not None and not inside[i, j]:
                continue
            vec = mm[:, i, j]; ca, cb = vec[a], vec[b]; ip = float(np.hypot(ca, cb))
            if ip < 0.3:
                continue
            ax.add_patch(FancyArrow(u[j] - ca * L / 2, v[i] - cb * L / 2, ca * L, cb * L, width=0.13 * step * du * ip,
                                    head_width=0.4 * step * du * ip, head_length=0.34 * step * du * ip,
                                    length_includes_head=True, fc=colour(*vec), ec="#14111F", lw=0.5))
    ax.set_xlabel(f"{la} (nm)", fontsize=15); ax.set_ylabel(f"{lb} (nm)", fontsize=15); ax.tick_params(labelsize=12)
    for sp in ax.spines.values():
        sp.set_color("#B9B0CC")


def save_views(m, x, y, z, caption, mask=None, prefix="texture"):
    """m (3, Nz, Ny, Nx); x, y, z in nm. Writes <prefix>_3d.png, <prefix>_2d.png and <prefix>_3d_2d.png."""
    Nz, Ny, Nx = m.shape[1:]
    step = max(2, int(round(max(Nx, Ny) / 15)))
    xz, xy = m[:, :, Ny // 2, :], m[:, Nz // 2]
    in_xz = None if mask is None else mask[:, Ny // 2, :]
    in_xy = None if mask is None else mask[Nz // 2]

    def draw3d(ax):
        _surface(ax, m, x, y, z, mask)
        _outline(ax, x, y, z, mask)
        ax.set_xlim(x[0], x[-1]); ax.set_ylim(y[0], y[-1]); ax.set_zlim(z[0], z[-1])
        ax.set_box_aspect((x[-1] - x[0], y[-1] - y[0], z[-1] - z[0]), zoom=1.25)
        ax.view_init(elev=26, azim=-58); ax.set_axis_off()

    fig = plt.figure(figsize=(9, 7))
    draw3d(fig.add_axes([0, 0.06, 1, 0.94], projection="3d"))
    _wheel(fig, [0.86, 0.8, 0.09, 0.09], (0.905, 0.79))
    fig.text(0.03, 0.03, caption, fontsize=10, color=INK)
    fig.savefig(f"{prefix}_3d.png", dpi=130, bbox_inches="tight", pad_inches=0.2); plt.close(fig)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5.4))
    _cut(a1, xz, x, z, 0, 2, in_xz, "x", "z", step); a1.set_title("cut y = 0", fontsize=13, color=INK)
    _cut(a2, xy, x, y, 0, 1, in_xy, "x", "y", step); a2.set_title("cut z = 0", fontsize=13, color=INK)
    fig.tight_layout(); fig.savefig(f"{prefix}_2d.png", dpi=130, bbox_inches="tight", pad_inches=0.2); plt.close(fig)

    fig = plt.figure(figsize=(18, 7.4))
    draw3d(fig.add_axes([-0.02, 0.06, 0.46, 0.92], projection="3d"))
    a1 = fig.add_axes([0.47, 0.17, 0.22, 0.72]); _cut(a1, xz, x, z, 0, 2, in_xz, "x", "z", step)
    a1.set_title("cut y = 0", fontsize=13, color=INK)
    a2 = fig.add_axes([0.73, 0.17, 0.22, 0.72]); _cut(a2, xy, x, y, 0, 1, in_xy, "x", "y", step)
    a2.set_title("cut z = 0", fontsize=13, color=INK)
    _wheel(fig, [0.955, 0.2, 0.04, 0.1], (0.975, 0.18))
    fig.text(0.02, 0.03, caption, fontsize=13, color=INK)
    fig.savefig(f"{prefix}_3d_2d.png", dpi=120, bbox_inches="tight", pad_inches=0.25); plt.close(fig)
