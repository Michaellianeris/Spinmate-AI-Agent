"""mumax3 (GPU micromagnetics, https://mumax.github.io) as a SpinMate engine.

The input is either SpinMate parameter lines (--Nx=64 --Msat=800e3 ...) that are turned into an .mx3 script,
or a complete .mx3 script. mumax3 itself must be installed (it needs an NVIDIA GPU); set MUMAX3_BIN when it is
not on the PATH."""
import os
import re
import shutil
import sys

from spinmate_core.engines import Validation

__version__ = "0.1.0"

# name: (default, kind, unit, description)
PARAMS = {
    "Nx": (64, "int", "", "cells along x"), "Ny": (64, "int", "", "cells along y"), "Nz": (1, "int", "", "cells along z"),
    "dx": (2e-9, "float", "m", "cell size x"), "dy": (2e-9, "float", "m", "cell size y"), "dz": (1e-9, "float", "m", "cell size z"),
    "Msat": (800e3, "float", "A/m", "saturation magnetisation"), "Aex": (13e-12, "float", "J/m", "exchange stiffness"),
    "Ku1": (0.0, "float", "J/m^3", "uniaxial anisotropy"), "anisU": ((0.0, 0.0, 1.0), "vector", "", "anisotropy axis"),
    "alpha": (0.02, "float", "", "Gilbert damping"), "B_ext": ((0.0, 0.0, 0.0), "vector", "T", "applied field"),
    "m": ((1.0, 0.0, 0.1), "vector", "", "initial uniform magnetisation"), "Temp": (0.0, "float", "K", "temperature"),
    "t": (1e-9, "float", "s", "simulated time"), "table_dt": (1e-12, "float", "s", "output interval"),
}
MX3_MARKERS = re.compile(r"\b(SetGridSize|SetCellSize|Run|RunWhile|Relax|Minimize|TableAutoSave)\s*\(", re.I)


def is_script(text):
    return bool(MX3_MARKERS.search(text or ""))


def parse(text):
    """SpinMate lines -> values; raises ValueError on unknown names or bad numbers."""
    vals = {k: v[0] for k, v in PARAMS.items()}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"--(\w+)=(\S+)", line)
        if not m:
            raise ValueError(f"cannot read line {raw.strip()!r}: write --name=value")
        name, value = m.groups()
        if name not in PARAMS:
            raise ValueError(f"unknown parameter {name}; known: {', '.join(PARAMS)}")
        kind = PARAMS[name][1]
        try:
            if kind == "vector":
                v = tuple(float(x) for x in value.split(","))
                if len(v) != 3:
                    raise ValueError
                vals[name] = v
            else:
                vals[name] = int(value) if kind == "int" else float(value)
        except ValueError:
            raise ValueError(f"{name}={value} is not a valid {kind}") from None
    return vals


def to_mx3(vals):
    v3 = lambda v: f"vector({v[0]!r}, {v[1]!r}, {v[2]!r})"
    lines = [
        "// written by SpinMate from parameters.txt",
        f"SetGridSize({vals['Nx']}, {vals['Ny']}, {vals['Nz']})",
        f"SetCellSize({vals['dx']!r}, {vals['dy']!r}, {vals['dz']!r})",
        f"Msat = {vals['Msat']!r}", f"Aex = {vals['Aex']!r}", f"Ku1 = {vals['Ku1']!r}", f"AnisU = {v3(vals['anisU'])}",
        f"alpha = {vals['alpha']!r}", f"B_ext = {v3(vals['B_ext'])}", f"m = Uniform{v3(vals['m'])[6:]}",
    ]
    if vals["Temp"] > 0:
        lines += [f"Temp = {vals['Temp']!r}", "FixDt = 1e-14"]
    lines += [f"TableAutoSave({vals['table_dt']!r})", f"Run({vals['t']!r})"]
    return "\n".join(lines) + "\n"


def script(text):
    """The .mx3 script to run: the text itself when it is a script (with a table added), else built from lines."""
    if is_script(text):
        return text if re.search(r"TableAutoSave\s*\(", text, re.I) else "TableAutoSave(1e-12)\n" + text
    return to_mx3(parse(text))


def binary():
    return os.environ.get("MUMAX3_BIN") or shutil.which("mumax3")


class Mumax3Engine:
    name = "mumax3"
    title = "mumax3 micromagnetics (GPU)"
    description = ("Finite-difference micromagnetics on the GPU with mumax3: exchange, anisotropy, demagnetisation, "
                   "Zeeman and thermal fields on a grid of cells. Input: SpinMate lines or an .mx3 script.")

    def ready(self):
        return (True, "") if binary() else (False, "mumax3 is not installed here: set MUMAX3_BIN (needs an NVIDIA GPU)")

    def parameter_schema(self):
        return {k: {"default": v[0], "kind": v[1], "unit": v[2], "description": v[3]} for k, v in PARAMS.items()}

    def validate(self, parameters_text):
        warnings = []
        try:
            if is_script(parameters_text):
                vals, changed = None, {}
                warnings.append("an .mx3 script is checked by mumax3 itself when the run starts")
            else:
                vals = parse(parameters_text)
                changed = {k: v for k, v in vals.items() if v != PARAMS[k][0]}
                cells = vals["Nx"] * vals["Ny"] * vals["Nz"]
                if cells > 4_000_000:
                    warnings.append(f"{cells:,} cells: this needs a large GPU")
        except ValueError as e:
            return Validation(ok=False, error=str(e))
        if not binary():
            warnings.append("mumax3 is not installed here (set MUMAX3_BIN): the run will fail")
        steps = int(vals["t"] / vals["table_dt"]) if vals else 0
        return Validation(ok=True, steps=steps, estimated_runtime_s=0.0, changed_from_defaults=changed,
                          warnings=warnings, extra={"cleaned_text": parameters_text.strip() + "\n"})

    def run_command(self):
        return [sys.executable, "-m", "spinmate_mumax3.runner"]

    def outputs(self):
        return {"input.mx3": "the script mumax3 ran", "table.txt": "mumax3 table: t, <m>, energies",
                "output1.dat": "time and the average m (x, y, z)", "summary.json": "metrics of the run",
                "Two_Spin_Dynamics.png": "plot of <m>(t)", "parameters.txt": "the input"}
