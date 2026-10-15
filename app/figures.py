"""Publication figures of one or more runs (matplotlib + SciencePlots, no LaTeX needed)."""
import io
import threading

from . import simulations as S

_lock = threading.Lock()   # pyplot state is global

# key: (label, function of (m1, m2) arrays -> 1D array)
QUANTITIES = {
    "m1x": ("$m_{1x}$", lambda a, b: a[:, 0]), "m1y": ("$m_{1y}$", lambda a, b: a[:, 1]),
    "m1z": ("$m_{1z}$", lambda a, b: a[:, 2]),
    "m2x": ("$m_{2x}$", lambda a, b: b[:, 0]), "m2y": ("$m_{2y}$", lambda a, b: b[:, 1]),
    "m2z": ("$m_{2z}$", lambda a, b: b[:, 2]),
    "nx": ("$n_x$", lambda a, b: (a[:, 0] - b[:, 0]) / 2), "ny": ("$n_y$", lambda a, b: (a[:, 1] - b[:, 1]) / 2),
    "nz": ("$n_z$", lambda a, b: (a[:, 2] - b[:, 2]) / 2),
    "dot": (r"$\mathbf{m}_1\cdot\mathbf{m}_2$", lambda a, b: (a * b).sum(axis=1)),
    "mz": ("$m_z$ (net)", lambda a, b: (a[:, 2] + b[:, 2]) / 2),
}
# same colours and dashes as the app: m1 blue, m2 magenta, n black; x dashed, y dotted, z solid
COLOR = {"m1": "#1F4FD1", "m2": "#B0127F", "n": "#17212B", "dot": "#2E7D32", "mz": "#C2410C"}
DASH = {"x": (0, (5, 3)), "y": (0, (1.5, 2)), "z": "-"}
RUN_COLORS = ["#1F4FD1", "#B0127F", "#C2410C", "#2E7D32", "#6B3FA0", "#0F766E"]
STYLES = {"science": ["science", "no-latex"], "ieee": ["science", "ieee", "no-latex"],
          "nature": ["science", "nature", "no-latex"], "plain": []}
SIZES = {"science": (3.5, 2.6), "ieee": (3.3, 2.5), "nature": (3.5, 2.6), "plain": (6.0, 3.6)}
MIME = {"pdf": "application/pdf", "svg": "image/svg+xml", "png": "image/png"}


def _style(q):
    if q in ("dot", "mz"):
        return COLOR[q], "-"
    return COLOR[q[:-1]], DASH[q[-1]]


def render(run_ids, quantities=("m1z", "m2z", "nz"), t0_ns=None, t1_ns=None, style="science", fmt="pdf",
           width_in=None, height_in=None, title="", labels=None):
    """Returns (bytes, mime type, file name). One run: each quantity in its own colour and dash.
    Several runs: one colour per run, one dash per quantity."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import scienceplots  # noqa: F401  (registers the styles)

    ids = [r for r in run_ids if S._valid(r, "r")][:6]
    qs = [q for q in quantities if q in QUANTITIES][:8] or ["nz"]
    if not ids:
        raise ValueError("no valid run ids")
    fmt = fmt if fmt in MIME else "pdf"
    style = style if style in STYLES else "science"
    w, h = SIZES[style]
    w, h = float(width_in or w), float(height_in or h)

    data = {}
    for rid in ids:
        d = S._load_series(rid)
        if d is None:
            raise ValueError(f"{rid} has no trajectory")
        t = d[:, 0] * 1e9
        keep = np.ones(len(t), bool)
        if t0_ns is not None:
            keep &= t >= t0_ns
        if t1_ns is not None:
            keep &= t <= t1_ns
        if keep.sum() < 2:
            raise ValueError(f"{rid} has no points in the chosen time range")
        data[rid] = (t[keep], d[keep, 1:4], d[keep, 4:7])
    span = max(v[0][-1] for v in data.values()) - min(v[0][0] for v in data.values())
    unit, scale = ("ps", 1e3) if span < 1.0 else ("ns", 1.0)
    names = labels or {}

    with _lock, plt.style.context(STYLES[style]):
        fig, ax = plt.subplots(figsize=(w, h))
        many = len(ids) > 1
        dashes = ["-", (0, (5, 3)), (0, (1.5, 2)), (0, (6, 2, 1.5, 2))]
        for i, rid in enumerate(ids):
            t, a, b = data[rid]
            for j, q in enumerate(qs):
                label, f = QUANTITIES[q]
                if many:
                    color, ls = RUN_COLORS[i % len(RUN_COLORS)], dashes[j % len(dashes)]
                    name = f"{names.get(rid) or rid}" + (f", {label}" if len(qs) > 1 else "")
                else:
                    (color, ls), name = _style(q), label
                ax.plot(t * scale, f(a, b), color=color, linestyle=ls, linewidth=1.0, label=name)
        ax.set_xlabel(f"Time ({unit})")
        ax.set_ylabel(QUANTITIES[qs[0]][0] if len(qs) == 1 else r"$M/M_s$")
        lo, hi = ax.get_ylim()
        if lo >= -1.1 and hi <= 1.1:
            ax.set_ylim(-1.05, 1.05)
        if title:
            ax.set_title(title)
        ax.legend(fontsize="small", ncol=2 if len(ax.lines) > 3 else 1, frameon=False)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=300 if fmt == "png" else None)
        plt.close(fig)
    return buf.getvalue(), MIME[fmt], f"{'_'.join(ids)}_{'-'.join(qs)}.{fmt}"
