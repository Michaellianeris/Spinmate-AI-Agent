"""2D finite-difference micromagnetics of a thin ferromagnetic film (one cell thick).

Effective field: exchange, uniaxial anisotropy along z, Zeeman, interfacial (Neel) or bulk (Bloch) DMI and the
local thin-film demagnetising field -Ms mz. LLG with Heun steps; damping-only steps for relaxation.
Units SI; m has shape (3, Ny, Nx).
"""
import numpy as np

MU0 = 4e-7 * np.pi
GAMMA0 = 2.211e5            # gamma * mu0, m/(A s)


class Film:
    def __init__(self, Nx, Ny, dx, Ms, A, Ku, D=0.0, dmi="interfacial", demag="local", boundary="periodic",
                 B=(0.0, 0.0, 0.0)):
        self.Nx, self.Ny, self.dx = int(Nx), int(Ny), float(dx)
        self.Ms, self.A, self.Ku, self.D = float(Ms), float(A), float(Ku), float(D)
        self.dmi, self.demag, self.boundary = dmi, demag, boundary
        self.H_ext = np.asarray(B, float).reshape(3, 1, 1) / MU0
        self.x = (np.arange(self.Nx) - self.Nx / 2 + 0.5) * self.dx
        self.y = (np.arange(self.Ny) - self.Ny / 2 + 0.5) * self.dx

    # ---- derivatives (periodic, or zero normal derivative at free edges)
    def _shift(self, f, step, axis):
        if self.boundary == "periodic":
            return np.roll(f, -step, axis)
        pad = [(0, 0)] * f.ndim
        pad[axis] = (1, 1)
        g = np.pad(f, pad, mode="edge")
        sl = [slice(None)] * f.ndim
        sl[axis] = slice(1 + step, g.shape[axis] - 1 + step)
        return g[tuple(sl)]

    def ddx(self, f):
        return (self._shift(f, 1, -1) - self._shift(f, -1, -1)) / (2 * self.dx)

    def ddy(self, f):
        return (self._shift(f, 1, -2) - self._shift(f, -1, -2)) / (2 * self.dx)

    def lap(self, f):
        return (self._shift(f, 1, -1) + self._shift(f, -1, -1) + self._shift(f, 1, -2) + self._shift(f, -1, -2)
                - 4 * f) / self.dx ** 2

    # ---- fields
    def h_internal(self, m):
        """Exchange + anisotropy + DMI + demag (all linear in m), A/m."""
        mx, my, mz = m
        h = 2 * self.A / (MU0 * self.Ms) * self.lap(m)
        h[2] += 2 * self.Ku / (MU0 * self.Ms) * mz
        if self.demag == "local":
            h[2] -= self.Ms * mz
        if self.D:
            c = 2 * self.D / (MU0 * self.Ms)
            if self.dmi == "interfacial":         # E = D [mz div m - (m.grad) mz]
                h[0] += c * self.ddx(mz)
                h[1] += c * self.ddy(mz)
                h[2] -= c * (self.ddx(mx) + self.ddy(my))
            else:                                  # bulk: E = D m.(curl m)
                h[0] -= c * self.ddy(mz)
                h[1] += c * self.ddx(mz)
                h[2] -= c * (self.ddx(my) - self.ddy(mx))
        return h

    def h_eff(self, m):
        return self.h_internal(m) + self.H_ext

    def energy(self, m, thickness):
        """Total energy (J) of the film; quadratic terms counted with 1/2."""
        dv = self.dx * self.dx * thickness
        e = -MU0 * self.Ms * (0.5 * np.sum(m * self.h_internal(m), 0) + np.sum(m * self.H_ext, 0))
        return float(e.sum() * dv)

    def torque(self, m):
        return float(np.abs(np.cross(m, self.h_eff(m), axis=0)).max())

    def max_freq(self):
        """Largest angular frequency on the grid, for the time step."""
        h = 8 * self.A / (MU0 * self.Ms * self.dx ** 2) + 2 * abs(self.Ku) / (MU0 * self.Ms) + self.Ms \
            + 4 * abs(self.D) / (MU0 * self.Ms * self.dx) + float(np.abs(self.H_ext).max())
        return GAMMA0 * h


def _norm(m):
    return m / np.linalg.norm(m, axis=0)


def llg_rhs(film, m, alpha, precess=True):
    h = film.h_eff(m)
    mxh = np.cross(m, h, axis=0)
    damp = np.cross(m, mxh, axis=0)
    pre = -GAMMA0 / (1 + alpha ** 2)
    return pre * ((mxh if precess else 0) + alpha * damp)


def heun(film, m, dt, alpha, precess=True):
    k1 = llg_rhs(film, m, alpha, precess)
    mp = _norm(m + dt * k1)
    k2 = llg_rhs(film, mp, alpha, precess)
    return _norm(m + 0.5 * dt * (k1 + k2))


# ---- initial states
def initial(film, kind="skyrmion", R0=10e-9, m0=(0.0, 0.0, 1.0), seed=0, width=None):
    X, Y = np.meshgrid(film.x, film.y)
    r, phi = np.hypot(X, Y), np.arctan2(Y, X)
    if film.dmi == "bulk":
        phi = phi + np.pi / 2                      # Bloch
    w = width or max(2 * film.dx, np.sqrt(film.A / max(abs(film.Ku - 0.5 * MU0 * film.Ms ** 2), 1.0)))
    f = lambda u: (1 - np.tanh(u / w)) / 2
    if kind == "uniform":
        m = np.broadcast_to(np.asarray(m0, float).reshape(3, 1, 1), (3, film.Ny, film.Nx)).copy()
        return _norm(m)
    if kind == "random":
        return _norm(np.random.default_rng(seed).normal(size=(3, film.Ny, film.Nx)))
    if kind == "skyrmion":
        th = np.pi * f(r - R0)
    elif kind == "skyrmionium":
        th = np.pi * (f(r - R0) + f(r - 2.5 * R0))
    elif kind == "domain_wall":                    # two walls across x, one full turn (periodic film)
        Lx = film.Nx * film.dx
        th = np.pi * ((1 - f(X + Lx / 4)) + (1 - f(X - Lx / 4)))
        phi = np.zeros_like(X) + (np.pi / 2 if film.dmi == "bulk" else 0.0)
        m = np.stack([np.sin(th) * np.cos(phi), np.sin(th) * np.sin(phi), np.cos(th)])
        return _norm(m + 1e-3 * np.random.default_rng(seed).normal(size=m.shape))   # breaks the symmetry
    else:
        raise ValueError(f"unknown initial state {kind!r}")
    return np.stack([np.sin(th) * np.cos(phi), np.sin(th) * np.sin(phi), np.cos(th)])


# ---- analysis
def topological_charge(m):
    """Berg-Luescher lattice charge: sum of signed solid angles of the triangles (periodic)."""
    def omega(a, b, c):
        num = np.sum(a * np.cross(b, c, axis=0), 0)
        den = 1 + np.sum(a * b, 0) + np.sum(b * c, 0) + np.sum(c * a, 0)
        return 2 * np.arctan2(num, den)
    m00 = m; m10 = np.roll(m, -1, 2); m01 = np.roll(m, -1, 1); m11 = np.roll(m10, -1, 1)
    return float((omega(m00, m10, m11) + omega(m00, m11, m01)).sum() / (4 * np.pi))


def texture_metrics(film, m):
    """Background direction, radius of the reversed region and the rings of m_z = 0 along the centre line."""
    mz = m[2]
    back = 1.0 if np.median(mz) >= 0 else -1.0
    rev = (mz * back) < 0
    out = {"background_mz": back, "reversed_area_nm2": float(rev.sum() * (film.dx * 1e9) ** 2)}
    out["radius_nm"] = float(np.sqrt(out["reversed_area_nm2"] / np.pi)) if rev.any() else 0.0
    out["crosses_film"] = bool(rev.any() and (rev.any(1).all() or rev.any(0).all()))   # stripes or walls
    w = np.abs(back - mz)                          # centre of the texture
    if w.sum() > 1e-9:
        X, Y = np.meshgrid(film.x, film.y)
        cx, cy = float((w * X).sum() / w.sum()), float((w * Y).sum() / w.sum())
        out["centre_nm"] = [round(cx * 1e9, 3), round(cy * 1e9, 3)]
        j = int(np.argmin(np.abs(film.y - cy)))
        row, xs = mz[j], film.x
        rings = sorted({round(abs(float(xs[i] + (xs[i + 1] - xs[i]) * row[i] / (row[i] - row[i + 1])) - cx) * 1e9, 2)
                        for i in range(len(row) - 1) if row[i] * row[i + 1] < 0 and xs[i] >= cx})
        out["rings_nm"] = rings
    return out


# ---- adaptive Runge-Kutta (Dormand-Prince 5(4)) for the dynamics
_DP_A = [[], [1 / 5], [3 / 40, 9 / 40], [44 / 45, -56 / 15, 32 / 9],
         [19372 / 6561, -25360 / 2187, 64448 / 6561, -212 / 729],
         [9017 / 3168, -355 / 33, 46732 / 5247, 49 / 176, -5103 / 18656],
         [35 / 384, 0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84]]
_DP_B5 = [35 / 384, 0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84, 0]
_DP_B4 = [5179 / 57600, 0, 7571 / 16695, 393 / 640, -92097 / 339200, 187 / 2100, 1 / 40]


def rk45_step(film, m, dt, alpha, tol=1e-5):
    """One adaptive step; returns (m_new, dt_used, dt_next). The error is the largest change of any m component."""
    while True:
        k = []
        for i in range(7):
            mi = m + dt * sum(a * kk for a, kk in zip(_DP_A[i], k)) if i else m
            k.append(llg_rhs(film, mi, alpha))
        m5 = m + dt * sum(b * kk for b, kk in zip(_DP_B5, k))
        m4 = m + dt * sum(b * kk for b, kk in zip(_DP_B4, k))
        err = float(np.abs(m5 - m4).max())
        fac = 0.9 * (tol / max(err, 1e-30)) ** 0.2
        if err <= tol:
            return _norm(m5), dt, dt * min(4.0, max(0.2, fac))
        dt *= max(0.2, fac)


# ---- energy minimisation: steepest descent with Barzilai-Borwein steps (as mumax3 Minimize)
def minimise(film, m, tol=1.0, max_iter=200000, callback=None):
    """Lowers the energy until max |m x H| < tol (A/m). Returns (m, iterations, final torque)."""
    def grad(m):
        h = film.h_eff(m)
        return np.cross(m, np.cross(m, h, axis=0), axis=0)     # -m x (m x H) lowers the energy
    g = grad(m)
    tau = 0.5 / (film.max_freq() / GAMMA0)
    for it in range(1, max_iter + 1):
        m_new = _norm(m + tau * g * -1)
        g_new = grad(m_new)
        s, y = (m_new - m).ravel(), (g_new - g).ravel()
        sy = float(s @ y)
        if sy != 0:
            tau_bb = float(s @ s) / sy if it % 2 else sy / float(y @ y)
            tau = -tau_bb if tau_bb < 0 else tau_bb     # step along -g uses positive tau
        m, g = m_new, g_new
        t = float(np.abs(np.cross(m, film.h_eff(m), axis=0)).max()) if it % 20 == 0 else None
        if callback and t is not None:
            callback(it, t, m)
        if t is not None and t < tol:
            return m, it, t
    return m, max_iter, float(np.abs(np.cross(m, film.h_eff(m), axis=0)).max())
