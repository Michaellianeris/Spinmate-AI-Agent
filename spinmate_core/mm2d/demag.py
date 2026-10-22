"""Long-range demagnetising field of a film one cell thick, by FFT convolution with the Newell tensor.

The Newell tensor (Newell, Williams and Dunlop, JGR 98, 9551, 1993) is the exact cell-averaged field of uniformly
magnetised rectangular cells, so a uniformly magnetised film made of cells gets exactly the demagnetising factors of
the whole prism. Free films are zero-padded; periodic films add periodic images. H = -N * M."""
import numpy as np


def _f(x, y, z):
    x, y, z = np.abs(x), np.abs(y), np.abs(z)
    x2, y2, z2 = x * x, y * y, z * z
    R = np.sqrt(x2 + y2 + z2)
    out = (2 * x2 - y2 - z2) * R / 6
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.sqrt(x2 + z2)
        out = out + np.where((y > 0) & (t > 0), y / 2 * (z2 - x2) * np.arcsinh(y / np.where(t > 0, t, 1)), 0)
        t = np.sqrt(x2 + y2)
        out = out + np.where((z > 0) & (t > 0), z / 2 * (y2 - x2) * np.arcsinh(z / np.where(t > 0, t, 1)), 0)
        out = out - np.where(x * R > 0, x * y * z * np.arctan(y * z / np.where(x * R > 0, x * R, 1)), 0)
    return out


def _g(x, y, z):
    z = np.abs(z)
    x2, y2, z2 = x * x, y * y, z * z
    R = np.sqrt(x2 + y2 + z2)
    out = -x * y * R / 3
    with np.errstate(divide="ignore", invalid="ignore"):
        nz = lambda a: a != 0
        safe = lambda a: np.where(a != 0, a, 1)                 # signed denominators stay signed
        t = np.sqrt(x2 + y2)
        out = out + np.where(nz(t), x * y * z * np.arcsinh(z / safe(t)), 0)
        t = np.sqrt(y2 + z2)
        out = out + np.where(nz(t), y / 6 * (3 * z2 - y2) * np.arcsinh(x / safe(t)), 0)
        t = np.sqrt(x2 + z2)
        out = out + np.where(nz(t), x / 6 * (3 * z2 - x2) * np.arcsinh(y / safe(t)), 0)
        out = out - np.where(nz(z * R), z * z2 / 6 * np.arctan(x * y / safe(z * R)), 0)
        out = out - np.where(nz(y * R), z * y2 / 2 * np.arctan(x * z / safe(y * R)), 0)
        out = out - np.where(nz(x * R), z * x2 / 2 * np.arctan(y * z / safe(x * R)), 0)
    return out


def _second_diff(fun, X, Y, Z, dx, dy, dz):
    """Sum over the 27 neighbours with weights 8 (-1/2)^(number of shifted axes)."""
    out = np.zeros_like(X, dtype=float)
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            for k in (-1, 0, 1):
                w = 8 * (-0.5) ** (abs(i) + abs(j) + abs(k))
                out += w * fun(X + i * dx, Y + j * dy, Z + k * dz)
    return out


def newell(X, Y, dx, dy, dz):
    """Nxx, Nyy, Nzz, Nxy between two cells of one layer at in-plane offset (X, Y)."""
    Z = np.zeros_like(X)
    c = 1 / (4 * np.pi * dx * dy * dz)
    Nxx = c * _second_diff(_f, X, Y, Z, dx, dy, dz)
    Nyy = c * _second_diff(lambda a, b, cc: _f(b, a, cc), X, Y, Z, dx, dy, dz)
    Nzz = c * _second_diff(lambda a, b, cc: _f(cc, b, a), X, Y, Z, dx, dy, dz)
    Nxy = c * _second_diff(_g, X, Y, Z, dx, dy, dz)
    return Nxx, Nyy, Nzz, Nxy


def dipole(X, Y, V):
    """Point-dipole tensor (far field), same sign convention as newell."""
    R2 = X * X + Y * Y
    R = np.sqrt(R2)
    R5 = R2 * R2 * R
    with np.errstate(divide="ignore", invalid="ignore"):
        Nxx = -V / (4 * np.pi) * (3 * X * X - R2) / R5
        Nyy = -V / (4 * np.pi) * (3 * Y * Y - R2) / R5
        Nzz = V / (4 * np.pi) / (R2 * R)
        Nxy = -V / (4 * np.pi) * 3 * X * Y / R5
    return Nxx, Nyy, Nzz, Nxy


def kernel(Nx, Ny, dx, d, periodic, reach=8.0, near=48):
    """FFT of the tensor on the convolution grid. Free: (2Ny, 2Nx) zero-padded. Periodic: (Ny, Nx), summing images
    out to about `reach` times the larger side of the film along each axis (a thin strip gets many images)."""
    if periodic:
        jx = np.arange(Nx); jx = np.where(jx > Nx // 2, jx - Nx, jx)
        jy = np.arange(Ny); jy = np.where(jy > Ny // 2, jy - Ny, jy)
        shape = (Ny, Nx)
        L = max(Nx, Ny)
        nx_img, ny_img = int(np.ceil(reach * L / Nx)), int(np.ceil(reach * L / Ny))
    else:
        jx = np.concatenate([np.arange(Nx), [0], np.arange(-Nx + 1, 0)])      # offsets 0..Nx-1, (pad), -(Nx-1)..-1
        jy = np.concatenate([np.arange(Ny), [0], np.arange(-Ny + 1, 0)])
        shape = (2 * Ny, 2 * Nx)
        nx_img = ny_img = 0
    JX, JY = np.meshgrid(jx, jy)
    N = [np.zeros(shape) for _ in range(4)]
    for px in range(-nx_img, nx_img + 1):
        for py in range(-ny_img, ny_img + 1):
            ox, oy = (JX + px * Nx).astype(float), (JY + py * Ny).astype(float)
            r = np.hypot(ox, oy)
            close = r <= near
            if close.any():
                parts = newell(ox[close] * dx, oy[close] * dx, dx, dx, d)
                for k in range(4):
                    N[k][close] += parts[k]
            if (~close).any():
                parts = dipole(ox[~close] * dx, oy[~close] * dx, dx * dx * d)
                for k in range(4):
                    N[k][~close] += parts[k]
    if not periodic:
        for k in range(4):
            N[k][:, Nx] = 0
            N[k][Ny, :] = 0
    if periodic:                       # the image sums end one cell sooner on one side: make N(-r) = N(r) exactly
        iy, ix = (-np.arange(Ny)) % Ny, (-np.arange(Nx)) % Nx
        N = [0.5 * (n + n[np.ix_(iy, ix)]) for n in N]
    K = [np.fft.rfft2(n) for n in N]
    if periodic:                       # the k = 0 term of an infinite film, exactly: Nxx = Nyy = Nxy = 0, Nzz = 1
        for k, v in enumerate((0.0, 0.0, 1.0, 0.0)):
            K[k][0, 0] = v
    return K


class Demag:
    def __init__(self, Nx, Ny, dx, d, periodic):
        self.Nx, self.Ny, self.periodic = Nx, Ny, periodic
        self.K = kernel(Nx, Ny, dx, d, periodic)
        self.shape = (Ny, Nx) if periodic else (2 * Ny, 2 * Nx)

    def field(self, M):
        """M (3, Ny, Nx) in A/m -> H_demag (3, Ny, Nx)."""
        Mk = [np.fft.rfft2(c, s=self.shape) for c in M]
        Kxx, Kyy, Kzz, Kxy = self.K
        Hk = [-(Kxx * Mk[0] + Kxy * Mk[1]), -(Kxy * Mk[0] + Kyy * Mk[1]), -Kzz * Mk[2]]
        return np.stack([np.fft.irfft2(h, s=self.shape)[:self.Ny, :self.Nx] for h in Hk])


def prism_Nz(a, b, c):
    """Demagnetising factor along c of a uniformly magnetised prism with sides a, b, c (Aharoni, JAP 83, 3432)."""
    a, b, c = a / 2, b / 2, c / 2
    r = np.sqrt(a * a + b * b + c * c)
    ab, bc, ac = np.sqrt(a * a + b * b), np.sqrt(b * b + c * c), np.sqrt(a * a + c * c)
    pD = ((b * b - c * c) / (2 * b * c) * np.log((r - a) / (r + a))
          + (a * a - c * c) / (2 * a * c) * np.log((r - b) / (r + b))
          + b / (2 * c) * np.log((ab + a) / (ab - a)) + a / (2 * c) * np.log((ab + b) / (ab - b))
          + c / (2 * a) * np.log((bc - b) / (bc + b)) + c / (2 * b) * np.log((ac - a) / (ac + a))
          + 2 * np.arctan(a * b / (c * r))
          + (a ** 3 + b ** 3 - 2 * c ** 3) / (3 * a * b * c)
          + (a * a + b * b - 2 * c * c) / (3 * a * b * c) * r
          + c / (a * b) * (ac + bc)
          - (ab ** 3 + bc ** 3 + ac ** 3) / (3 * a * b * c))
    return pD / np.pi
