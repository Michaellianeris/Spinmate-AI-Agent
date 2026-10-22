"""The shape of the film: a built-in outline (disk, strip, racetrack) or a PNG mask.

In a PNG the film is where the picture is dark and opaque; white or transparent pixels are empty. The picture is
stretched to Nx x Ny cells (nearest pixel), with its top row at +y."""
from pathlib import Path

import numpy as np

BUILT_IN = ("disk", "strip", "racetrack")


def built_in(name, Nx, Ny):
    """Shapes that fill the grid: a disk, a strip along x half as wide as the grid, a racetrack (a strip with
    round ends)."""
    x = (np.arange(Nx) - Nx / 2 + 0.5)
    y = (np.arange(Ny) - Ny / 2 + 0.5)
    X, Y = np.meshgrid(x, y)
    if name == "disk":
        return np.hypot(X, Y) <= min(Nx, Ny) / 2 - 0.5
    if name == "strip":
        return np.abs(Y) <= Ny / 4
    if name == "racetrack":
        r = min(Ny / 4, Nx / 4)
        a = Nx / 2 - 1 - r                          # centres of the two round ends
        return (np.abs(Y) <= r) & (np.abs(X) <= a) | (np.hypot(np.abs(X) - a, Y) <= r)
    raise ValueError(f"unknown shape {name!r}; built in: {', '.join(BUILT_IN)}")


def from_png(path, Nx, Ny):
    from PIL import Image
    im = Image.open(path).convert("RGBA").resize((Nx, Ny), Image.NEAREST)
    a = np.asarray(im, float) / 255
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    return ((lum < 0.5) & (a[..., 3] > 0.5))[::-1]            # image rows run downwards, y runs upwards


def png_size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def load(name, Nx, Ny, folder):
    """The mask for the parameter value `name`: a built-in shape or a PNG in `folder` (or the run folder)."""
    if name in BUILT_IN:
        return built_in(name, Nx, Ny)
    for p in (Path(name), Path(folder) / name):
        if p.exists():
            return from_png(p, Nx, Ny)
    raise FileNotFoundError(f"no mask {name}")


def make_examples(folder, size=(256, 128)):
    """The built-in shapes as black-on-white PNGs, to edit or to upload as they are."""
    from PIL import Image
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    for name in BUILT_IN:
        m = built_in(name, *size)
        Image.fromarray(np.where(m[::-1], 0, 255).astype(np.uint8)).save(folder / f"{name}.png")
