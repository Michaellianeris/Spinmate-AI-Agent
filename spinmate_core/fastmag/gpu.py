"""GPU kernels: numba.cuda, one thread per cell, on CuPy arrays (NVIDIA GPU with CUDA 12).

The cell physics is the same source as on the CPU (cells.py). The Runge-Kutta stages, norms and reductions use
CuPy array operations."""
import math

from numba import cuda

from .cells import make

_neighbour, _field, _rhs, _descent = make(cuda.jit(device=True, inline=True, fastmath=True))
THREADS = (32, 8, 1)


@cuda.jit(fastmath=True, cache=True)
def _rhs_kernel(m, hext, alpha, mask, G, P, out):
    i, j, r = cuda.grid(3)
    Nx, Ny, Nz, S = G[0], G[1], G[2], G[3]
    if i >= Nx or j >= Ny or r >= S * Nz:
        return
    s = r // Nz
    k = r % Nz
    a, b, c = _rhs(m, hext, alpha, mask, G, P, s, k, j, i)
    out[0, s, k, j, i] = a
    out[1, s, k, j, i] = b
    out[2, s, k, j, i] = c


@cuda.jit(fastmath=True, cache=True)
def _descent_kernel(m, hext, mask, G, P, out, tq):
    i, j, r = cuda.grid(3)
    Nx, Ny, Nz, S = G[0], G[1], G[2], G[3]
    if i >= Nx or j >= Ny or r >= S * Nz:
        return
    s = r // Nz
    k = r % Nz
    a, b, c, t = _descent(m, hext, mask, G, P, s, k, j, i)
    out[0, s, k, j, i] = a
    out[1, s, k, j, i] = b
    out[2, s, k, j, i] = c
    tq[s, k, j, i] = t


@cuda.jit(fastmath=True, cache=True)
def _field_kernel(m, hext, mask, G, P, out):
    i, j, r = cuda.grid(3)
    Nx, Ny, Nz, S = G[0], G[1], G[2], G[3]
    if i >= Nx or j >= Ny or r >= S * Nz:
        return
    s = r // Nz
    k = r % Nz
    if G[10] == 1 and mask[k, j, i] == 0:
        out[0, s, k, j, i] = 0.0
        out[1, s, k, j, i] = 0.0
        out[2, s, k, j, i] = 0.0
        return
    a, b, c = _field(m, hext, mask, G, P, s, k, j, i)
    out[0, s, k, j, i] = a
    out[1, s, k, j, i] = b
    out[2, s, k, j, i] = c


def field_all(m, hext, mask, G, P, Gh, out):
    _field_kernel[_blocks(Gh), THREADS](m, hext, mask, G, P, out)


def _blocks(G):
    Nx, Ny, Nz, S = int(G[0]), int(G[1]), int(G[2]), int(G[3])
    return (math.ceil(Nx / THREADS[0]), math.ceil(Ny / THREADS[1]), S * Nz)


def rhs_all(m, hext, alpha, mask, G, P, Gh, out):
    _rhs_kernel[_blocks(Gh), THREADS](m, hext, alpha, mask, G, P, out)


def descent_all(m, hext, mask, G, P, Gh, out, tq):
    _descent_kernel[_blocks(Gh), THREADS](m, hext, mask, G, P, out, tq)
    return float(tq.max())


def available():
    """(True, name of the GPU) or (False, why not)."""
    try:
        import cupy
        if not cuda.is_available():
            return False, "no CUDA GPU visible (nvidia-smi; in Docker: the GPU compose file)"
        n = cupy.cuda.runtime.getDeviceCount()
        if n < 1:
            return False, "no CUDA GPU visible"
        name = cupy.cuda.runtime.getDeviceProperties(0)["name"]
        return True, name.decode() if isinstance(name, bytes) else str(name)
    except Exception as e:                      # no CuPy, no driver, no numba-cuda
        return False, f"GPU backend not installed ({type(e).__name__}: {e})"[:200]
