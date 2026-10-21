"""3D finite-difference micromagnetics of a chiral magnet slab: hopfions, skyrmion tubes.

Effective field: exchange, uniaxial anisotropy along z, Zeeman, bulk (Bloch) or interfacial DMI. The top and bottom
surfaces can be pinned along +z (strong perpendicular surface anisotropy), which stabilises hopfions in a slab about
one helical period thick. No demagnetising field. m has shape (3, Nz, Ny, Nx); SI units.
The time steppers and the minimiser of mm2d work unchanged on these arrays."""
import numpy as np

from ..mm2d.solver import GAMMA0, MU0, _norm, llg_rhs, minimise, rk45_step  # noqa: F401  (shared)

UP = np.array([0.0, 0.0, 1.0])


class Slab:
    def __init__(self, Nx, Ny, Nz, dx, Ms, A, Ku=0.0, D=0.0, dmi="bulk", B=(0.0, 0.0, 0.0),
                 boundary="periodic", surfaces="pinned", disk=0.0):
        self.Nx, self.Ny, self.Nz, self.dx = int(Nx), int(Ny), int(Nz), float(dx)
        self.Ms, self.A, self.Ku, self.D = float(Ms), float(A), float(Ku), float(D)
        self.dmi, self.boundary, self.surfaces = dmi, boundary, surfaces
        self.H_ext = np.asarray(B, float).reshape(3, 1, 1, 1) / MU0
        c = lambda n: (np.arange(n) - n / 2 + 0.5) * self.dx
        self.x, self.y, self.z = c(self.Nx), c(self.Ny), c(self.Nz)
        self.mask = None                         # disk > 0: a nanodisk of that diameter with free side walls
        if disk:
            Y, X = np.meshgrid(self.y, self.x, indexing="ij")
            self.mask = np.broadcast_to(np.hypot(X, Y) <= disk / 2, (self.Nz, self.Ny, self.Nx)).copy()

    # neighbours: axis -1 = x, -2 = y (periodic or free), -3 = z (pinned along +z or free)
    def _shift(self, f, step, axis):
        g = self._shift_raw(f, step, axis)
        if self.mask is not None and axis != -3:
            inside = self._shift_raw(self.mask, step, axis)
            g = np.where(inside, g, f)            # outside the disk: free surface, no neighbour
        return g

    def _shift_raw(self, f, step, axis):
        if axis == -3 and self.surfaces == "periodic":
            return np.roll(f, -step, axis)
        if axis == -3:
            pad = [(0, 0)] * f.ndim
            pad[axis] = (1, 1)
            if self.surfaces == "pinned" and f.ndim == 4 and f.shape[0] == 3:
                g = np.pad(f, pad, mode="edge")
                g[:, 0], g[:, -1] = UP[:, None, None], UP[:, None, None]
            else:
                g = np.pad(f, pad, mode="edge")
            sl = [slice(None)] * f.ndim
            sl[axis] = slice(1 + step, g.shape[axis] - 1 + step)
            return g[tuple(sl)]
        if self.boundary == "periodic":
            return np.roll(f, -step, axis)
        pad = [(0, 0)] * f.ndim
        pad[axis] = (1, 1)
        g = np.pad(f, pad, mode="edge")
        sl = [slice(None)] * f.ndim
        sl[axis] = slice(1 + step, g.shape[axis] - 1 + step)
        return g[tuple(sl)]

    def d(self, f, axis):
        return (self._shift(f, 1, axis) - self._shift(f, -1, axis)) / (2 * self.dx)

    def lap(self, f):
        return sum(self._shift(f, 1, a) + self._shift(f, -1, a) for a in (-1, -2, -3)) / self.dx ** 2 \
            - 6 * f / self.dx ** 2

    def h_internal(self, m):
        h = 2 * self.A / (MU0 * self.Ms) * self.lap(m)
        h[2] += 2 * self.Ku / (MU0 * self.Ms) * m[2]
        if self.D:
            c = 2 * self.D / (MU0 * self.Ms)
            dm = {a: self.d(m, a) for a in (-1, -2, -3)}     # d/dx, d/dy, d/dz of all components
            dx_, dy_, dz_ = dm[-1], dm[-2], dm[-3]
            if self.dmi == "bulk":                             # E = D m.(curl m): H = -c curl m
                h[0] -= c * (dy_[2] - dz_[1])
                h[1] -= c * (dz_[0] - dx_[2])
                h[2] -= c * (dx_[1] - dy_[0])
            else:                                              # interfacial, normal along z
                h[0] += c * dx_[2]
                h[1] += c * dy_[2]
                h[2] -= c * (dx_[0] + dy_[1])
        return h

    def h_eff(self, m):
        h = self.h_internal(m) + self.H_ext
        return h if self.mask is None else np.where(self.mask, h, m)     # cells outside never turn

    def energy(self, m):
        dv = self.dx ** 3
        e = -MU0 * self.Ms * (0.5 * np.sum(m * self.h_internal(m), 0) + np.sum(m * self.H_ext, 0))
        if self.mask is not None:
            e = e * self.mask
        return float(e.sum() * dv)

    def max_freq(self):
        h = 12 * self.A / (MU0 * self.Ms * self.dx ** 2) + 2 * abs(self.Ku) / (MU0 * self.Ms) \
            + 6 * abs(self.D) / (MU0 * self.Ms * self.dx) + float(np.abs(self.H_ext).max())
        return GAMMA0 * h


def initial(slab, kind="hopfion", R=None, width=None, seed=0):
    Z, Y, X = np.meshgrid(slab.z, slab.y, slab.x, indexing="ij")
    p = 4 * np.pi * slab.A / abs(slab.D) if slab.D else 20 * slab.dx      # helical period
    if kind == "uniform":
        m = np.zeros((3,) + X.shape); m[2] = 1
        return m
    if kind == "random":
        return _norm(np.random.default_rng(seed).normal(size=(3,) + X.shape))
    if kind == "hopfion":
        R = R or 0.32 * p
        w = width or 0.2 * p
        rp = np.hypot(X, Y)
        rho = np.hypot(rp - R, Z)
        th = np.pi * (1 - np.tanh(rho / w) ** 2) ** 0.8
        ph = np.arctan2(Y, X) + np.arctan2(Z, rp - R) + (np.pi / 2 if slab.dmi == "bulk" else 0)
    elif kind == "skyrmion_tube":
        R = R or 0.25 * p
        w = width or 0.2 * p
        r = np.hypot(X, Y)
        th = np.pi * (1 - np.tanh((r - R) / w)) / 2
        ph = np.arctan2(Y, X) + (np.pi / 2 if slab.dmi == "bulk" else 0)
    else:
        raise ValueError(f"unknown initial state {kind!r}")
    return np.stack([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)])


def hopf_index(m):
    """H = -∫ A·B dV with B_i = (1/8π) ε_ijk m·(∂_j m × ∂_k m) and curl A = B (Coulomb gauge, FFT). Cell units."""
    dd = lambda f, a: (np.roll(f, -1, a) - np.roll(f, 1, a)) / 2
    der = [dd(m, 3), dd(m, 2), dd(m, 1)]                                # d/dx, d/dy, d/dz
    B = np.stack([np.sum(m * np.cross(der[j], der[k], axis=0), 0) for j, k in ((1, 2), (2, 0), (0, 1))]) / (4 * np.pi)
    kz, ky, kx = [np.fft.fftfreq(n) * 2 * np.pi for n in m.shape[1:]]
    KZ, KY, KX = np.meshgrid(kz, ky, kx, indexing="ij")
    K2 = KX ** 2 + KY ** 2 + KZ ** 2; K2[0, 0, 0] = 1
    Bk = [np.fft.fftn(b) for b in B]
    Ak = [1j * (KY * Bk[2] - KZ * Bk[1]) / K2, 1j * (KZ * Bk[0] - KX * Bk[2]) / K2, 1j * (KX * Bk[1] - KY * Bk[0]) / K2]
    A = np.stack([np.real(np.fft.ifftn(a)) for a in Ak])
    return float(-np.sum(A * B))


def metrics(slab, m):
    """Hopf index, rings of m_z = 0 and the core ring (m_z most negative) on the x axis of the middle layer."""
    k = slab.Nz // 2
    row = m[2, k, slab.Ny // 2]
    if slab.mask is not None:
        row = np.where(slab.mask[k, slab.Ny // 2], row, 1.0)
    x = slab.x
    rings = sorted({round(abs(float(x[i] + (x[i + 1] - x[i]) * row[i] / (row[i] - row[i + 1]))) * 1e9, 2)
                    for i in range(len(row) - 1) if row[i] * row[i + 1] < 0})
    i_min = int(np.argmin(row))
    inside = m[2] if slab.mask is None else m[2][slab.mask]
    return {"hopf_index": round(hopf_index(m), 3), "rings_nm": rings,
            "core_ring_radius_nm": round(abs(float(x[i_min])) * 1e9, 2), "min_mz": round(float(row.min()), 3),
            "mean_mz": round(float(inside.mean()), 4)}


def skyrmion_number_z(m, k):
    """Skyrmion number of the layer z = k (Berg-Luescher)."""
    from ..mm2d.solver import topological_charge
    return topological_charge(m[:, k])
