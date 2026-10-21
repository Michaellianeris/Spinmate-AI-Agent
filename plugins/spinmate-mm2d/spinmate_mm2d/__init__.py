"""SpinMate's own 2D micromagnetic engine (CPU, NumPy): skyrmions, skyrmionium, domain walls and uniform modes in a
thin ferromagnetic film one cell thick.

Input: SpinMate lines (--Nx=128 --D=3.5e-3 ...). mode=relax lowers the energy to the nearest stable texture;
mode=dynamics relaxes first (optional) and then solves the LLG equation in time, with an optional field pulse."""
import re
import sys

from spinmate_core.engines import Validation

__version__ = "0.1.0"
MU0 = 4e-7 * 3.141592653589793

# name: (default, kind, unit, description); a "choice" lists its values after the description
PARAMS = {
    "mode": ("relax", "choice", "", "relax = find the stable texture; dynamics = LLG in time", ("relax", "dynamics")),
    "texture": ("skyrmion", "choice", "", "initial state",
                ("skyrmion", "skyrmionium", "domain_wall", "uniform", "random")),
    "Nx": (128, "int", "", "cells along x"), "Ny": (128, "int", "", "cells along y"),
    "dx": (1e-9, "float", "m", "cell size (square cells)"), "thickness": (1e-9, "float", "m", "film thickness"),
    "Msat": (580e3, "float", "A/m", "saturation magnetisation"), "Aex": (15e-12, "float", "J/m", "exchange stiffness"),
    "Ku": (0.8e6, "float", "J/m^3", "perpendicular anisotropy (axis z)"),
    "D": (3.5e-3, "float", "J/m^2", "DMI constant"),
    "dmi": ("interfacial", "choice", "", "interfacial (Neel textures) or bulk (Bloch textures)", ("interfacial", "bulk")),
    "boundary": ("periodic", "choice", "", "periodic film or free edges", ("periodic", "free")),
    "B_ext": ((0.0, 0.0, 0.0), "vector", "T", "applied field"),
    "R0": (12e-9, "float", "m", "radius of the seed skyrmion (skyrmionium: inner ring)"),
    "m0": ((0.0, 0.0, 1.0), "vector", "", "direction for texture=uniform"),
    "seed": (0, "int", "", "random seed (texture=random)"),
    "tol": (10.0, "float", "A/m", "relaxation stops when max |m x H| is below this"),
    "max_iter": (100000, "int", "", "most relaxation iterations"),
    "relax_first": (1, "int", "", "dynamics: relax the initial state first (1) or not (0)"),
    "alpha": (0.01, "float", "", "Gilbert damping (dynamics)"),
    "t": (200e-12, "float", "s", "simulated time (dynamics)"),
    "table_dt": (1e-12, "float", "s", "output interval (dynamics)"),
    "B_pulse": ((0.0, 0.0, 0.0), "vector", "T", "extra uniform field during the first t_pulse seconds"),
    "t_pulse": (0.0, "float", "s", "length of the field pulse"),
    "rk_tol": (1e-5, "float", "", "error per step of the adaptive Runge-Kutta solver"),
}
EXTRA = {"Ms": "Msat", "A": "Aex", "Ku1": "Ku", "Dind": "D", "Dbulk": "D", "d": "thickness"}   # mumax-like names


def parse(text):
    """SpinMate lines -> values; raises ValueError on unknown names or bad values."""
    vals = {k: v[0] for k, v in PARAMS.items()}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"--(\w+)=(\S+)", line)
        if not m:
            raise ValueError(f"cannot read line {raw.strip()!r}: write --name=value")
        name, value = m.groups()
        if name in ("Dbulk",):
            vals["dmi"] = "bulk"
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
    """Errors that stop the run, and warnings about the physics or the cost."""
    errors, warnings = [], []
    for k in ("Nx", "Ny"):
        if not 4 <= vals[k] <= 2048:
            errors.append(f"{k} must be between 4 and 2048")
    for k in ("dx", "thickness", "Msat", "Aex"):
        if vals[k] <= 0:
            errors.append(f"{k} must be positive")
    if vals["mode"] == "dynamics" and (vals["t"] <= 0 or vals["table_dt"] <= 0):
        errors.append("t and table_dt must be positive")
    if errors:
        return errors, warnings
    Ms, A, Ku, D, dx = vals["Msat"], vals["Aex"], vals["Ku"], vals["D"], vals["dx"]
    l_ex = (2 * A / (MU0 * Ms ** 2)) ** 0.5
    Keff = Ku - 0.5 * MU0 * Ms ** 2
    if dx > l_ex:
        warnings.append(f"cell {dx * 1e9:.2g} nm is larger than the exchange length {l_ex * 1e9:.2g} nm")
    if Keff > 0 and dx > (A / Keff) ** 0.5 / 2:
        warnings.append(f"cell {dx * 1e9:.2g} nm is coarse for the wall width {(A / Keff) ** 0.5 * 1e9:.2g} nm")
    if D and dx > 2 * A / abs(D) / 4:
        warnings.append(f"cell {dx * 1e9:.2g} nm is coarse for the DMI length 2A/|D| = {2 * A / abs(D) * 1e9:.2g} nm")
    if Keff > 0 and abs(D) > 4 * (A * Keff) ** 0.5 / 3.141592653589793:
        warnings.append(f"|D| is above the critical value {4 * (A * Keff) ** 0.5 / 3.141592653589793 * 1e3:.3g} mJ/m²: "
                        "the uniform state is unstable and the film breaks into stripes")
    if Keff <= 0 and vals["texture"] in ("skyrmion", "skyrmionium", "domain_wall"):
        warnings.append("Ku below ½μ0Ms² (in-plane film): perpendicular textures are not stable here")
    if vals["texture"] in ("skyrmion", "skyrmionium"):
        R_out = vals["R0"] * (2.5 if vals["texture"] == "skyrmionium" else 1)
        if 2 * R_out > 0.8 * min(vals["Nx"], vals["Ny"]) * dx:
            warnings.append("the seed texture almost fills the film: make the film larger or R0 smaller")
    cells = vals["Nx"] * vals["Ny"]
    if cells > 512 * 512:
        warnings.append(f"{cells:,} cells on the CPU: this run will be slow")
    return errors, warnings


def estimate_s(vals):
    """Rough CPU time: about 0.2 µs per cell and relaxation iteration, 50 µs per cell and picosecond of dynamics."""
    cells = vals["Nx"] * vals["Ny"]
    relax = 0.2e-6 * cells * 3000 if (vals["mode"] == "relax" or vals["relax_first"]) else 0
    dyn = 50e-6 * cells * vals["t"] / 1e-12 if vals["mode"] == "dynamics" else 0
    return round(relax + dyn, 1)


class MM2DEngine:
    name = "mm2d"
    title = "SpinMate 2D micromagnetics (CPU)"
    description = ("SpinMate's own finite-difference solver for a thin film one cell thick: exchange, perpendicular "
                   "anisotropy, interfacial or bulk DMI, Zeeman and the local thin-film demagnetising field. "
                   "Finds skyrmions, skyrmionium and domain walls (mode=relax) or runs LLG dynamics with a field "
                   "pulse (mode=dynamics). Runs on any computer, no GPU needed.")

    example = "--mode=relax\n--texture=skyrmion\n--D=3.5e-3\n--Nx=128\n--Ny=128"

    def ready(self):
        return True, ""

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
        steps = int(round(vals["t"] / vals["table_dt"])) if vals["mode"] == "dynamics" else vals["max_iter"]
        return Validation(ok=True, steps=steps, estimated_runtime_s=estimate_s(vals), changed_from_defaults=changed,
                          warnings=warnings, extra={"cleaned_text": (parameters_text or "").strip() + "\n"})

    def run_command(self):
        return [sys.executable, "-m", "spinmate_mm2d.runner"]

    def outputs(self):
        return {"texture_3d.png": "tilted view of the final texture with 3D arrows",
                "texture_2d.png": "top view of the final texture with nm axes",
                "texture_3d_2d.png": "both views side by side",
                "m_final.npz": "final m (3, Ny, Nx) and the x, y cell centres in m",
                "output1.dat": "relax: iteration and <m>; dynamics: time and <m>",
                "energy.dat": "relax: iteration, energy (J), max torque (A/m); dynamics: time, energy",
                "summary.json": "topological charge, radius, rings, energies", "Two_Spin_Dynamics.png": "plot of <m>",
                "parameters.txt": "the input"}
