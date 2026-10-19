"""The original two-sublattice macrospin solver (macrospin/, unchanged), behind the Engine interface."""
from .. import params as P
from .. import simulations as S
from .base import Validation


class MacrospinEngine:
    name = "macrospin"
    title = "Two-sublattice macrospin"
    description = ("Coupled LLG equations of two sublattices with exchange, anisotropy, Zeeman, SOT, thermal "
                   "noise and time-dependent drives; Heun integration (the original code, unchanged).")

    def parameter_schema(self):
        out = {}
        for name, value in P.defaults().items():
            kind = "vector" if name in P.VECTOR_PARAMS else "flag" if name in P.FLAG_PARAMS else "float"
            out[name] = {"default": value, "kind": kind}
        return out

    def validate(self, parameters_text):
        r = P.validate(parameters_text, S.sec_per_step())
        keys = ("ok", "error", "steps", "estimated_runtime_s", "changed_from_defaults", "warnings")
        return Validation(**{k: r[k] for k in keys if k in r},
                          extra={k: v for k, v in r.items() if k not in keys and k != "cleaned_text"})

    def run_command(self):
        return list(S.RUNNER_CMD)

    def outputs(self):
        return {"output1.dat": "time and m1 (x, y, z)", "output2.dat": "time and m2 (x, y, z)",
                "neel_z.dat": "time and n_z", "summary.json": "metrics of the run",
                "Two_Spin_Dynamics.png": "plot of the dynamics", "parameters.txt": "the input file"}
