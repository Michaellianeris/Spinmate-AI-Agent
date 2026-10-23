"""The SpinMate tools: JSON schemas and one dispatcher, used by the web app agent and the MCP server."""
import base64
import json
import os

from . import catalog
from . import figures
from . import reports
from . import params as P
from . import simulations as S

TEXT = {"type": "string"}

TOOLS = [
    {
        "name": "list_input_files",
        "description": "List parameter .txt files uploaded by the user or saved earlier.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_input_file",
        "description": "Read a parameter .txt file by name.",
        "input_schema": {"type": "object", "properties": {"name": TEXT}, "required": ["name"]},
    },
    {
        "name": "save_input_file",
        "description": "Save parameter text as a .txt file the user can download and reuse.",
        "input_schema": {"type": "object",
                         "properties": {"name": TEXT, "parameters_text": TEXT},
                         "required": ["name", "parameters_text"]},
    },
    {
        "name": "validate_parameters",
        "description": "Parse parameter text with the simulator's own parser. Returns errors, the values "
                       "that differ from the defaults, step count, estimated runtime and warnings.",
        "input_schema": {"type": "object", "properties": {"parameters_text": TEXT,
                                                          "engine": {"type": "string", "description": "default macrospin"}},
                         "required": ["parameters_text"]},
    },
    {
        "name": "list_engines",
        "description": "The simulation engines installed here (macrospin, mm2d and mm3d are built in; mumax3 and others are "
                         "plug-ins), with what they model and their parameters.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "start_simulation",
        "description": "Start one simulation in the background. Returns a run id immediately. engine defaults to "
                       "macrospin; list_engines shows the others and their parameters.",
        "input_schema": {"type": "object",
                         "properties": {"parameters_text": TEXT,
                                        "label": {"type": "string", "description": "short description"},
                                        "engine": {"type": "string", "description": "simulation engine, default macrospin"}},
                         "required": ["parameters_text"]},
    },
    {
        "name": "start_sweep",
        "description": "Start one run per value of a scalar parameter (e.g. Fr for a frequency sweep). "
                       "Other parameters come from parameters_text. engine=mm2d or mm3d sweeps a micromagnetic "
                       "parameter (D, Ku, Msat, thickness, ... or one component of a vector, e.g. B_ext.z): the "
                       "result rows give the texture, its phase, Q or H and the radius, and the plot is the radius "
                       "against the parameter. Add parameter2 and values2 for a phase diagram (one run per pair, "
                       "e.g. D against B_ext.z: uniform, skyrmion, stripes).",
        "input_schema": {"type": "object",
                         "properties": {"parameters_text": TEXT, "parameter": TEXT,
                                        "values": {"type": "array", "items": {"type": "number"}},
                                        "label": TEXT,
                                        "engine": {"type": "string", "description": "macrospin (default), mm2d or mm3d"},
                                        "parameter2": {"type": "string", "description": "mm2d/mm3d: second parameter, for a phase diagram"},
                                        "values2": {"type": "array", "items": {"type": "number"}}},
                         "required": ["parameters_text", "parameter", "values"]},
    },
    {
        "name": "wait_for_run",
        "description": "Wait until a run has finished (up to timeout_s, at most 120). Returns the status and, when "
                       "done, the summary. Call it right after start_simulation; get_run is only needed for the "
                       "plot or the full parameter text.",
        "input_schema": {"type": "object",
                         "properties": {"run_id": TEXT, "timeout_s": {"type": "number"}},
                         "required": ["run_id"]},
    },
    {
        "name": "wait_for_sweep",
        "description": "Wait until every run of a sweep has finished (up to timeout_s, at most 120). "
                       "Then call get_sweep. Rows carry mean |n_z|, neel_z_switches (real reversals), order_final "
                       "and neel_z_final, which are what a threshold search needs.",
        "input_schema": {"type": "object",
                         "properties": {"sweep_id": TEXT, "timeout_s": {"type": "number"}},
                         "required": ["sweep_id"]},
    },
    {
        "name": "get_run",
        "description": "Status, progress, parameters and result summary of a run. Set include_plot=true "
                       "to also receive the magnetization dynamics plot of a finished run.",
        "input_schema": {"type": "object",
                         "properties": {"run_id": TEXT, "include_plot": {"type": "boolean"}},
                         "required": ["run_id"]},
    },
    {
        "name": "get_sweep",
        "description": "Status of a sweep and, when finished, mean |n_z| per value and a plot. "
                       "discard_fraction drops the initial transient (0-0.95) before averaging.",
        "input_schema": {"type": "object",
                         "properties": {"sweep_id": TEXT, "discard_fraction": {"type": "number"},
                                        "include_plot": {"type": "boolean"}},
                         "required": ["sweep_id"]},
    },
    {
        "name": "continue_run",
        "description": "Continue a finished run from its final state for extra_t more seconds (default: the same "
                       "duration). Time does not restart: the new run covers t_end -> t_end + extra_t, so AC "
                       "fields, A0(t), Ku(t), chirp and the FeRh temperature cycle keep their phase. "
                       "extra_parameters (parameter lines) can change conditions for the new segment. "
                       "Returns the new run id; call wait_for_run, then combine_runs for one trajectory.",
        "input_schema": {"type": "object",
                         "properties": {"run_id": TEXT, "extra_t": {"type": "number"},
                                        "extra_parameters": TEXT, "label": TEXT},
                         "required": ["run_id"]},
    },
    {
        "name": "combine_runs",
        "description": "Join finished runs into one new run with one trajectory, summary, CSV and plot over the "
                       "whole time range (segment joins marked). Give run_id of the last segment to follow its "
                       "continuation chain, or run_ids in time order. Then call get_run on the returned run_id "
                       "with include_plot=true.",
        "input_schema": {"type": "object",
                         "properties": {"run_id": TEXT, "run_ids": {"type": "array", "items": TEXT},
                                        "label": TEXT}},
    },
    {
        "name": "list_runs",
        "description": "Most recent runs with their status.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "cancel_run",
        "description": "Stop a queued or running simulation.",
        "input_schema": {"type": "object", "properties": {"run_id": TEXT}, "required": ["run_id"]},
    },
]


def _query_description(dialect):
    pg = dialect == "postgres"
    db = "PostgreSQL" if pg else "SQLite"
    json_t, arr_t, time_t = ("jsonb", "text[]", "timestamptz") if pg else ("JSON text", "JSON array text", "ISO text, UTC")
    return (
        f"Read-only SQL ({db}) over the catalog of all runs and sweeps, for questions across many runs: "
        "filters, counts, rankings, comparisons. One SELECT (or WITH ... SELECT). The result is shown to the "
        "user as a table, so select clear columns, order the rows and alias long expressions.\n"
        "Table runs: run_id, label, kind ('run' or 'combined'), status ('queued','running','done','failed',"
        f"'cancelled','interrupted'), created, started, finished ({time_t}), steps, sweep_id, sweep_value, "
        f"parent_run, t_offset_s, chain ({arr_t} of a combined run), params ({json_t}: every parameter of the "
        "run, defaults included, e.g. params->>'A0'; vectors are arrays, e.g. params->'m1'->>2), changed "
        f"({json_t}: only the values that differ from the defaults), t_start_s, t_end_s, runtime_s, "
        "order_initial, order_final ('AFM','FM','canted'), m1_dot_m2_initial, m1_dot_m2_final, "
        "neel_z_switches (real reversals of n_z), first_neel_z_switch_s, settle_time_s (null = still moving), "
        "neel_z_sign_changes (every zero crossing, ringing included), neel_z_abs_mean_second_half, freq_m1x_ghz, "
        f"freq_neel_z_ghz, summary ({json_t}, the full summary.json).\n"
        f"Table sweeps: sweep_id, label, parameter, sweep_values ({arr_t}), run_ids ({arr_t}), created.\n"
        "Cast numbers from JSON with CAST(params->>'A0' AS DOUBLE PRECISION). Times are in seconds; multiply "
        "by 1e9 for ns. Example: SELECT run_id, CAST(params->>'A0' AS DOUBLE PRECISION) AS a0, order_final, "
        "first_neel_z_switch_s*1e12 AS first_switch_ps FROM runs WHERE status='done' AND "
        "CAST(params->>'A0' AS DOUBLE PRECISION) > 0 ORDER BY first_switch_ps")


TOOLS += [
    {
        "name": "compare_runs",
        "description": "Compare 2-6 runs side by side: key results of each and the parameters that differ. The "
                       "page also shows them overlaid in one chart. Use it when the user asks how runs differ.",
        "input_schema": {"type": "object", "properties": {"run_ids": {"type": "array", "items": TEXT}},
                         "required": ["run_ids"]},
    },
    {
        "name": "compare_with_mumax3",
        "description": "Cross-check an mm2d input with mumax3: runs the same parameters on both engines from the same "
                       "starting state (mm2d's local thin-film demag becomes no demag with Ku1 = Ku - mu0 Ms^2/2 in "
                       "mumax3, the same energy; demag=full keeps mumax3's demag; a mask becomes ImageShape). Needs "
                       "mumax3 installed (list_engines shows it). Not for currents, temperature, antennas or "
                       "two-sublattice films. Returns compare_id; then call get_comparison.",
        "input_schema": {"type": "object",
                         "properties": {"parameters_text": TEXT, "label": TEXT},
                         "required": ["parameters_text"]},
    },
    {
        "name": "get_comparison",
        "description": "Wait (up to timeout_s, at most 120) for a cross-check with mumax3 and return the comparison: "
                       "topological charge, radius, energy, the rms angle between the two final textures and, for "
                       "dynamics, <m>(t); each with its tolerance and ok, and agree for all of them. The page shows "
                       "the two textures side by side with the angle between them.",
        "input_schema": {"type": "object",
                         "properties": {"compare_id": TEXT, "timeout_s": {"type": "number"},
                                        "include_plot": {"type": "boolean"}},
                         "required": ["compare_id"]},
    },
    {
        "name": "make_figure",
        "description": "Make a publication-quality figure (matplotlib + SciencePlots) of 1-6 runs. The chat shows it with "
                       "PDF, SVG and PNG downloads. quantities: m1x m1y m1z m2x m2y m2z nx ny nz dot (m1.m2) mz (net); "
                       "one run: several quantities; several runs: usually one quantity. Optional time window in ns, "
                       "style science | ieee | nature | plain, title. Use it when the user asks for a graph or figure.",
        "input_schema": {"type": "object",
                         "properties": {"run_ids": {"type": "array", "items": TEXT},
                                        "quantities": {"type": "array", "items": TEXT},
                                        "t0_ns": {"type": "number"}, "t1_ns": {"type": "number"},
                                        "style": TEXT, "title": TEXT},
                         "required": ["run_ids"]},
    },
    {
        "name": "make_report",
        "description": "Write a report of 1-6 finished runs (method, results table, parameters, figures, appendix) "
                       "as an HTML page the user can print to PDF, plus a Markdown zip. Put your interpretation in "
                       "notes (Markdown, a few short paragraphs or bullets, only numbers from the runs; symbols with indices "
                       "or exponents in LaTeX such as $n_z$, $m_{1z}$, $0.248\\times10^{-12}$). Use it when "
                       "the user asks for a report or a write-up.",
        "input_schema": {"type": "object",
                         "properties": {"run_ids": {"type": "array", "items": TEXT}, "title": TEXT, "notes": TEXT},
                         "required": ["run_ids"]},
    },
]

CONFIRM_MINUTES = float(os.environ.get("CONFIRM_MINUTES") or 10)
CONFIRM_RUNS = int(os.environ.get("CONFIRM_RUNS") or 40)


def _needs_confirmation(name, args, approved):
    """Large requests wait for the user's Run button; the next message then carries the approval."""
    if approved:
        return None
    if name not in ("start_simulation", "start_sweep", "compare_with_mumax3"):
        return None
    runs = len(args.get("values") or []) * (len(args.get("values2") or []) if args.get("parameter2") else 1) \
        if name == "start_sweep" else 1
    engine = "mm2d" if name == "compare_with_mumax3" else args.get("engine")
    wall = S.estimate(args.get("parameters_text") or "", runs, engine)
    if wall is None:
        return None
    if wall <= CONFIRM_MINUTES * 60 and runs <= CONFIRM_RUNS:
        return None
    return {"ok": False, "needs_confirmation": True, "runs": runs, "estimated_wall_time_s": round(wall, 1),
            "error": confirmation_message(runs, wall)}


def confirmation_message(runs, wall):
    return (f"needs the user's confirmation: {runs} run(s), about {wall / 60:.1f} min. The page shows a Run "
            "button; tell the user the estimate in one line and stop. Do not retry in this turn.")


QUERY_TOOL = {
    "name": "query_runs",
    "description": _query_description(catalog.dialect()),
    "input_schema": {"type": "object",
                     "properties": {"sql": TEXT,
                                    "limit": {"type": "integer", "description": "max rows returned, default 200"}},
                     "required": ["sql"]},
}
if catalog.enabled():
    TOOLS.append(QUERY_TOOL)


def _image(path):
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _brief(run):
    keep = ("id", "label", "status", "progress", "eta_s", "steps", "estimated_runtime_s",
            "sweep_id", "sweep_value", "warnings", "changed_from_defaults", "summary",
            "parameters_text", "error_tail", "files", "kind", "chain", "parent_run", "t_offset_s")
    return {k: run[k] for k in keep if k in run and run[k] is not None}


def call(name, args, approved=False):
    """Run a tool. Returns (content for Claude, event for the UI)."""
    gate = _needs_confirmation(name, args, approved)
    if gate:
        return json.dumps(gate), {"tool": name, **{k: gate[k] for k in ("needs_confirmation", "runs",
                                                                       "estimated_wall_time_s", "error")}}
    if name == "list_input_files":
        out = {"files": S.list_inputs()}
    elif name == "read_input_file":
        text = S.read_input(args["name"])
        out = {"name": args["name"], "text": text} if text is not None else {"error": "file not found"}
    elif name == "save_input_file":
        out = {"saved": S.save_input(args["name"], args["parameters_text"])}
    elif name == "validate_parameters":
        out = S._check(args["parameters_text"], args.get("engine"))
        out.pop("cleaned_text", None)
    elif name == "list_engines":
        from . import engines
        out = {"engines": []}
        for n in engines.available():
            try:
                e = engines.get(n)
            except Exception as err:   # a broken plug-in is reported, not fatal
                out["engines"].append({"name": n, "error": str(err)})
                continue
            schema = e.parameter_schema()
            ready, note = e.ready() if hasattr(e, "ready") else (True, "")
            out["engines"].append({"name": n, "title": e.title, "description": e.description, "ready": ready,
                                   **({"note": note} if note else {}),
                                   "parameters": schema if n != "macrospin" else "see spinmate://parameters"})
        from .materials import table
        out["materials"] = table()                     # --material=<name> for mm2d and mm3d
    elif name == "start_simulation":
        out = S.start_run(args["parameters_text"], args.get("label", ""), engine=args.get("engine"))
    elif name == "start_sweep":
        out = S.start_sweep(args["parameters_text"], args["parameter"], args["values"], args.get("label", ""),
                            engine=args.get("engine"), parameter2=args.get("parameter2"), values2=args.get("values2"))
    elif name == "wait_for_run":
        run = S.wait_for_run(args["run_id"], float(args.get("timeout_s") or 60))
        out = _brief(run) if run else {"error": f"no run {args['run_id']}"}
    elif name == "wait_for_sweep":
        out = S.wait_for_sweep(args["sweep_id"], float(args.get("timeout_s") or 60)) \
            or {"error": f"no sweep {args['sweep_id']}"}
    elif name == "get_run":
        run = S.run_info(args["run_id"], full=True)
        if not run:
            out = {"error": f"no run {args['run_id']}"}
        else:
            out = _brief(run)
            png = S.run_file(run["id"], "Two_Spin_Dynamics.png")
            if args.get("include_plot") and png:
                return [{"type": "text", "text": json.dumps(out)}, _image(png)], \
                    {"tool": name, "run_id": run["id"]}
    elif name == "get_sweep":
        info = S.sweep_info(args["sweep_id"], float(args.get("discard_fraction") or 0.0))
        if not info:
            out = {"error": f"no sweep {args['sweep_id']}"}
        else:
            out = info
            png = S.sweep_file(info["id"], info.get("plot") or "")
            if args.get("include_plot") and png:
                return [{"type": "text", "text": json.dumps(out)}, _image(png)], \
                    {"tool": name, "sweep_id": info["id"]}
    elif name == "continue_run":
        out = S.continue_run(args["run_id"], args.get("extra_t"), args.get("extra_parameters") or "",
                             args.get("label", ""))
    elif name == "combine_runs":
        out = S.combine_runs(args.get("run_ids"), args.get("run_id"), args.get("label", ""))
    elif name == "list_runs":
        out = {"runs": [{k: r.get(k) for k in ("id", "label", "status", "progress", "parent_run", "kind")}
                        for r in S.list_runs(int(args.get("limit") or 20))]}
    elif name == "cancel_run":
        out = S.cancel(args["run_id"])
    elif name == "compare_runs":
        out = S.compare(args.get("run_ids") or [])
    elif name == "compare_with_mumax3":
        from . import compare
        out = compare.start(args["parameters_text"], args.get("label", ""))
    elif name == "get_comparison":
        from . import compare
        out = compare.result(args["compare_id"], float(args.get("timeout_s") or 60)) \
            or {"error": f"no cross-check {args['compare_id']}"}
        png = compare.plot_file(args["compare_id"]) if out.get("plot") else None
        if args.get("include_plot") and png:
            return [{"type": "text", "text": json.dumps(out, default=str)}, _image(png)], event_for(name, out)
    elif name == "make_figure":
        out = figures.save(args.get("run_ids") or [], args.get("quantities") or ["m1z", "m2z", "nz"],
                           args.get("t0_ns"), args.get("t1_ns"), args.get("style") or "science",
                           title=args.get("title") or "")
    elif name == "make_report":
        out = reports.build(args.get("run_ids") or [], args.get("title") or "", args.get("notes") or "")
    elif name == "query_runs":
        out = catalog.query(args.get("sql", ""), args.get("limit") or 200)
    else:
        out = {"error": f"unknown tool {name}"}

    return json.dumps(out, default=str), event_for(name, out)


def event_for(name, out):
    """The part of a tool result the web page needs for its chips and cards."""
    event = {"tool": name}
    if not isinstance(out, dict):
        return event
    if out.get("needs_confirmation"):
        return {"tool": name, **{k: out.get(k) for k in ("needs_confirmation", "runs", "estimated_wall_time_s", "error")}}
    for key in ("run_id", "sweep_id", "runs", "saved", "ok", "error", "run_ids", "report", "html_url", "zip_url", "figure",
                "png_url", "compare_id", "agree"):
        if key in out:
            event[key] = out[key]
    if name in ("get_run", "wait_for_run") and "id" in out:
        event["run_id"] = out["id"]
    return event
