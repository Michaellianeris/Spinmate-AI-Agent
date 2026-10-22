"""SpinMate as an MCP server: the simulation tools, the runs and the parameter reference for any agent."""
import base64
import json
import os
import re
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server

from app import prompts
from spinmate_core import simulations as S
from spinmate_core import tools

VERSION = "2.0.0-dev"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
GATED = ("start_simulation", "start_sweep", "compare_with_mumax3")

INSTRUCTIONS = """SpinMate runs the original two-sublattice macrospin solver (antiferromagnets, FeRh, exchange-coupled
ferromagnets). Usual flow: validate_parameters -> start_simulation (or start_sweep) -> wait_for_run (or wait_for_sweep)
-> get_run with include_plot=true. Parameters are lines such as --A0=0.248e-12 or --t=2e-9 (SI units); only
values that differ from the defaults are needed. Read spinmate://physics/guide before designing runs,
spinmate://parameters for every parameter and its unit, and
spinmate://physics/scales for the time, field and frequency scales of the default material. Report only numbers
that come from tool results. Large requests return needs_confirmation: ask the user, then call again with
confirmed=true."""


def app_url():
    return (os.environ.get("APP_URL") or "http://localhost:8421").rstrip("/")


def _section(text, start, end=None):
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i) if end else -1
    return text[i:j if j > 0 else None].strip()


PARAMETERS = _section(prompts.SYSTEM_PROMPT, "Parameter reference")
GUIDE = (Path(__file__).resolve().parent / "physics.md").read_text()
SCALES = _section(GUIDE, "## Scales of the default material", "## Answer")


def tool_list():
    out = []
    for t in tools.TOOLS:
        schema = json.loads(json.dumps(t["input_schema"]))
        desc = t["description"]
        if t["name"] in GATED:
            schema.setdefault("properties", {})["confirmed"] = {
                "type": "boolean", "description": "true only after the user agreed to a request that returned "
                                                  "needs_confirmation"}
        desc = desc.replace("The chat shows it", "The result has the PNG and links to").replace(
            "The page also shows them overlaid in one chart. ", "")
        out.append(types.Tool(name=t["name"], description=desc, input_schema=schema))
    return out


def _absolute(obj):
    """Links of the web app (/api/...) become full URLs, so they work outside the browser."""
    if isinstance(obj, dict):
        return {k: (app_url() + v if isinstance(v, str) and v.startswith("/api/") else _absolute(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_absolute(v) for v in obj]
    return obj


def call(name, args):
    """Runs one tool and returns MCP content plus the structured result."""
    args = dict(args or {})
    confirmed = bool(args.pop("confirmed", False)) if name in GATED else False
    if name not in {t["name"] for t in tools.TOOLS}:
        return [types.TextContent(type="text", text=f"unknown tool {name}")], None, True
    content, _ = tools.call(name, args, approved=confirmed)
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
    out, data, error = [], None, False
    for b in blocks:
        if b["type"] == "image":
            out.append(types.ImageContent(type="image", data=b["source"]["data"], mime_type=b["source"]["media_type"]))
            continue
        try:
            data = _absolute(json.loads(b["text"]))
        except ValueError:
            out.append(types.TextContent(type="text", text=b["text"]))
            continue
        if isinstance(data, dict) and data.get("needs_confirmation"):
            data["error"] = (f"needs the user's confirmation: {data['runs']} run(s), about "
                             f"{data['estimated_wall_time_s'] / 60:.1f} min. Ask the user; if they agree, call "
                             f"{name} again with confirmed=true.")
        elif isinstance(data, dict) and data.get("error") and data.get("ok") is not True:
            error = True
        out.append(types.TextContent(type="text", text=json.dumps(data, default=str)))
    if name == "make_figure" and isinstance(data, dict) and data.get("ok"):
        png = S.WORKSPACE / "figures" / data["figure"] / "figure.png"
        if png.exists():
            out.append(types.ImageContent(type="image", data=base64.b64encode(png.read_bytes()).decode(),
                                          mime_type="image/png"))
    return out, data if isinstance(data, dict) else None, error


# ---------------------------------------------------------------- resources
STATIC = {
    "spinmate://parameters": ("Parameter reference", "Every solver parameter, its unit and default.", PARAMETERS),
    "spinmate://physics/scales": ("Physics scales", "Exchange and anisotropy fields, mode frequencies and switching "
                                  "times of the default material, measured with SpinMate.", SCALES),
    "spinmate://physics/guide": ("Physics guide", "How to design, run and interpret SpinMate simulations: workflows, "
                                 "how to read the summary, scales of the default material.", None),
}
TEMPLATES = [
    ("spinmate://runs/{run_id}", "Run", "Status, summary and changed parameters of a run.", "application/json"),
    ("spinmate://runs/{run_id}/parameters.txt", "Run parameter file", "The exact input file of a run.", "text/plain"),
    ("spinmate://examples/{name}", "Example parameter file", "A ready parameter file from examples/.", "text/plain"),
]


def resource_list():
    res = [types.Resource(uri=u, name=n, description=d, mime_type="text/markdown" if t is None else "text/plain")
           for u, (n, d, t) in STATIC.items()]
    res.append(types.Resource(uri="spinmate://validation", name="Physics validation",
                              description="Latest physics validation of the engine: cases, results and findings.",
                              mime_type="application/json"))
    res.append(types.Resource(uri="spinmate://runs", name="Runs", description="The most recent runs and their status.",
                              mime_type="application/json"))
    for p in sorted(EXAMPLES.glob("*.txt")):
        res.append(types.Resource(uri=f"spinmate://examples/{p.name}", name=p.stem.replace("_", " "),
                                  description="Example parameter file", mime_type="text/plain"))
    return res


def read(uri):
    """Returns (text, mime type) for a SpinMate resource, or raises ValueError."""
    if uri == "spinmate://physics/guide":
        return GUIDE, "text/markdown"
    if uri in STATIC:
        return STATIC[uri][2], "text/plain"
    if uri == "spinmate://validation":
        f = S.WORKSPACE / "validation" / "report.json"
        if not f.exists():
            raise ValueError("no validation report yet: run python -m spinmate_core.validation")
        return f.read_text(), "application/json"
    if uri == "spinmate://runs":
        rows = [{k: r.get(k) for k in ("id", "label", "status", "kind", "sweep_id", "parent_run")}
                for r in S.list_runs(100)]
        return json.dumps(rows), "application/json"
    m = re.fullmatch(r"spinmate://runs/([\w-]+)(/parameters\.txt)?", uri)
    if m:
        run = S.run_info(m.group(1), full=True)
        if not run:
            raise ValueError(f"no run {m.group(1)}")
        if m.group(2):
            return run.get("parameters_text") or "", "text/plain"
        return json.dumps(_absolute(tools._brief(run)), default=str), "application/json"
    m = re.fullmatch(r"spinmate://examples/([\w.-]+\.txt)", uri)
    if m and (EXAMPLES / m.group(1)).is_file():
        return (EXAMPLES / m.group(1)).read_text(), "text/plain"
    raise ValueError(f"unknown resource {uri}")


# ---------------------------------------------------------------- server
async def _list_tools(ctx, params):
    return types.ListToolsResult(tools=tool_list())


async def _call_tool(ctx, params):
    # tools block (wait_for_run waits up to 120 s), so they run in a worker thread
    content, data, error = await anyio.to_thread.run_sync(call, params.name, params.arguments or {})
    return types.CallToolResult(content=content, structured_content=data, is_error=error)


async def _list_resources(ctx, params):
    return types.ListResourcesResult(resources=resource_list())


async def _list_templates(ctx, params):
    return types.ListResourceTemplatesResult(resource_templates=[
        types.ResourceTemplate(uri_template=u, name=n, description=d, mime_type=mt) for u, n, d, mt in TEMPLATES])


async def _read_resource(ctx, params):
    text, mime = await anyio.to_thread.run_sync(read, str(params.uri))
    return types.ReadResourceResult(contents=[types.TextResourceContents(uri=str(params.uri), mime_type=mime, text=text)])


# ---------------------------------------------------------------- prompts: the physics subagent
PROMPTS = {
    "design_experiment": (
        "Design and run the simulations that answer a physics question, then interpret them.",
        [("goal", "the question, e.g. 'does a 0.3 T field along z switch the Néel vector?'", True)],
        "Design the simulations that answer this question, run them with the SpinMate tools and interpret the "
        "result. First say in two lines which runs you will do and why (durations from the scales).\n\n"
        "Question: {goal}"),
    "find_threshold": (
        "Bracket the value of a parameter where the behaviour changes, with coarse and fine sweeps.",
        [("parameter", "the parameter to sweep, e.g. SOT_DC_Amp or A0", True),
         ("behaviour", "what changes at the threshold, e.g. 'the Néel vector switches'", True),
         ("conditions", "other parameter lines, optional", False)],
        "Find the value of {parameter} at which {behaviour}. Follow the threshold search workflow: coarse sweep, "
        "then finer sweeps inside the bracket, at most 4 rounds. Conditions: {conditions}"),
    "interpret_run": (
        "Explain what happened in a finished run, from its summary and plot.",
        [("run_id", "the run, e.g. r0001", True)],
        "Explain run {run_id}: call get_run with include_plot=true, state the outcome, the mechanism and the "
        "evidence, the caveats and one useful next run."),
}


async def _list_prompts(ctx, params):
    return types.ListPromptsResult(prompts=[
        types.Prompt(name=n, description=d, arguments=[types.PromptArgument(name=a, description=ad, required=r)
                                                        for a, ad, r in args])
        for n, (d, args, _) in PROMPTS.items()])


def prompt_text(name, arguments):
    if name not in PROMPTS:
        raise ValueError(f"unknown prompt {name}")
    desc, args, task = PROMPTS[name]
    values = {a: (arguments or {}).get(a) or ("none" if not r else "") for a, _, r in args}
    missing = [a for a, _, r in args if r and not values[a]]
    if missing:
        raise ValueError(f"missing argument: {', '.join(missing)}")
    return desc, GUIDE + "\n\n## Task\n" + task.format(**values)


async def _get_prompt(ctx, params):
    desc, text = prompt_text(params.name, params.arguments)
    return types.GetPromptResult(description=desc, messages=[
        types.PromptMessage(role="user", content=types.TextContent(type="text", text=text))])


def build():
    return Server("spinmate", version=VERSION, title="SpinMate",
                  description="Two-sublattice macrospin simulations: runs, sweeps, figures and reports.",
                  instructions=INSTRUCTIONS, website_url="https://github.com/Michaellianeris/Spinmate-AI-Agent",
                  on_list_tools=_list_tools, on_call_tool=_call_tool, on_list_resources=_list_resources,
                  on_list_resource_templates=_list_templates, on_read_resource=_read_resource,
                  on_list_prompts=_list_prompts, on_get_prompt=_get_prompt)
