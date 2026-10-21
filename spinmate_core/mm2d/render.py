"""Pictures of a 2D texture: a tilted view with shaded 3D arrows and a top view with nm axes.

Film colour: hue = in-plane direction, light = up (+z), dark = down (-z). Arrows take the colour of the film below."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
from matplotlib.collections import PolyCollection     # noqa: E402
from matplotlib.colors import hsv_to_rgb              # noqa: E402
from matplotlib.patches import Ellipse, FancyArrow, Polygon    # noqa: E402
from matplotlib.transforms import Affine2D            # noqa: E402

TILT = np.radians(30)
UP, DOWN = np.array((0.93, 0.92, 0.96)), np.array((0.16, 0.13, 0.28))
INK = "#4A3F66"
VIEW = np.array([0.0, -np.cos(TILT), np.sin(TILT)])   # towards the camera
LIGHT = np.array([-0.45, -0.55, 0.7]) / np.linalg.norm([-0.45, -0.55, 0.7])
SEG = 14


def colour(mx, my, mz):
    mx, my, mz = np.asarray(mx, float), np.asarray(my, float), np.asarray(mz, float)
    hue = (np.arctan2(my, mx) / (2 * np.pi)) % 1
    base = hsv_to_rgb(np.stack([hue, np.ones_like(hue), np.ones_like(hue)], -1))
    w = (np.clip(np.abs(mz), 0, 1) ** 1.6)[..., None]
    return base * (1 - w) + np.where(mz[..., None] > 0, UP, DOWN) * w


def _project(p):
    return np.stack([p[..., 0], p[..., 1] * np.sin(TILT) + p[..., 2] * np.cos(TILT)], -1)


def _arrow(c, v, L, r_shaft, r_head, h_head):
    a = np.array([1.0, 0, 0]) if abs(v[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(v, a); e1 /= np.linalg.norm(e1); e2 = np.cross(v, e1)
    t = np.linspace(0, 2 * np.pi, SEG, endpoint=False)
    ring = np.cos(t)[:, None] * e1 + np.sin(t)[:, None] * e2
    p0 = c - v * L / 2; p1 = p0 + v * (L - h_head); tip = c + v * L / 2
    polys, normals = [], []
    for k in range(SEG):
        k2 = (k + 1) % SEG
        mid = (ring[k] + ring[k2]) / 2
        polys.append([p0 + r_shaft * ring[k], p0 + r_shaft * ring[k2], p1 + r_shaft * ring[k2], p1 + r_shaft * ring[k]])
        normals.append(mid)
        n = mid * h_head + v * r_head
        polys.append([p1 + r_head * ring[k], p1 + r_head * ring[k2], tip]); normals.append(n / np.linalg.norm(n))
        polys.append([p1 + r_head * ring[k], p1 + r_head * ring[k2], p1]); normals.append(-v)
    polys.append([p0 + r_shaft * ring[k] for k in range(SEG)]); normals.append(-v)
    return [np.array(p) for p in polys], np.array(normals)


def _grid(m, x, y, step):
    """Indices of the arrows: a regular grid through the centre of the film."""
    ci, cj = len(y) // 2, len(x) // 2
    return range(ci % step, len(y), step), range(cj % step, len(x), step)


def draw_3d(ax, m, x, y, step, round_view=False):
    """x, y in nm; m shape (3, Ny, Nx). round_view: a disc around a localised texture (skyrmion)."""
    hx, hy = (x[-1] - x[0]) / 2 + (x[1] - x[0]) / 2, (y[-1] - y[0]) / 2 + (y[1] - y[0]) / 2
    s = np.sin(TILT)
    rgb = colour(*m)
    RD = min(hx, hy) - 1.5
    if round_view:
        XX, YY = np.meshgrid(x, y)
        a = np.clip((RD + 1.5 - np.hypot(XX, YY)) / 1.5, 0, 1)
        rgb = np.dstack([rgb, a])
        ax.add_patch(Ellipse((0, -1.0), 2 * RD + 3, (2 * RD + 3) * s + 1.6, fc="#B6AECB", ec="none", zorder=0))
    else:
        ax.add_patch(Polygon([(-hx + 1.5, -hy * s - 2.2), (hx + 1.5, -hy * s - 2.2), (hx + 1.5, hy * s - 2.2),
                              (-hx + 1.5, hy * s - 2.2)], fc="#B6AECB", ec="none", alpha=0.7, zorder=0))
    ax.imshow(rgb, extent=[-hx, hx, -hy, hy], origin="lower", interpolation="bicubic",
              transform=Affine2D().scale(1, s) + ax.transData, zorder=1)
    L = 1.05 * step * (x[1] - x[0])
    polys, cols, depth = [], [], []
    rows, cols_ = _grid(m, x, y, step)
    half = LIGHT + VIEW; half /= np.linalg.norm(half)
    for i in rows:
        for j in cols_:
            if round_view and np.hypot(x[j], y[i]) > RD - 1:
                continue
            v = m[:, i, j] / np.linalg.norm(m[:, i, j])
            base = colour(*v)
            P, Nn = _arrow(np.array([x[j], y[i], 0.0]), v, L, 0.1 * L, 0.26 * L, 0.38 * L)
            for p, n in zip(P, Nn):
                if n @ VIEW < -0.05:
                    continue
                shade = 0.38 + 0.62 * max(0.0, n @ LIGHT) + 0.25 * max(0.0, n @ half) ** 18
                polys.append(_project(p)); cols.append(np.clip(base * shade, 0, 1)); depth.append(p.mean(0) @ VIEW)
    order = np.argsort(depth)
    ax.add_collection(PolyCollection([polys[k] for k in order], facecolors=[cols[k] for k in order],
                                     edgecolors="none", zorder=2))
    ax.set_xlim(-hx - 3, hx + 4)
    ax.set_ylim(-hy * s - 5, hy * s + L + 2)
    ax.set_aspect("equal"); ax.axis("off")


def draw_2d(ax, m, x, y, step):
    dx = x[1] - x[0]
    ax.imshow(colour(*m), extent=[x[0] - dx / 2, x[-1] + dx / 2, y[0] - dx / 2, y[-1] + dx / 2], origin="lower",
              interpolation="bicubic")
    L = 0.95 * step * dx
    rows, cols_ = _grid(m, x, y, step)
    for i in rows:
        for j in cols_:
            v = m[:, i, j]; ip = float(np.hypot(v[0], v[1]))
            if ip < 0.35:
                continue                                  # nearly out of plane: the colour says it
            ax.add_patch(FancyArrow(x[j] - v[0] * L / 2, y[i] - v[1] * L / 2, v[0] * L, v[1] * L,
                                    width=0.13 * step * dx * ip, head_width=0.4 * step * dx * ip,
                                    head_length=0.34 * step * dx * ip, length_includes_head=True,
                                    fc=colour(*v), ec="#14111F", lw=0.6))
    ax.set_xlabel("x (nm)", fontsize=17); ax.set_ylabel("y (nm)", fontsize=17)
    ax.tick_params(labelsize=14, length=5)
    for sp in ax.spines.values():
        sp.set_color("#B9B0CC")


def _wheel(fig, rect, text_xy):
    w = fig.add_axes(rect, projection="polar")
    th = np.linspace(0, 2 * np.pi, 256); rr = np.linspace(0, 1, 40); T, R = np.meshgrid(th, rr)
    w.pcolormesh(T, R, T / (2 * np.pi), cmap="hsv", shading="auto"); w.set_axis_off()
    fig.text(*text_xy, "in-plane\ndirection\n\nlight = up\ndark = down", ha="center", va="top", fontsize=11, color=INK)


def crop(m, x, y, centre_nm=None, half_nm=None):
    """The part of the film around the texture (the whole film when it is small)."""
    if not half_nm:
        return m, x, y
    cx, cy = centre_nm or (0.0, 0.0)
    sx = np.abs(x - cx) <= half_nm; sy = np.abs(y - cy) <= half_nm
    if sx.all() and sy.all():
        return m, x, y
    return m[:, sy][:, :, sx], x[sx] - cx, y[sy] - cy


def step_for(n):
    return max(1, int(round(n / 15)))


def save_views(m, x_nm, y_nm, caption, prefix="texture", round_view=False):
    """Writes <prefix>_3d.png, <prefix>_2d.png and the combined <prefix>_3d_2d.png."""
    step = step_for(max(len(x_nm), len(y_nm)))
    asp = (y_nm[-1] - y_nm[0]) / max(x_nm[-1] - x_nm[0], 1e-9)
    fig, ax = plt.subplots(figsize=(11, 7 * min(max(asp, 0.5), 1.4)))
    draw_3d(ax, m, x_nm, y_nm, step, round_view)
    _wheel(fig, [0.86, 0.80, 0.09, 0.09], (0.905, 0.79))
    fig.text(0.05, 0.04, caption, fontsize=12, color=INK)
    fig.savefig(f"{prefix}_3d.png", dpi=140, bbox_inches="tight", pad_inches=0.2); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 7.5))
    draw_2d(ax, m, x_nm, y_nm, step)
    fig.savefig(f"{prefix}_2d.png", dpi=140, bbox_inches="tight", pad_inches=0.2); plt.close(fig)

    fig = plt.figure(figsize=(17, 7.2))
    a3 = fig.add_axes([0.0, 0.1, 0.56, 0.88]); a2 = fig.add_axes([0.63, 0.14, 0.3, 0.8])
    draw_3d(a3, m, x_nm, y_nm, step, round_view); draw_2d(a2, m, x_nm, y_nm, step)
    _wheel(fig, [0.935, 0.16, 0.06, 0.13], (0.965, 0.13))
    fig.text(0.02, 0.04, caption, fontsize=13, color=INK)
    fig.savefig(f"{prefix}_3d_2d.png", dpi=120, bbox_inches="tight", pad_inches=0.25); plt.close(fig)
