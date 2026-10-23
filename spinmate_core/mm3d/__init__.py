"""SpinMate's own 3D micromagnetic engine, built in (CPU, NumPy): hopfions (H = 1, 2), skyrmion tubes, chiral bobbers
and torons in a chiral magnet slab or nanodisk, with Bloch points found and marked.

Input: SpinMate lines (--texture=hopfion --D=5.24e-3 ...). The solver relaxes the seed to the nearest stable
texture and reports the Hopf index H."""
import re
import sys

from ..engines.base import Validation

MU0 = 4e-7 * 3.141592653589793
PI = 3.141592653589793

# name: (default, kind, unit, description[, choices])
PARAMS = {
    "texture": ("hopfion", "choice", "", "initial state: hopfion (H = 1), hopfion2 (H = 2: two hopfions stacked in a disk "
                "about 2p thick), skyrmion_tube, "
                "chiral_bobber (a tube from the top surface ending at a Bloch point; surfaces=free), toron (a tube "
                "closed by two Bloch points; surfaces=pinned), uniform, random",
                ("hopfion", "hopfion2", "skyrmion_tube", "chiral_bobber", "toron", "uniform", "random")),
    "Nx": (58, "int", "", "cells along x"), "Ny": (58, "int", "", "cells along y"),
    "Nz": (24, "int", "", "cells along z (the thickness)"),
    "dx": (1e-9, "float", "m", "cell size (cubic cells)"),
    "Msat": (500e3, "float", "A/m", "saturation magnetisation"), "Aex": (10e-12, "float", "J/m", "exchange stiffness"),
    "Ku": (0.0, "float", "J/m^3", "bulk uniaxial anisotropy (axis z)"),
    "D": (5.236e-3, "float", "J/m^2", "DMI constant; the helical period is p = 4πA/D"),
    "dmi": ("bulk", "choice", "", "bulk (chiral magnet, hopfions) or interfacial", ("bulk", "interfacial")),
    "geometry": ("disk", "choice", "", "a nanodisk with free side walls, or a slab periodic in x and y",
                 ("disk", "slab")),
    "disk_diameter": (54e-9, "float", "m", "diameter of the nanodisk"),
    "surfaces": ("pinned", "choice", "", "top and bottom surfaces: pinned along +z (strong surface anisotropy), "
                 "free, or periodic", ("pinned", "free", "periodic")),
    "B_ext": ((0.0, 0.0, 0.3), "vector", "T", "applied field"),
    "R0": (0.0, "float", "m", "radius of the seed torus or tube (0 = from the helical period)"),
    "seed": (0, "int", "", "random seed (texture=random)"),
    "animation": (1, "int", "", "also write rotate.gif (and rotate.mp4 when ffmpeg is installed), the 3D view "
                  "turning once around z; 0 = no animation"),
    "backend": ("auto", "choice", "", "where the solver runs: auto (the GPU if there is one, else all CPU cores), "
                "gpu (NVIDIA, CUDA), cpu (numba, all cores) or numpy (the reference code, one core)",
                ("auto", "gpu", "cpu", "numpy")),
    "tol": (5.0, "float", "A/m", "relaxation stops when max |m x H| is below this"),
    "max_iter": (40000, "int", "", "most relaxation iterations"),
}
EXTRA = {"Ms": "Msat", "A": "Aex", "Ku1": "Ku", "Dbulk": "D", "Dind": "D"}


def parse(text):
    vals = {k: v[0] for k, v in PARAMS.items()}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"--(\w+)=(\S+)", line)
        if not m:
            raise ValueError(f"cannot read line {raw.strip()!r}: write --name=value")
        name, value = m.groups()
        if name == "Dind":
            vals["dmi"] = "interfacial"
        name = EXTRA.get(name, name)
        if name not in PARAMS:
            raise ValueError(f"unknown parameter {name}; known: {', '.join(PARAMS)}")
        kind = PARAMS[name][1]
        try:
            if kind == "vector":
                v = tuple(float(x) for x in value.split(","))
                if len(v) != 3:
                    raise ValueError
                vals[name] = v
            elif kind == "choice":
                if value not in PARAMS[name][4]:
                    raise ValueError
                vals[name] = value
            elif kind == "int":
                f = float(value)
                if not f.is_integer():
                    raise ValueError
                vals[name] = int(f)
            else:
                vals[name] = float(value)
        except ValueError:
            allowed = f" (one of {', '.join(PARAMS[name][4])})" if kind == "choice" else ""
            raise ValueError(f"{name}={value} is not a valid {kind}{allowed}") from None
    return vals


def check(vals):
    errors, warnings = [], []
    for k in ("Nx", "Ny", "Nz"):
        if not 2 <= vals[k] <= 512:
            errors.append(f"{k} must be between 2 and 512")
    for k in ("dx", "Msat", "Aex"):
        if vals[k] <= 0:
            errors.append(f"{k} must be positive")
    cells = vals["Nx"] * vals["Ny"] * vals["Nz"]
    if cells > 2_000_000:
        errors.append(f"{cells:,} cells: too many for the CPU solver (at most 2,000,000)")
    if errors:
        return errors, warnings
    A, D, dx = vals["Aex"], vals["D"], vals["dx"]
    if D:
        p = 4 * PI * A / abs(D)
        if dx > p / 12:
            warnings.append(f"cell {dx * 1e9:.2g} nm is coarse for the helical period {p * 1e9:.3g} nm "
                            "(use at least 12 cells per period)")
        d = vals["Nz"] * dx
        BD = D ** 2 / (A * vals["Msat"])             # field unit of a chiral magnet; the cone saturates at BD/2
        Bz = vals["B_ext"][2]
        if vals["texture"] == "chiral_bobber":
            if vals["surfaces"] != "free":
                warnings.append("a chiral bobber lives at a free surface: set surfaces=free")
            if not 0.35 * BD <= Bz <= 0.5 * BD:
                warnings.append(f"chiral bobbers are metastable for B_z between about 0.35 and 0.5 B_D, here "
                                f"{0.35 * BD:.2g}-{0.5 * BD:.2g} T (B_D = D²/(A Ms) = {BD:.3g} T); below, the Bloch point "
                                "leaves and a tube or nothing remains")
            if d < 1.0 * p:
                warnings.append(f"the film is {d / p:.2f} p thick: a bobber needs at least about one period")
        if vals["texture"] == "toron" and vals["surfaces"] != "pinned":
            warnings.append("a toron needs the top and bottom surfaces pinned along +z")
        if vals["texture"] == "hopfion" and not 0.7 <= d / p <= 1.4:
            warnings.append(f"thickness {d * 1e9:.3g} nm is {d / p:.2f} p: hopfions are stable for a thickness "
                            "close to the helical period")
        if vals["texture"] == "hopfion2" and not 1.9 <= d / p <= 2.4:
            warnings.append(f"thickness {d * 1e9:.3g} nm is {d / p:.2f} p: hopfion2 stacks two hopfions, one per "
                            "period; use a thickness of about 2p")
        if vals["texture"] in ("hopfion", "hopfion2") and vals["geometry"] == "slab":
            warnings.append("in a periodic slab a hopfion tends to unwind or to grow into helices; a nanodisk "
                            "about 2p across binds it")
        if vals["texture"] in ("hopfion", "hopfion2") and vals["surfaces"] != "pinned":
            warnings.append("hopfions need the top and bottom surfaces pinned along +z")
    elif vals["texture"] == "hopfion":
        warnings.append("D = 0: nothing stabilises a hopfion, it will collapse")
    if vals["geometry"] == "disk" and vals["disk_diameter"] > min(vals["Nx"], vals["Ny"]) * dx:
        errors.append("the disk is wider than the grid: raise Nx and Ny or lower disk_diameter")
    from .. import fastmag
    backend, note = fastmag.choose(vals["backend"])
    if note:
        warnings.append(f"backend={vals['backend']}: runs on {backend} ({note})")
    if cells > 300_000 and backend != "gpu":
        warnings.append(f"{cells:,} cells on the CPU: this relaxation can take many minutes")
    return errors, warnings


def estimate_s(vals):
    """About 0.35 µs per cell and iteration, some 2,500 iterations."""
    from .. import fastmag
    cells = vals["Nx"] * vals["Ny"] * vals["Nz"]
    backend, _ = fastmag.choose(vals["backend"])
    relax = 0.35e-6 * cells * 2500 / fastmag.speedup(backend, cells) + (3.0 if backend != "numpy" else 0.0)
    return round(relax + (30 if vals["animation"] else 0), 1)


class MM3DEngine:
    name = "mm3d"
    title = "3D micromagnetics: hopfions (built in)"
    description = ("SpinMate's own 3D finite-difference solver for a chiral magnet slab or nanodisk: exchange, "
                   "bulk or interfacial DMI, anisotropy and Zeeman, with the top and bottom surfaces pinned along +z. "
                   "Relaxes hopfions (H = 1, 2), skyrmion tubes, chiral bobbers and torons, reports the Hopf "
                   "index, the skyrmion number of each layer and the Bloch points (found from the solid angle of m). Runs on all CPU cores or an NVIDIA GPU (backend).")
    example = "--texture=hopfion\n--D=5.236e-3\n--Nz=24\n--disk_diameter=54e-9"

    def ready(self):
        from .. import fastmag
        return True, fastmag.describe()

    def parameter_schema(self):
        out = {}
        for k, v in PARAMS.items():
            out[k] = {"default": v[0], "kind": v[1], "unit": v[2], "description": v[3]}
            if v[1] == "choice":
                out[k]["choices"] = list(v[4])
        return out

    def validate(self, parameters_text):
        try:
            vals = parse(parameters_text)
        except ValueError as e:
            return Validation(ok=False, error=str(e))
        errors, warnings = check(vals)
        if errors:
            return Validation(ok=False, error="; ".join(errors))
        changed = {k: v for k, v in vals.items() if v != PARAMS[k][0]}
        return Validation(ok=True, steps=vals["max_iter"], estimated_runtime_s=estimate_s(vals),
                          changed_from_defaults=changed, warnings=warnings,
                          extra={"cleaned_text": (parameters_text or "").strip() + "\n"})

    def run_command(self):
        return [sys.executable, "-m", "spinmate_core.mm3d.runner"]

    def outputs(self):
        return {"texture_3d_2d.png": "the m_z = 0 surface in 3D and the cuts y = 0 and z = 0",
                "texture_3d.png": "the m_z = 0 surface in 3D", "texture_2d.png": "the two cuts",
                "m_final.npz": "final m (3, Nz, Ny, Nx) and the cell centres x, y, z in m",
                "output1.dat": "relaxation iteration and <m>", "energy.dat": "iteration, energy (J), max torque",
                "summary.json": "Hopf index, rings, energies", "Two_Spin_Dynamics.png": "<m> during the relaxation",
                "parameters.txt": "the input"}
