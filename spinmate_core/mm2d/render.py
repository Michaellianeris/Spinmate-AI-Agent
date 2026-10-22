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


SUB1, SUB2 = np.array([1.0, 0.353, 0.259]), np.array([0.353, 0.451, 1.0])      # #FF5A42, #5A73FF


def draw_3d(ax, m, x, y, step, round_view=False, pair=None):
    """x, y in nm; m shape (3, Ny, Nx). round_view: a disc around a localised texture (skyrmion)."""
    x, y = x - (x[0] + x[-1]) / 2, y - (y[0] + y[-1]) / 2       # no axes here: centre the film
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
            if pair is not None:                  # two sublattices on a checkerboard: m1 red, m2 blue
                k = ((i - rows.start) // step + (j - cols_.start) // step) % 2
                v = pair[k][:, i, j] / np.linalg.norm(pair[k][:, i, j])
                base = SUB1 if k == 0 else SUB2
            else:
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
    pad = max(3.0, 0.02 * hx)
    ax.set_xlim(-hx - pad, hx + pad + 1)
    ax.set_ylim(-hy * s - pad - 2, hy * s + L + pad)
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


def save_views(m, x_nm, y_nm, caption, prefix="texture", round_view=False, step=None, pair=None):
    """Writes <prefix>_3d.png, <prefix>_2d.png and the combined <prefix>_3d_2d.png."""
    step = step or step_for(max(len(x_nm), len(y_nm)))
    asp = (y_nm[-1] - y_nm[0]) / max(x_nm[-1] - x_nm[0], 1e-9)
    fig, ax = plt.subplots(figsize=(11, 7 * min(max(asp, 0.5), 1.4)))
    draw_3d(ax, m, x_nm, y_nm, step, round_view, pair)
    _wheel(fig, [0.86, 0.80, 0.09, 0.09], (0.905, 0.79))
    fig.text(0.05, 0.04, caption, fontsize=12, color=INK)
    fig.savefig(f"{prefix}_3d.png", dpi=140, bbox_inches="tight", pad_inches=0.2); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 7.5))
    draw_2d(ax, m, x_nm, y_nm, step)
    fig.savefig(f"{prefix}_2d.png", dpi=140, bbox_inches="tight", pad_inches=0.2); plt.close(fig)

    fig = plt.figure(figsize=(17, 7.2))
    a3 = fig.add_axes([0.0, 0.1, 0.56, 0.88]); a2 = fig.add_axes([0.63, 0.14, 0.3, 0.8])
    draw_3d(a3, m, x_nm, y_nm, step, round_view, pair); draw_2d(a2, m, x_nm, y_nm, step)
    _wheel(fig, [0.935, 0.16, 0.06, 0.13], (0.965, 0.13))
    fig.text(0.02, 0.04, caption, fontsize=13, color=INK)
    fig.savefig(f"{prefix}_3d_2d.png", dpi=120, bbox_inches="tight", pad_inches=0.25); plt.close(fig)


# ---- spin waves: mx as a surface and a map, and the dispersion
WAVE = matplotlib.colors.LinearSegmentedColormap.from_list(
    "spinmate_wave", ["#2B3FC9", "#5A73FF", "#F7F5FB", "#FF5A42", "#C2261A"])


def save_spinwave(dmx, x_nm, y_nm, antenna, caption, absorb_nm=0.0, prefix="spinwave_map"):
    """dmx: change of mx from the ground state, shape (Ny, Nx)."""
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    lim = float(np.percentile(np.abs(dmx), 99.5)) or 1e-12
    norm = matplotlib.colors.Normalize(-lim, lim)
    fig = plt.figure(figsize=(17, 7.2))
    a3 = fig.add_axes([-0.04, 0.04, 0.6, 0.96], projection="3d")
    st = max(1, min(int(round(max(dmx.shape) / 160)), min(dmx.shape) // 2))
    X, Y = np.meshgrid(x_nm[::st], y_nm[::st])
    Z = dmx[::st, ::st]
    span = max(x_nm[-1] - x_nm[0], y_nm[-1] - y_nm[0])
    zs = 0.05 * span / lim
    ls = matplotlib.colors.LightSource(azdeg=225, altdeg=40)
    rgb = ls.shade(Z * zs, cmap=WAVE, norm=norm, blend_mode="soft", vert_exag=1.0, dx=x_nm[st] - x_nm[0],
                   dy=x_nm[st] - x_nm[0])
    a3.plot_surface(X, Y, Z * zs, facecolors=rgb, rstride=1, cstride=1, linewidth=0, antialiased=False, shade=False)
    if antenna.any():
        cols = np.where(antenna.any(0))[0]; rows_ = np.where(antenna.any(1))[0]
        xa, ya = x_nm[[cols[0], cols[-1]]], y_nm[[rows_[0], rows_[-1]]]
        a3.bar3d(xa[0], ya[0], 0.08 * span, max(xa[1] - xa[0], 0.6), max(ya[1] - ya[0], 0.6), 0.02 * span,
                 color="#C79BFF", alpha=0.85, shade=True)
    a3.set_box_aspect((x_nm[-1] - x_nm[0], y_nm[-1] - y_nm[0], 0.2 * span), zoom=1.35)
    a3.view_init(elev=36, azim=-60)
    a3.set_axis_off()
    a2 = fig.add_axes([0.6, 0.16, 0.33, 0.76])
    dx = x_nm[1] - x_nm[0]
    ext = [x_nm[0] - dx / 2, x_nm[-1] + dx / 2, y_nm[0] - dx / 2, y_nm[-1] + dx / 2]
    a2.imshow(dmx, extent=ext, origin="lower", cmap=WAVE, norm=norm, interpolation="bicubic", aspect="equal")
    if antenna.any():
        a2.contour(x_nm, y_nm, antenna.astype(float), levels=[0.5], colors="#7A35A8", linewidths=1.6)
    if absorb_nm > 0:
        for x0, x1 in ((ext[0], ext[0] + absorb_nm), (ext[1] - absorb_nm, ext[1])):
            a2.axvspan(x0, x1, color="#120D26", alpha=0.18, lw=0)
    a2.set_xlabel("x (nm)", fontsize=17); a2.set_ylabel("y (nm)", fontsize=17); a2.tick_params(labelsize=14, length=5)
    for sp in a2.spines.values():
        sp.set_color("#B9B0CC")
    cax = fig.add_axes([0.945, 0.2, 0.012, 0.6])
    cb = fig.colorbar(matplotlib.cm.ScalarMappable(norm=norm, cmap=WAVE), cax=cax)
    cb.set_label("Δmx", fontsize=14); cb.ax.tick_params(labelsize=11); cb.formatter.set_powerlimits((-2, 2))
    fig.text(0.02, 0.03, caption, fontsize=13, color=INK)
    fig.savefig(f"{prefix}.png", dpi=120, bbox_inches="tight", pad_inches=0.25); plt.close(fig)


def save_dispersion(k, f, P, k_line, f_line, f_max, caption, name="dispersion.png"):
    fig, ax = plt.subplots(figsize=(9, 6.2))
    kk = k * 1e-6                                       # rad/µm
    sel = f <= 1.1 * f_max
    img = np.log10(P[sel] / P[sel].max() + 1e-6)
    ax.pcolormesh(kk, f[sel] / 1e9, img, cmap="magma", shading="auto", vmin=-4.5, vmax=0)
    ax.plot(k_line * 1e-6, f_line / 1e9, color="#5BD0FF", lw=1.6, ls="--", label="analytic")
    inside = f_line <= 1.25 * f_max                      # beyond that the waves are not driven (and alias)
    k_lim = float(np.abs(k_line[inside]).max() * 1e-6) if inside.any() else float(np.abs(kk).max())
    ax.set_ylim(0, 1.1 * f_max / 1e9); ax.set_xlim(-k_lim, k_lim)
    ax.set_xlabel("k (rad/µm)", fontsize=16); ax.set_ylabel("f (GHz)", fontsize=16); ax.tick_params(labelsize=13)
    ax.legend(loc="upper center", fontsize=12, framealpha=0.85)
    fig.text(0.01, -0.02, caption, fontsize=10.5, color=INK)
    fig.savefig(name, dpi=130, bbox_inches="tight", pad_inches=0.25); plt.close(fig)


def save_profile(x_nm, row, antenna_x, absorb_nm, lam_nm=None, name="spinwave_profile.png"):
    """Δmx along y = 0 at the end of the run."""
    fig, ax = plt.subplots(figsize=(8, 5.2))
    ax.plot(x_nm, row, color="#B24BD0", lw=1.6)
    ax.axhline(0, color="#B9B0CC", lw=0.8)
    for xa in antenna_x:
        ax.axvline(xa, color="#FF5A42", lw=1.2, ls="--")
    if absorb_nm > 0:
        ax.axvspan(x_nm[0], x_nm[0] + absorb_nm, color="#120D26", alpha=0.1, lw=0)
        ax.axvspan(x_nm[-1] - absorb_nm, x_nm[-1], color="#120D26", alpha=0.1, lw=0)
    ax.set_xlabel("x (nm)", fontsize=16); ax.set_ylabel("Δmx at y = 0", fontsize=16); ax.tick_params(labelsize=13)
    ax.ticklabel_format(axis="y", style="sci", scilimits=(-2, 2))
    if lam_nm:
        ax.set_title(f"λ = {lam_nm:.1f} nm · dashed: antenna · shaded: absorbing edges", fontsize=12, color=INK)
    fig.tight_layout(); fig.savefig(name, dpi=130); plt.close(fig)


def save_track(m, x_nm, y_nm, track, summary, vals, name="skyrmion_track.png"):
    """Top view of the final texture with the path of its centre, the current direction and the velocity."""
    fig, ax = plt.subplots(figsize=(8, 7))
    dx = x_nm[1] - x_nm[0]
    ax.imshow(colour(*m), extent=[x_nm[0] - dx / 2, x_nm[-1] + dx / 2, y_nm[0] - dx / 2, y_nm[-1] + dx / 2],
              origin="lower", interpolation="bicubic")
    Lx, Ly = len(x_nm) * dx, len(y_nm) * dx
    px = (track[:, 1] * 1e9 - x_nm[0] + dx / 2) % Lx + x_nm[0] - dx / 2     # back into the periodic film
    py = (track[:, 2] * 1e9 - y_nm[0] + dx / 2) % Ly + y_nm[0] - dx / 2
    jumps = np.where((np.abs(np.diff(px)) > Lx / 2) | (np.abs(np.diff(py)) > Ly / 2))[0] + 1
    for seg_x, seg_y in zip(np.split(px, jumps), np.split(py, jumps)):
        ax.plot(seg_x, seg_y, color="#FFFFFF", lw=4.5, solid_capstyle="round")
        ax.plot(seg_x, seg_y, color="#7A35A8", lw=2.2, solid_capstyle="round")
    ax.plot(px[0], py[0], "o", ms=9, mfc="#FFFFFF", mec="#7A35A8", mew=2)
    j = np.asarray(vals["j"], float)[:2]
    jn = np.linalg.norm(j)
    if jn:
        L = 0.18 * min(Lx, Ly)
        x0, y0 = x_nm[0] + 0.1 * Lx, y_nm[-1] - 0.1 * Ly
        ax.annotate("", (x0 + L * j[0] / jn, y0 + L * j[1] / jn), (x0, y0),
                    arrowprops=dict(arrowstyle="-|>", color="#FF5A42", lw=2.5, mutation_scale=22))
        ax.text(x0, y0 - 0.06 * Ly, "j", color="#FF5A42", fontsize=16, fontweight="bold")
    ax.set_xlabel("x (nm)", fontsize=16); ax.set_ylabel("y (nm)", fontsize=16); ax.tick_params(labelsize=13)
    if "velocity_mps" in summary:
        v = summary["velocity_mps"]
        th = summary.get("thiele_velocity_mps")
        title = (f"v = ({v[0]:.1f}, {v[1]:.1f}) m/s · Hall angle {summary['hall_angle_deg']:.1f}°"
                 + (f" · Thiele ({th[0]:.1f}, {th[1]:.1f}) m/s" if th else ""))
    else:                                           # Brownian motion at temperature
        D, Dt = summary["diffusion_m2ps"], summary.get("diffusion_thiele_m2ps")
        title = (f"T = {summary.get('temperature_K', 0):.0f} K · D = {D * 1e9:.3g} nm²/ns"
                 + (f" · Thiele {Dt * 1e9:.3g} nm²/ns" if Dt else ""))
    ax.set_title(title, fontsize=12, color=INK)
    for sp in ax.spines.values():
        sp.set_color("#B9B0CC")
    fig.tight_layout(); fig.savefig(name, dpi=130); plt.close(fig)


# ---- animations: GIF always, MP4 too when ffmpeg is installed
def write_movie(fig, update, n, name, fps=12, dpi=90):
    """Draws update(i) for i < n once, writes <name>.gif and, when ffmpeg is installed, <name>.mp4.
    Returns the files written."""
    import shutil
    import subprocess
    from PIL import Image
    fig.set_dpi(dpi)
    shots = []
    for i in range(n):
        update(i)
        fig.canvas.draw()
        shots.append(np.asarray(fig.canvas.buffer_rgba())[..., :3].copy())
    plt.close(fig)
    h, w = shots[0].shape[:2]
    h2, w2 = h - h % 2, w - w % 2                     # H.264 wants even sizes
    # one palette for all frames (no flicker), no dithering (smaller files)
    pal = Image.fromarray(np.concatenate(shots[::max(1, n // 8)])).quantize(colors=160, method=Image.Quantize.MEDIANCUT)
    pics = [Image.fromarray(s_).quantize(palette=pal, dither=Image.Dither.NONE) for s_ in shots]
    pics[0].save(f"{name}.gif", save_all=True, append_images=pics[1:], duration=int(1000 / fps), loop=0,
                 optimize=True)
    out = [f"{name}.gif"]
    ff = shutil.which("ffmpeg")
    if ff:
        try:
            p = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                "-s", f"{w2}x{h2}", "-r", str(fps), "-i", "-", "-pix_fmt", "yuv420p",
                                "-vcodec", "libx264", "-crf", "20", f"{name}.mp4"],
                               input=b"".join(s_[:h2, :w2].tobytes() for s_ in shots), capture_output=True,
                               timeout=300)
            if p.returncode == 0:
                out.append(f"{name}.mp4")
        except Exception:                              # an MP4 is a bonus: the GIF is enough
            pass
    return out


def save_animation(frames, times, x_nm, y_nm, kind, caption, track=None, antenna=None, absorb_nm=0.0,
                   name="animation"):
    """frames: (n, Ny, Nx) changes of mx for kind='wave', or (n, 3, Ny, Nx) textures for kind='texture'.
    track: (t, x, y) of the texture centre in s and m, drawn as a growing trail."""
    frames = np.asarray(frames, float)
    dx = x_nm[1] - x_nm[0]
    ext = [x_nm[0] - dx / 2, x_nm[-1] + dx / 2, y_nm[0] - dx / 2, y_nm[-1] + dx / 2]
    asp = (ext[3] - ext[2]) / (ext[1] - ext[0])
    fig = plt.figure(figsize=(7.2, max(3.0, min(7.2, 6.2 * asp + 1.2))))
    ax = fig.add_axes([0.12, 0.16, 0.8, 0.72])
    if kind == "wave":
        lim = float(np.percentile(np.abs(frames[-max(1, len(frames) // 4):]), 99.5)) or 1e-12
        im = ax.imshow(frames[0], extent=ext, origin="lower", cmap=WAVE, vmin=-lim, vmax=lim,
                       interpolation="bicubic", aspect="equal")
        if antenna is not None and antenna.any():
            if min(antenna.shape) >= 2 and not antenna.any(1).all():     # a dot
                ax.contourf(x_nm, y_nm, antenna.astype(float), levels=[0.5, 1.5], colors=["#7A35A8"], alpha=0.45)
            else:                                                          # a stripe across y
                cols = np.where(antenna.any(0))[0]
                ax.axvspan(x_nm[cols[0]] - dx / 2, x_nm[cols[-1]] + dx / 2, color="#7A35A8", alpha=0.45, lw=0)
        if absorb_nm > 0:
            for a0, a1 in ((ext[0], ext[0] + absorb_nm), (ext[1] - absorb_nm, ext[1])):
                ax.axvspan(a0, a1, color="#120D26", alpha=0.18, lw=0)
        quiv = None
    else:
        im = ax.imshow(colour(*frames[0]), extent=ext, origin="lower", interpolation="bicubic", aspect="equal")
        st = max(1, int(round(max(len(x_nm), len(y_nm)) / 18)))
        X, Y = np.meshgrid(x_nm[::st], y_nm[::st])
        def inplane(g):                                # nearly out of plane: no arrow, the colour says it
            hide = np.hypot(g[0], g[1]) < 0.35
            return np.ma.masked_array(g[0], hide), np.ma.masked_array(g[1], hide)
        f0 = frames[0][:, ::st, ::st]
        quiv = ax.quiver(X, Y, *inplane(f0), color="#14111F", scale=1.6 / (st * dx), scale_units="xy",
                         width=0.004, headwidth=4, pivot="mid")
    trail, = ax.plot([], [], ".", color="#FFFFFF", ms=3.5, mec="#7A35A8", mew=0.6)
    Lx, Ly = ext[1] - ext[0], ext[3] - ext[2]
    if track is not None and len(track):
        tr = np.asarray(track, float)
        px = (tr[:, 1] * 1e9 - ext[0]) % Lx + ext[0]
        py = (tr[:, 2] * 1e9 - ext[2]) % Ly + ext[2]
    ax.set_xlabel("x (nm)", fontsize=12); ax.set_ylabel("y (nm)", fontsize=12); ax.tick_params(labelsize=10)
    for sp in ax.spines.values():
        sp.set_color("#B9B0CC")
    title = ax.set_title("", fontsize=12, color=INK)
    fig.text(0.02, 0.03, caption, fontsize=8.5, color=INK, wrap=True)

    def update(i):
        f = frames[i]
        if kind == "wave":
            im.set_data(f)
        else:
            im.set_data(colour(*f))
            g = f[:, ::st, ::st]
            quiv.set_UVC(*inplane(g))
        if track is not None and len(track):
            sel = tr[:, 0] <= times[i] + 1e-18
            trail.set_data(px[sel], py[sel])
        title.set_text(f"t = {times[i] * 1e12:.0f} ps")
        return []
    return write_movie(fig, update, len(frames), name)
