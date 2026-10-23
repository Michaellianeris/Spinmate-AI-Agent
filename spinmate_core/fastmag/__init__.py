"""Fast backends for the micromagnetic engines (mm2d, mm3d).

numpy: the reference NumPy code, one core.
cpu:   the same physics compiled by numba into one fused loop over the cells, on all cores.
gpu:   the same physics as CUDA kernels (numba.cuda) on CuPy arrays, on an NVIDIA GPU.
auto:  gpu when a GPU is usable, else cpu when numba is installed, else numpy."""
import os
import sys

BACKENDS = ("auto", "numpy", "cpu", "gpu")
if getattr(sys, "frozen", False) and "NUMBA_CACHE_DIR" not in os.environ:   # the desktop app: a writable cache
    os.environ["NUMBA_CACHE_DIR"] = os.path.join(os.environ.get("WORKSPACE", os.path.expanduser("~")), ".numba-cache")


def cpu_available():
    try:
        import numba  # noqa: F401
        return True, ""
    except Exception as e:
        return False, f"numba is not installed ({e})"


def gpu_available():
    try:
        from .gpu import available
    except Exception as e:
        return False, f"GPU backend not installed ({type(e).__name__})"
    return available()


def choose(asked, needs_fft=False):
    """The backend to use for `asked`, and a note when it differs from what was asked."""
    if asked == "numpy":
        return "numpy", ""
    gpu_ok, gpu_note = gpu_available() if asked in ("auto", "gpu") else (False, "")
    if asked == "gpu" and needs_fft:
        return ("cpu" if cpu_available()[0] else "numpy"), "demag=full runs on the CPU (no FFT on the GPU yet)"
    if asked == "gpu" and gpu_ok:
        return "gpu", ""
    if asked == "auto" and gpu_ok and not needs_fft:
        return "gpu", ""
    cpu_ok, cpu_note = cpu_available()
    if cpu_ok:
        return "cpu", (f"no GPU: {gpu_note}" if asked == "gpu" else "")
    return "numpy", (cpu_note if asked in ("cpu", "gpu") else "")


def speedup(backend, cells):
    """How much faster than the numpy code one evaluation of the field is (measured on a laptop; a guide)."""
    import os
    if backend == "cpu":
        threads = max(1, min(os.cpu_count() or 1, cells // 2000))
        return 3.5 * threads ** 0.85
    if backend == "gpu":
        return min(100.0, max(2.0, cells * 130e-9 / max(20e-6, cells * 1.5e-9)))
    return 1.0


def warmup():
    """Compiles the CPU kernels once (the Docker image does it at build time), so runs start at once."""
    import numpy as np
    from ..mm2d import solver as S
    from ..mm3d import solver as S3
    from . import stepper as ST
    f = S.Film(8, 8, 1e-9, 5.8e5, 15e-12, 8e5, 3e-3, boundary="free", thickness=1e-9)
    st = ST.for_mm2d(f, "cpu", 0.1)
    m = st.put(S.initial(f, "skyrmion", R0=2e-9))
    m, _, dt = st.rk45(m, 1e-15, 1e-6)
    st.heun(m, 1e-15, None)
    st.minimise(m, 1e3, 21)
    st.energy(m)
    sl = S3.Slab(6, 6, 4, 1e-9, 5e5, 1e-11, 0, 5e-3, boundary="free", surfaces="pinned", disk=5e-9)
    s3 = ST.for_mm3d(sl, "cpu")
    s3.minimise(s3.put(S3.initial(sl, "hopfion")), 1e3, 21)
    return True


def describe():
    """One line for the engine list: where micromagnetic runs go when backend=auto."""
    gpu_ok, gpu = gpu_available()
    if gpu_ok:
        return f"backend auto = GPU ({gpu})"
    if cpu_available()[0]:
        return f"backend auto = CPU, numba on {os.cpu_count()} cores (no GPU: {gpu})"
    return "backend auto = numpy, one core (numba not installed)"
