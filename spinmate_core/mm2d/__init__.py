"""SpinMate's own 2D micromagnetic engine, built in (CPU, NumPy): skyrmions, skyrmionium, domain walls and uniform modes in a
thin ferromagnetic film one cell thick.

Input: SpinMate lines (--Nx=128 --D=3.5e-3 ...). mode=relax lowers the energy to the nearest stable texture;
mode=dynamics relaxes first (optional) and then solves the LLG equation in time, with an optional field pulse."""
import re
import sys

from ..engines.base import Validation

MU0 = 4e-7 * 3.141592653589793

# name: (default, kind, unit, description); a "choice" lists its values after the description
PARAMS = {
    "mode": ("relax", "choice", "", "relax = find the stable texture; dynamics = LLG in time", ("relax", "dynamics")),
    "lattice": ("ferro", "choice", "", "ferro = one magnetisation; afm = two antiparallel sublattices m1, m2 of an "
                "intrinsic, compensated antiferromagnet; saf = synthetic antiferromagnet, two ferromagnetic layers "
                "coupled through a spacer (RKKY)", ("ferro", "afm", "saf")),
    "J_afm": (2e7, "float", "J/m^3", "afm: homogeneous exchange J_afm m1.m2 between the sublattices"),
    "J_rkky": (-1e-3, "float", "J/m^2", "saf: interlayer coupling, energy -J_rkky m1.m2 per area (< 0 antiparallel)"),
    "texture": ("skyrmion", "choice", "", "initial state",
                ("skyrmion", "skyrmionium", "domain_wall", "uniform", "random")),
    "Nx": (128, "int", "", "cells along x"), "Ny": (128, "int", "", "cells along y"),
    "dx": (1e-9, "float", "m", "cell size (square cells)"), "thickness": (1e-9, "float", "m", "film thickness"),
    "Msat": (580e3, "float", "A/m", "saturation magnetisation"), "Aex": (15e-12, "float", "J/m", "exchange stiffness"),
    "Ku": (0.8e6, "float", "J/m^3", "perpendicular anisotropy (axis z)"),
    "D": (3.5e-3, "float", "J/m^2", "DMI constant"),
    "dmi": ("interfacial", "choice", "", "interfacial (Neel textures) or bulk (Bloch textures)", ("interfacial", "bulk")),
    "demag": ("local", "choice", "", "local = thin-film field -Ms mz, exact for a film a few nm thick; full = the "
              "long-range demagnetising field by FFT (Newell tensor), for thicker films, bubbles and stripe domains",
              ("local", "full")),
    "mask": ("", "mask", "", "shape of the film: disk, strip, racetrack, or the name of an uploaded PNG (dark, opaque "
             "pixels = film; stretched to Nx x Ny); empty = the whole grid. The outline is a free edge with the "
             "DMI boundary condition", None),
    "boundary": ("periodic", "choice", "", "periodic film or free edges", ("periodic", "free")),
    "B_ext": ((0.0, 0.0, 0.0), "vector", "T", "applied field"),
    "R0": (12e-9, "float", "m", "radius of the seed skyrmion (skyrmionium: inner ring)"),
    "m0": ((0.0, 0.0, 1.0), "vector", "", "direction for texture=uniform"),
    "seed": (0, "int", "", "random seed (texture=random, noise)"),
    "noise": (0.0, "float", "", "random tilt added to the seed (0-1): breaks its symmetry, so an unstable skyrmion "
              "can elongate into stripes (useful in phase diagrams)"),
    "start_from": ("", "run", "", "start from the final texture of an earlier mm2d run (its id, e.g. r0003) instead of "
                   "a seed; the grid and the lattice must match"),
    "tol": (10.0, "float", "A/m", "relaxation stops when max |m x H| is below this"),
    "max_iter": (100000, "int", "", "most relaxation iterations"),
    "relax_first": (1, "int", "", "dynamics: relax the initial state first (1) or not (0)"),
    "alpha": (0.01, "float", "", "Gilbert damping (dynamics)"),
    "t": (200e-12, "float", "s", "simulated time (dynamics)"),
    "table_dt": (1e-12, "float", "s", "output interval (dynamics)"),
    "B_pulse": ((0.0, 0.0, 0.0), "vector", "T", "extra uniform field during the first t_pulse seconds"),
    "t_pulse": (0.0, "float", "s", "length of the field pulse"),
    "rk_tol": (1e-7, "float", "", "error per step of the adaptive Runge-Kutta solver"),
    "excite": ("uniform", "choice", "", "where B_pulse acts: the whole film, a stripe antenna across y, or a dot",
               ("uniform", "stripe", "dot")),
    "excite_width": (4e-9, "float", "m", "width of the stripe or diameter of the dot"),
    "excite_x": (0.0, "float", "m", "centre of the antenna along x (0 = middle of the film)"),
    "pulse": ("step", "choice", "", "time shape of B_pulse: step, sine at f_rf, or sinc with cut-off f_rf "
              "(broadband, gives the dispersion)", ("step", "sine", "sinc")),
    "f_rf": (0.0, "float", "Hz", "frequency of the sine, or cut-off of the sinc"),
    "absorb": (0.0, "float", "m", "absorbing edges: the damping rises to 1 over this width at the x edges "
               "(and y edges for a dot), so spin waves do not come back"),
    "animation": (1, "int", "", "dynamics: also write animation.gif (and animation.mp4 when ffmpeg is installed) of "
                  "the spin waves or of the moving texture; 0 = no animation"),
    "backend": ("auto", "choice", "", "where the solver runs: auto (the GPU if there is one, else all CPU cores), "
                "gpu (NVIDIA, CUDA), cpu (numba, all cores) or numpy (the reference code, one core)",
                ("auto", "gpu", "cpu", "numpy")),
    "Temp": (0.0, "float", "K", "temperature (dynamics): stochastic thermal field, fixed Heun steps"),
    "thermal_dt": (0.0, "float", "s", "time step with temperature (0 = 0.15 / the fastest precession)"),
    "j": ((0.0, 0.0, 0.0), "vector", "A/m^2", "current density in the plane (dynamics): drives spin torques"),
    "stt_P": (0.0, "float", "", "spin polarisation of the current in the film (Zhang-Li STT); 0 = no STT"),
    "stt_beta": (0.0, "float", "", "non-adiabatic STT parameter beta"),
    "theta_SH": (0.0, "float", "", "spin Hall angle of the heavy metal under the film (damping-like SOT); 0 = no SOT"),
    "sot_sigma": ((0.0, 0.0, 0.0), "vector", "", "SOT spin polarisation; 0,0,0 = z x j (spin Hall effect)"),
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
            elif kind == "run":
                if not re.fullmatch(r"r\d{4,}", value):
                    raise ValueError
                vals[name] = value
            elif kind == "mask":
                if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value):
                    raise ValueError
                vals[name] = value
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
    if vals["pulse"] in ("sine", "sinc") and vals["f_rf"] <= 0:
        errors.append(f"pulse={vals['pulse']} needs f_rf > 0")
    if vals["lattice"] != "ferro" and vals["excite"] != "uniform":
        errors.append("spin waves from an antenna are for lattice=ferro only (for now)")
    if vals["demag"] == "full" and vals["lattice"] != "ferro":
        errors.append("demag=full is for lattice=ferro (an AFM has no stray field; an SAF uses the local field)")
    if vals["lattice"] == "afm" and vals["J_afm"] <= 0:
        errors.append("J_afm must be positive for an antiferromagnet")
    if vals["lattice"] == "saf" and vals["J_rkky"] >= 0:
        errors.append("J_rkky must be negative for a synthetic antiferromagnet (antiparallel layers)")
    if errors:
        return errors, warnings
    if vals["mode"] == "dynamics" and vals["pulse"] != "step" and vals["table_dt"] > 1 / (2.5 * vals["f_rf"]):
        warnings.append(f"table_dt is too coarse for f_rf = {vals['f_rf'] / 1e9:.3g} GHz: use at most "
                        f"{1 / (2.5 * vals['f_rf']):.2g} s")
    if vals["mask"]:
        err, warn = mask_problems(vals)
        errors += err; warnings += warn
    if vals["start_from"]:
        err = start_problem(vals)
        if err:
            errors.append(err)
    if vals["Temp"] < 0:
        errors.append("Temp must be zero or positive")
    if vals["Temp"] > 0 and vals["mode"] != "dynamics":
        warnings.append("relaxation finds the 0 K state, so Temp has no effect in mode=relax: for a run at this "
                        "temperature use mode=dynamics (it relaxes at 0 K first, then evolves with the thermal field)")
    if vals["Temp"] > 0 and vals["mode"] == "dynamics":
        warnings.append("with temperature the solver takes fixed Heun steps: slower than at 0 K")
    if any(vals["j"]) and vals["mode"] != "dynamics":
        warnings.append("a current acts only in mode=dynamics")
    if any(vals["j"]) and not (vals["stt_P"] or vals["theta_SH"]):
        warnings.append("j is set but stt_P and theta_SH are 0: the current exerts no torque")
    if (vals["stt_P"] or vals["theta_SH"]) and not any(vals["j"]):
        warnings.append("stt_P or theta_SH is set but j = 0: no current flows")
    l_ex0 = (2 * vals["Aex"] / (MU0 * vals["Msat"] ** 2)) ** 0.5
    if vals["demag"] == "local" and vals["lattice"] == "ferro" and vals["thickness"] > 2 * l_ex0:
        warnings.append(f"the film is {vals['thickness'] * 1e9:.3g} nm thick, above twice the exchange length: the "
                        "local demag misses the stray field of the domains; use demag=full for quantitative sizes")
    if vals["demag"] == "full" and vals["excite"] != "uniform":
        warnings.append("with demag=full the spin-wave theory line (local demag) is only a guide")
    if vals["excite"] != "uniform" and vals["absorb"] == 0 and vals["boundary"] == "periodic":
        warnings.append("spin waves will wrap around the periodic film: set absorb, for example to a fifth of the film")
    Ms, A, Ku, D, dx = vals["Msat"], vals["Aex"], vals["Ku"], vals["D"], vals["dx"]
    l_ex = (2 * A / (MU0 * Ms ** 2)) ** 0.5
    Keff = Ku if vals["lattice"] == "afm" else Ku - 0.5 * MU0 * Ms ** 2     # no demag in a compensated AFM (an SAF keeps it)
    if dx > l_ex and vals["lattice"] != "afm":
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
    from .. import fastmag
    backend, note = fastmag.choose(vals["backend"], needs_fft=vals["demag"] == "full" and vals["lattice"] == "ferro")
    if note:
        warnings.append(f"backend={vals['backend']}: runs on {backend} ({note})")
    if vals["mode"] == "dynamics":
        steps, _ = time_steps(vals)
        est = estimate_s(vals)
        if est > 1800:
            warnings.append(f"about {steps:,.0f} time steps of {vals['t'] / steps * 1e15:.0f} fs (the exchange of "
                            f"{vals['dx'] * 1e9:.3g} nm cells sets the step) on {backend}: about {est / 3600:.1f} h. "
                            "Larger cells (the step grows as dx²), a smaller film or a GPU make it faster")
    return errors, warnings


def mask_folder():
    from ..simulations import MASKS
    return MASKS


def mask_problems(vals):
    """Errors and warnings about the mask: missing file, aspect ratio, seed outside the film."""
    from . import geometry
    name, Nx, Ny = vals["mask"], vals["Nx"], vals["Ny"]
    errors, warnings = [], []
    if name not in geometry.BUILT_IN:
        path = mask_folder() / name
        if not name.lower().endswith(".png") or not path.exists():
            return [f"mask={name}: no uploaded PNG with that name (or use disk, strip, racetrack)"], []
        w, h = geometry.png_size(path)
        if abs((w / h) / (Nx / Ny) - 1) > 0.05:
            warnings.append(f"mask {name} is {w} x {h} pixels but the grid is {Nx} x {Ny} cells: the shape is "
                            f"stretched; use Nx:Ny = {w}:{h}")
    try:
        shape = geometry.load(name, Nx, Ny, mask_folder())
    except Exception as e:                            # a broken picture
        return [f"mask={name}: {e}"], []
    if shape.sum() < 16:
        errors.append(f"mask={name} leaves {int(shape.sum())} cells of film: draw the film dark on a white background")
    elif vals["texture"] in ("skyrmion", "skyrmionium") and not shape[Ny // 2, Nx // 2]:
        warnings.append("the seed sits in the middle of the grid, which is outside the shape of the mask")
    if vals["boundary"] == "periodic" and (shape[0].any() or shape[-1].any() or shape[:, 0].any() or shape[:, -1].any()):
        warnings.append("the shape touches the edge of the grid and boundary=periodic joins it to the other side")
    return errors, warnings


def start_file(run_id):
    from ..simulations import RUNS
    return RUNS / run_id / "m_final.npz"


def start_problem(vals):
    """Why the run in start_from cannot be used, or ''."""
    f = start_file(vals["start_from"])
    if not f.exists():
        return f"start_from={vals['start_from']}: no finished mm2d run with that id"
    import numpy as np
    shape = np.load(f)["m"].shape
    want = (3, 2, vals["Ny"], vals["Nx"]) if vals["lattice"] != "ferro" else (3, vals["Ny"], vals["Nx"])
    if shape != want:
        return (f"start_from={vals['start_from']}: its texture has shape {shape}, this run needs {want} "
                "(use the same Nx, Ny and lattice)")
    return ""


def max_freq(vals):
    """The fastest precession on the grid (rad/s), as Film.max_freq: it sets the time step."""
    Ms, A, dx = vals["Msat"], vals["Aex"], vals["dx"]
    B = max(abs(float(c)) for c in vals["B_ext"]) + max(abs(float(c)) for c in vals["B_pulse"])
    h = 8 * A / (MU0 * Ms * dx ** 2) + 2 * abs(vals["Ku"]) / (MU0 * Ms) + Ms + 4 * abs(vals["D"]) / (MU0 * Ms * dx) + B / MU0
    if vals["lattice"] != "ferro":
        h += (vals["J_afm"] if vals["lattice"] == "afm" else -vals["J_rkky"] / vals["thickness"]) / (MU0 * Ms)
    return 2.211e5 * h


def time_steps(vals):
    """About how many time steps a dynamics run takes: adaptive Dormand-Prince settles near dt = 1.46 / w_max
    (the exchange of the smallest cells sets w_max); with temperature, fixed Heun steps of 0.15 / w_max."""
    w = max_freq(vals)
    if vals["Temp"] > 0:
        dt = vals["thermal_dt"] or 0.15 / w
        return vals["t"] / dt, 2
    return vals["t"] * w / 1.46, 7


def estimate_s(vals):
    """Rough run time: the numpy code costs about 0.2 µs per cell and relaxation iteration and 130 ns per cell and
    field evaluation; the cpu and gpu backends divide that by their speed-up."""
    cells = vals["Nx"] * vals["Ny"]
    relax = 0.2e-6 * cells * 3000 if (vals["mode"] == "relax" or vals["relax_first"]) else 0
    dyn = 0.0
    if vals["mode"] == "dynamics":
        steps, evals = time_steps(vals)
        dyn = steps * evals * 130e-9 * cells
    if vals["demag"] == "full":                       # FFTs per field, and slower relaxation of long-range textures
        f = 2.0 if vals["boundary"] == "periodic" else 4.0
        relax, dyn = 1.5 * f * relax, f * dyn
        relax += 2e-8 * cells * (17 ** 2 if vals["boundary"] == "periodic" else 1)     # the tensor, once
    if vals["lattice"] != "ferro":                    # two sublattices (the faster precession is in max_freq)
        relax, dyn = 2 * relax, 2 * dyn
    from .. import fastmag
    backend, _ = fastmag.choose(vals["backend"], needs_fft=vals["demag"] == "full" and vals["lattice"] == "ferro")
    k = fastmag.speedup(backend, cells * (2 if vals["lattice"] != "ferro" else 1))
    relax, dyn = relax / k, dyn / k
    if backend != "numpy":
        relax += 3.0                                  # loading the compiled kernels
    if vals["mode"] == "dynamics" and vals["animation"] and (vals["excite"] != "uniform" or any(vals["j"])
                                                             or vals["Temp"] > 0):
        dyn += 0.4 * 65                               # drawing about 65 frames
    return round(relax + dyn, 1)


class MM2DEngine:
    name = "mm2d"
    title = "2D micromagnetics: skyrmions (built in)"
    description = ("SpinMate's own finite-difference solver for a thin film one cell thick: exchange, perpendicular "
                   "anisotropy, interfacial or bulk DMI, Zeeman and the demagnetising field: local thin-film, or long-range by FFT (demag=full) "
                   "for thicker films, bubbles and stripe domains. "
                   "Finds skyrmions, skyrmionium and domain walls, in a ferromagnet or a two-sublattice antiferromagnet (lattice=afm) (mode=relax) or runs LLG dynamics with a field "
                   "pulse, uniform or from an antenna for spin waves, or with a current that moves skyrmions by spin-transfer or spin-orbit torque (mode=dynamics). Runs on all CPU cores or an NVIDIA GPU (backend).")

    example = "--mode=relax\n--texture=skyrmion\n--D=3.5e-3\n--Nx=128\n--Ny=128"

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
        steps = int(round(vals["t"] / vals["table_dt"])) if vals["mode"] == "dynamics" else vals["max_iter"]
        return Validation(ok=True, steps=steps, estimated_runtime_s=estimate_s(vals), changed_from_defaults=changed,
                          warnings=warnings, extra={"cleaned_text": (parameters_text or "").strip() + "\n"})

    def run_command(self):
        return [sys.executable, "-m", "spinmate_core.mm2d.runner"]

    def outputs(self):
        return {"texture_3d.png": "tilted view of the final texture with 3D arrows",
                "texture_2d.png": "top view of the final texture with nm axes",
                "texture_3d_2d.png": "both views side by side",
                "skyrmion_track.png": "current-driven motion: the path of the texture centre, velocity and Hall angle",
                "spinwave_3d_2d.png": "spin waves (excite = stripe or dot): 3D arrows and top view, colour = phase",
                "spinwave_map.png": "spin waves: the change of mx as a tilted surface and as a map",
                "spinwave_profile.png": "spin waves: the change of mx along y = 0",
                "dispersion.png": "pulse = sinc: f(k) from the simulation with the analytic curve",
                "spinwave.npz": "mx along the centre row in time, and the last mx map",
                "m_final.npz": "final m (3, Ny, Nx) and the x, y cell centres in m",
                "output1.dat": "relax: iteration and <m>; dynamics: time and <m>",
                "energy.dat": "relax: iteration, energy (J), max torque (A/m); dynamics: time, energy",
                "summary.json": "topological charge, radius, rings, energies", "Two_Spin_Dynamics.png": "plot of <m>",
                "parameters.txt": "the input"}
