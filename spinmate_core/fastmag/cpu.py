"""CPU kernels: numba, one parallel loop over the rows of cells (all cores)."""
import os

# idle worker threads must sleep, not spin: the solver alternates short parallel loops with NumPy work
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
os.environ.setdefault("KMP_BLOCKTIME", "0")

import numpy as np  # noqa: E402
from numba import get_num_threads, njit, prange, set_num_threads  # noqa: E402

from .cells import make  # noqa: E402

FAST = dict(cache=True, fastmath=True, error_model="numpy")
_neighbour, _field, _rhs, _descent = make(njit(inline="always", **FAST))


@njit(parallel=True, **FAST)
def rhs_all(m, hext, alpha, mask, G, P, out):
    S, Nz, Ny, Nx = m.shape[1], m.shape[2], m.shape[3], m.shape[4]
    for r in prange(S * Nz * Ny):
        s = r // (Nz * Ny)
        k = (r // Ny) % Nz
        j = r % Ny
        for i in range(Nx):
            a, b, c = _rhs(m, hext, alpha, mask, G, P, s, k, j, i)
            out[0, s, k, j, i] = a
            out[1, s, k, j, i] = b
            out[2, s, k, j, i] = c


@njit(parallel=True, **FAST)
def descent_all(m, hext, mask, G, P, out):
    """out = m x (m x H); returns the largest |m x H| component."""
    S, Nz, Ny, Nx = m.shape[1], m.shape[2], m.shape[3], m.shape[4]
    rows = S * Nz * Ny
    tmax = np.zeros(rows)
    for r in prange(rows):
        s = r // (Nz * Ny)
        k = (r // Ny) % Nz
        j = r % Ny
        best = 0.0
        for i in range(Nx):
            a, b, c, t = _descent(m, hext, mask, G, P, s, k, j, i)
            out[0, s, k, j, i] = a
            out[1, s, k, j, i] = b
            out[2, s, k, j, i] = c
            if t > best:
                best = t
        tmax[r] = best
    return tmax.max()


@njit(parallel=True, **FAST)
def field_all(m, hext, mask, G, P, out):
    """The effective field with hext (zero hext: the internal field), zero outside the mask."""
    S, Nz, Ny, Nx = m.shape[1], m.shape[2], m.shape[3], m.shape[4]
    for r in prange(S * Nz * Ny):
        s = r // (Nz * Ny)
        k = (r // Ny) % Nz
        j = r % Ny
        for i in range(Nx):
            if G[10] == 1 and mask[k, j, i] == 0:
                out[0, s, k, j, i] = 0.0
                out[1, s, k, j, i] = 0.0
                out[2, s, k, j, i] = 0.0
                continue
            a, b, c = _field(m, hext, mask, G, P, s, k, j, i)
            out[0, s, k, j, i] = a
            out[1, s, k, j, i] = b
            out[2, s, k, j, i] = c


@njit(parallel=True, **FAST)
def lincomb(m, K, coef, n, dt, out):
    """out = m + dt * sum_{j<n} coef[j] K[j] (all arrays flat)."""
    for p in prange(m.size):
        acc = 0.0
        for q in range(n):
            acc += coef[q] * K[q, p]
        out[p] = m[p] + dt * acc


@njit(parallel=True, **FAST)
def maxdiff(K, coef, n, dt):
    """max |dt * sum_j coef[j] K[j]|: the error of an embedded Runge-Kutta pair."""
    size = K.shape[1]
    chunks = 256
    best = np.zeros(chunks)
    step = (size + chunks - 1) // chunks
    for c in prange(chunks):
        b = 0.0
        for p in range(c * step, min(size, (c + 1) * step)):
            acc = 0.0
            for q in range(n):
                acc += coef[q] * K[q, p]
            acc = abs(dt * acc)
            if acc > b:
                b = acc
        best[c] = b
    return best.max()


@njit(parallel=True, **FAST)
def normalise(m):
    """m (3, cells) to unit length, in place."""
    for p in prange(m.shape[1]):
        n = np.sqrt(m[0, p] ** 2 + m[1, p] ** 2 + m[2, p] ** 2)
        m[0, p] /= n
        m[1, p] /= n
        m[2, p] /= n


CORES = get_num_threads()


def use_threads(cells):
    """Enough threads to pay for themselves: about one per 8,000 cells, at most all cores."""
    n = max(1, min(CORES, cells // 2000))
    set_num_threads(n)
    return n
