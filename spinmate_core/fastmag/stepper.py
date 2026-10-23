"""Time steppers and the minimiser of the micromagnetic engines, the same algorithms on every backend.

A stepper holds the film on its device. The runner sets the drive (field pulse amplitude, thermal noise),
calls rk45 / heun / minimise, and takes host copies of m only when it writes output."""
import numpy as np

from .layout import GAMMA0, pack

# Dormand-Prince 5(4), as in mm2d.solver
_A = [[], [1 / 5], [3 / 40, 9 / 40], [44 / 45, -56 / 15, 32 / 9],
      [19372 / 6561, -25360 / 2187, 64448 / 6561, -212 / 729],
      [9017 / 3168, -355 / 33, 46732 / 5247, 49 / 176, -5103 / 18656],
      [35 / 384, 0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84]]
_B5 = [35 / 384, 0, 500 / 1113, 125 / 192, -2187 / 6784, 11 / 84, 0]
_B4 = [5179 / 57600, 0, 7571 / 16695, 393 / 640, -92097 / 339200, 187 / 2100, 1 / 40]


class NumpyStepper:
    """The reference code of mm2d.solver, unchanged, behind the stepper interface."""
    name = "numpy"

    def __init__(self, film, alpha=0.0, base=None, pulse=None):
        from ..mm2d import solver as S
        self.S, self.film, self.alpha = S, film, alpha
        self.base = film.H_ext.copy() if base is None else base
        self.pulse = pulse
        self._amp = 0.0
        self.thickness = None

    def put(self, m):
        return m

    def get(self, m):
        return m

    def set_field(self, amp=0.0, noise=None):
        h = self.base
        if self.pulse is not None and amp:
            h = h + self.pulse * amp
        if noise is not None:
            h = h + noise
        self.film.H_ext = h

    def rk45(self, m, dt, tol):
        return self.S.rk45_step(self.film, m, dt, self.alpha, tol=tol)

    def heun(self, m, dt, noise):
        self.set_field(self._amp, noise)
        return self.S.heun(self.film, m, dt, self.alpha)

    def drive(self, amp):
        self._amp = amp
        self.set_field(amp)

    def minimise(self, m, tol, max_iter, callback=None):
        return self.S.minimise(self.film, m, tol=tol, max_iter=max_iter, callback=callback)

    def energy(self, m):
        return self.film.energy(m, self.thickness) if self.thickness else self.film.energy(m)

    def thermal(self, sigma, rng, shape):
        return sigma * rng.normal(size=shape)

    def done(self):
        self.film.H_ext = self.base


class FastStepper:
    """numba on the CPU or numba.cuda + CuPy on the GPU. m lives as (3, S, Nz, Ny, Nx) on the device."""

    def __init__(self, backend, shape, G, P, max_freq, alpha=0.0, mask=None, base=(0.0, 0.0, 0.0), pulse=None,
                 demag=None):
        self.name = backend
        self.shape = tuple(shape)                      # the engine's own shape of m
        S_, Nz, Ny, Nx = int(G[3]), int(G[2]), int(G[1]), int(G[0])
        self.shape5 = (3, S_, Nz, Ny, Nx)
        self.n = 3 * S_ * Nz * Ny * Nx
        if backend == "gpu":
            try:
                import cupy as xp
            except ImportError:                        # the CUDA simulator of the tests: NumPy stands in for CuPy
                import os
                if os.environ.get("NUMBA_ENABLE_CUDASIM") != "1":
                    raise
                xp = np
            from . import gpu as K
        else:
            xp = np
            from . import cpu as K
        self.xp, self.K = xp, K
        self.Gh = np.asarray(G, np.int64)
        self.G, self.P = xp.asarray(G, dtype=xp.int64), xp.asarray(P, dtype=xp.float64)
        a = np.broadcast_to(np.asarray(alpha, float), (Ny, Nx))
        self.alpha = xp.asarray(np.array(a, float))
        self.alpha_max = float(a.max())
        mk = np.ones((Nz, Ny, Nx), np.uint8) if mask is None else np.broadcast_to(np.asarray(mask, np.uint8), (Nz, Ny, Nx))
        self.mask = xp.asarray(np.array(mk, np.uint8))
        self.base = xp.asarray(np.broadcast_to(np.asarray(base, float).reshape(3, 1, 1, 1, 1), self.shape5).copy())
        self.pulse = None if pulse is None else xp.asarray(np.array(np.broadcast_to(pulse, self.shape5), float))
        self.hext = self.base.copy()
        self.demag = demag                             # host callable m5 -> H (A/m), CPU only
        self.max_freq = max_freq
        self.k = xp.zeros((7, self.n))
        self.tmp = xp.zeros(self.n)
        self.tq = xp.zeros(self.shape5[1:]) if backend == "gpu" else None
        self._amp = 0.0
        self._noise = None
        self.threads = K.use_threads(self.n // 3) if backend == "cpu" else None
        self.zero = xp.zeros(self.shape5)
        self.dV = None                                 # cell volume, for the energy
        self.Ms = float(P[2])

    # ---- data in and out
    def put(self, m):
        return self.xp.asarray(np.array(m, float).reshape(self.shape5))      # a copy: the stepper writes into it

    def get(self, m):
        h = m.get() if self.name == "gpu" and hasattr(m, "get") else m
        return np.array(h).reshape(self.shape)

    def _noise5(self, noise):
        if noise is None:
            return None
        return self.xp.asarray(np.asarray(noise, float).reshape(self.shape5)) if isinstance(noise, np.ndarray) else noise

    def set_field(self, amp=0.0, noise=None):
        h = self.base
        if self.pulse is not None and amp:
            h = h + self.pulse * amp
        if noise is not None:
            h = h + self._noise5(noise)
        self.hext = h

    def drive(self, amp):
        self._amp = amp
        self.set_field(amp)

    def done(self):
        self.hext = self.base

    def thermal(self, sigma, rng, shape):
        """The thermal field of one step: on the CPU the same random numbers as the numpy code; on the GPU drawn there."""
        if self.name == "gpu":
            if not hasattr(self, "_rng"):
                self._rng = self.xp.random.default_rng(int(rng.integers(2 ** 31)))
            s = self.xp.asarray(np.asarray(sigma, float))
            return (s * self._rng.standard_normal(self.shape, dtype=self.xp.float64)).reshape(self.shape5)
        return sigma * rng.normal(size=shape)

    def energy(self, m):
        """Total energy (J), as Film.energy: -mu0 Ms dV sum (m.H_int / 2 + m.H_ext), on the device."""
        h = self.xp.empty(self.shape5)
        hd = self.zero if self.demag is None else self.demag(m)
        if self.name == "gpu":
            self.K.field_all(m, hd, self.mask, self.G, self.P, self.Gh, h)
        else:
            self.K.field_all(m, hd, self.mask, self.G, self.P, h)
        mk = self.mask[None, None].astype(m.dtype)
        e = (0.5 * (m * h).sum(0) + (m * self.base).sum(0)) * mk[0]
        return float(-4e-7 * np.pi * self.Ms * self.dV * e.sum())

    # ---- the effective field and dm/dt
    def _h(self, m5):
        if self.demag is None:
            return self.hext
        return self.hext + self.demag(m5)

    def rhs(self, m5, out5):
        if self.name == "gpu":
            self.K.rhs_all(m5, self._h(m5), self.alpha, self.mask, self.G, self.P, self.Gh, out5)
        else:
            self.K.rhs_all(m5, self._h(m5), self.alpha, self.mask, self.G, self.P, out5)

    def _norm(self, flat):
        if self.name == "gpu":
            v = flat.reshape(3, -1)
            v /= self.xp.sqrt((v * v).sum(0))
        else:
            self.K.normalise(flat.reshape(3, -1))

    def _lincomb(self, m_flat, coef, n, dt, out):
        if self.name == "gpu":
            c = self.xp.asarray(np.asarray(coef[:n], float))
            out[...] = m_flat + dt * self.xp.tensordot(c, self.k[:n], axes=1)
        else:
            self.K.lincomb(m_flat, self.k, np.asarray(coef, float), n, dt, out)

    def _maxdiff(self, coef, dt):
        if self.name == "gpu":
            c = self.xp.asarray(np.asarray(coef, float))
            return float(self.xp.abs(dt * self.xp.tensordot(c, self.k, axes=1)).max())
        return float(self.K.maxdiff(self.k, np.asarray(coef, float), 7, dt))

    def rk45(self, m, dt, tol):
        """One adaptive Dormand-Prince step, as mm2d.solver.rk45_step: returns (m_new, dt_used, dt_next)."""
        mf = m.reshape(-1)
        s5 = self.shape5
        self.rhs(m, self.k[0].reshape(s5))
        err_c = [b5 - b4 for b5, b4 in zip(_B5, _B4)]
        while True:
            for i in range(1, 7):
                self._lincomb(mf, _A[i] + [0.0] * (7 - len(_A[i])), i, dt, self.tmp)
                self.rhs(self.tmp.reshape(s5), self.k[i].reshape(s5))
            err = self._maxdiff(err_c, dt)
            fac = 0.9 * (tol / max(err, 1e-30)) ** 0.2
            if err <= tol:
                out = self.xp.empty_like(mf)
                self._lincomb(mf, _B5, 7, dt, out)
                self._norm(out)
                return out.reshape(s5), dt, dt * min(4.0, max(0.2, fac))
            dt *= max(0.2, fac)

    def heun(self, m, dt, noise):
        """One stochastic Heun step with a fixed thermal field (noise in the engine's shape, or None)."""
        self.set_field(self._amp, noise)
        s5 = self.shape5
        mf = m.reshape(-1)
        self.rhs(m, self.k[0].reshape(s5))
        self._lincomb(mf, [1.0], 1, dt, self.tmp)
        self._norm(self.tmp)
        self.rhs(self.tmp.reshape(s5), self.k[1].reshape(s5))
        out = self.xp.empty_like(mf)
        self._lincomb(mf, [0.5, 0.5], 2, dt, out)
        self._norm(out)
        return out.reshape(s5)

    def _descent(self, m5, g5):
        if self.name == "gpu":
            return self.K.descent_all(m5, self._h(m5), self.mask, self.G, self.P, self.Gh, g5, self.tq)
        return float(self.K.descent_all(m5, self._h(m5), self.mask, self.G, self.P, g5))

    def minimise(self, m, tol, max_iter, callback=None):
        """Steepest descent with Barzilai-Borwein steps, as mm2d.solver.minimise. Returns (m, iterations, torque)."""
        xp, s5 = self.xp, self.shape5
        g = xp.empty(s5)
        self._descent(m, g)
        tau = 0.5 / (self.max_freq / GAMMA0)
        m_new, g_new = xp.empty(s5), xp.empty(s5)
        for it in range(1, max_iter + 1):
            m_new[...] = m - tau * g
            self._norm(m_new.reshape(-1))
            t = self._descent(m_new, g_new)
            s, y = (m_new - m).reshape(-1), (g_new - g).reshape(-1)
            sy = float(xp.dot(s, y))
            if sy != 0:
                tau_bb = float(xp.dot(s, s)) / sy if it % 2 else sy / float(xp.dot(y, y))
                tau = -tau_bb if tau_bb < 0 else tau_bb
            m, m_new = m_new, m
            g, g_new = g_new, g
            if it % 20 == 0:
                if callback:
                    callback(it, t, m)                 # m on the device: get() it for host work
                if t < tol:
                    return m, it, t
        return m, max_iter, self._descent(m, g_new)


def for_mm2d(film, backend, alpha=0.0, pulse=None):
    """A stepper for an mm2d film (Film or AFMFilm) on `backend` ('numpy', 'cpu' or 'gpu')."""
    if backend == "numpy":
        st = NumpyStepper(film, alpha, pulse=pulse)
        st.thickness = film.thickness
        return st
    afm = hasattr(film, "J_afm")
    S_ = 2 if afm else 1
    bc = "periodic" if film.boundary == "periodic" else "free"
    G, P = pack(film.Nx, film.Ny, 1, S_, film.dx, film.Ms, film.A, film.Ku, film.D, film.dmi, (bc, bc, "periodic"),
                local_demag=film.demag == "local", ghost=True, J=getattr(film, "J_afm", 0.0),
                has_mask=film.mask is not None, stt_u=getattr(film, "stt_u", None),
                stt_beta=getattr(film, "stt_beta", 0.0), sot_h=getattr(film, "sot_h", 0.0),
                sot_sigma=tuple(getattr(film, "sot_sigma", (0.0, 0.0, 0.0))))
    demag = None
    if film._demag is not None:
        mk = film.mask if film.mask is not None else 1.0
        demag = lambda m5: film._demag.field(film.Ms * m5[:, 0, 0] * mk).reshape(3, 1, 1, film.Ny, film.Nx)
    shape = (3, 2, film.Ny, film.Nx) if afm else (3, film.Ny, film.Nx)
    if pulse is not None:
        pulse = np.broadcast_to(np.asarray(pulse, float).reshape(3, -1, film.Ny, film.Nx)[:, :1],
                                (3, S_, film.Ny, film.Nx))[:, :, None]
    st = FastStepper(backend, shape, G, P, film.max_freq(), alpha=alpha,
                     mask=None if film.mask is None else film.mask[None], base=film.H_ext.reshape(3),
                     pulse=pulse, demag=demag)
    st.dV = film.dx * film.dx * film.thickness
    return st


def for_mm3d(slab, backend):
    if backend == "numpy":
        st = NumpyStepper(slab, 0.0)
        st.thickness = None
        return st
    bcz = {"pinned": "pinned", "free": "free", "periodic": "periodic"}[slab.surfaces]
    bc = "periodic" if slab.boundary == "periodic" else "free"
    G, P = pack(slab.Nx, slab.Ny, slab.Nz, 1, slab.dx, slab.Ms, slab.A, slab.Ku, slab.D, slab.dmi, (bc, bc, bcz),
                local_demag=False, ghost=False, has_mask=slab.mask is not None)
    st = FastStepper(backend, (3, slab.Nz, slab.Ny, slab.Nx), G, P, slab.max_freq(),
                     mask=slab.mask, base=slab.H_ext.reshape(3))
    st.dV = slab.dx ** 3
    return st
