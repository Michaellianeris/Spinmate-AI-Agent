"""Fixed requests for the prompt evaluation. Each case runs in a fresh conversation, in this order
(the catalog cases come after the runs they query).

Checks:
  tools       every listed tool must be called
  no_tools    none of these may be called
  order       order_final of the last simulation started in the case
  max_lines   lines of the final answer, code blocks excluded
  expect      regexes the final answer must match (case-insensitive)
  forbid      regexes it must not match
  check       custom function(ctx) -> (ok, message)
"""
import re

FREQ = r"\b\d+(\.\d+)?\s*GHz"


def _last_param(ctx, name):
    """Value of --name in the parameter text of the last run, or None."""
    text = (ctx.last_run(full=True) or {}).get("parameters_text") or ""
    m = re.findall(rf"--{name}=([-+0-9.eE]+)", text)
    return float(m[-1]) if m else None


def unit_slip(ctx):
    a0 = _last_param(ctx, "A0")
    if a0 is not None:
        return abs(a0) < 1e-10, f"A0 used = {a0:g}"
    asked = re.search(r"e-12|pJ|J/m", ctx.answer)
    return bool(asked), "no run; answer points out the unit" if asked else "no run and no unit remark"


def few_runs(limit):
    def check(ctx):
        n = ctx.runs_started
        return n <= limit, f"{n} runs started (limit {limit})"
    return check


CASES = [
    {"id": "afm_fm", "mode": "fast",
     "turns": ["Start antiferromagnetic with a positive exchange A0 = 0.248e-12, no current, 2 ns. "
               "Does it end ferromagnetic?"],
     "tools": ["start_simulation", "wait_for_run"], "no_tools": ["validate_parameters"],
     "order": "FM", "max_lines": 6, "expect": [r"\bFM\b"]},
    {"id": "afm_afm", "mode": "fast",
     "turns": ["Start antiferromagnetic with the negative default exchange, no current, 2 ns. Does it stay AFM?"],
     "tools": ["start_simulation"], "order": "AFM", "max_lines": 6},
    {"id": "fm_afm_100ps", "mode": "fast",
     "turns": ["Start with m1 = 0.25,0,0.968 and m2 = 0.35,0,0.937, A0 = -0.248e-12, no current, 100 ps. "
               "What happens?"],
     "tools": ["start_simulation"], "order": "AFM", "max_lines": 6,
     "forbid": [r"\b19\b", FREQ]},          # ringing crossings and FFT of a 50 ps window are not results
    {"id": "ferrh_deep", "mode": "deep",
     "turns": ["Heat a FeRh layer through its transition: positive A0 = 0.248e-12, temperature 370 K +- 60 K at "
               "0.25 GHz, 2 ns. When does it go from AFM to FM?"],
     "tools": ["validate_parameters", "start_simulation", "get_run"], "order": "FM",
     "expect": [r"37[05]\s*K|transition"]},
    {"id": "sot_rotation", "mode": "deep",
     "turns": ["Apply a DC spin-orbit current of 100e6 A/cm2 for 2 ns to the default AFM state. What happens?"],
     "tools": ["start_simulation"], "expect": [r"rotat|oscillat|precess", FREQ]},
    {"id": "unit_slip", "mode": "fast",
     "turns": ["Run 2 ns with A0 = 0.248 and no current."],
     "check": unit_slip},
    {"id": "continuation", "mode": "fast",
     "turns": ["Run the FeRh heating scenario for 0.5 ns, then continue it for another 0.5 ns from the final "
               "state and show me one plot of the whole 1 ns."],
     "tools": ["continue_run", "combine_runs"], "no_tools": []},
    {"id": "goal_search", "mode": "deep",
     "turns": ["Find the smallest DC SOT current between 50e6 and 150e6 A/cm2 that makes the Neel vector of "
               "the default AFM state rotate continuously. Use 0.5 ns runs."],
     "tools": ["start_sweep"], "expect": [r"between|bracket|threshold"],
     "check": few_runs(40)},
    {"id": "catalog_table", "mode": "fast",
     "turns": ["Make me a table of all finished runs: run id, A0, initial and final order, and the time of the "
               "first real n_z switch in ps."],
     "tools": ["query_runs"], "no_tools": ["get_run", "start_simulation"], "max_lines": 8},
    {"id": "catalog_count", "mode": "fast",
     "turns": ["How many finished runs ended FM, and which A0 values did they use?"],
     "tools": ["query_runs"], "no_tools": ["start_simulation"], "max_lines": 8},
    {"id": "compare", "mode": "fast",
     "turns": ["Compare the AFM -> FM run and the AFM -> AFM run from before: what differs?"],
     "tools": ["compare_runs"], "no_tools": ["start_simulation"], "max_lines": 8},
    {"id": "report", "mode": "fast",
     "turns": ["Write a short report of the FeRh runs you made, with your interpretation."],
     "tools": ["make_report"], "no_tools": ["start_simulation"]},
    {"id": "figure", "mode": "fast",
     "turns": ["Make a figure of n_z for the AFM -> FM run for my paper, first 100 ps, IEEE style."],
     "tools": ["make_figure"], "no_tools": ["start_simulation"], "max_lines": 5},
    {"id": "next_steps", "mode": "fast",
     "turns": ["Run AFM -> FM with A0 = 0.248e-12 for 1 ns."],
     "check": lambda ctx: (bool(re.search(r"^\s*NEXT:.+\|", ctx.answer, re.M)), "ends with a NEXT line")},
    {"id": "params_question", "mode": "fast",
     "turns": ["Which parameters control the FeRh temperature cycle?"],
     "no_tools": ["start_simulation", "start_sweep"], "max_lines": 10},
    {"id": "greeting", "mode": "fast", "name": "Michalis",
     "turns": ["Run the default scenario.", "Now the same with A0 = -0.248e-12."],
     "tools": ["start_simulation"], "order": "AFM", "max_lines": 6,
     "forbid": [r"^\W*(hi|hello|hey)?\W*michalis"]},   # no second greeting
]
