"""A LangGraph workflow on top of SpinMate's MCP server: a threshold search as a graph.

    plan -> start_sweep -> (approve) -> wait -> analyse -> plan ... -> done

plan      values over the range (round 1) or inside the bracket found so far
start     start_sweep through MCP; a large sweep comes back with needs_confirmation
approve   human in the loop: interrupt() and wait for a yes or no
wait      wait_for_sweep until every run is done (long runs keep running on the server)
analyse   classify each value with the criterion, find where it changes, narrow the bracket
done      when the bracket is narrower than rel_width of its centre, after max_rounds, or when nothing changes

The graph state is checkpointed (SQLite): stop the script at any time, start it again with the same thread id,
and it goes on from the last step, for example waiting again for a sweep of 100 ns runs that kept running."""
import json
import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class Search(TypedDict, total=False):
    # the question
    parameters_text: str
    engine: str                 # macrospin, mm2d or mm3d
    parameter: str              # e.g. A0, D, B_ext.z
    lo: float
    hi: float
    criterion: dict             # {"field": "order_final", "equals": "FM"} or {"field": ..., "above": x}
    points: int                 # values per sweep
    max_rounds: int
    rel_width: float            # stop when (hi - lo) < rel_width * |centre|
    # the progress
    round: int
    values: list
    sweep_id: Optional[str]
    approved: bool
    tried: Annotated[list, operator.add]       # [value, True/False, run_id] for every run
    bracket: Optional[list]
    log: Annotated[list, operator.add]
    done: bool
    answer: Optional[dict]


def met(row, criterion):
    """Whether one sweep row meets the criterion (a field equal to a value, or above / below a number)."""
    v = row.get(criterion["field"])
    if v is None:
        return False
    if "equals" in criterion:
        return v == criterion["equals"]
    if "above" in criterion:
        return float(v) > float(criterion["above"])
    if "below" in criterion:
        return float(v) < float(criterion["below"])
    raise ValueError("criterion needs equals, above or below")


def _json(result: Any) -> dict:
    """The JSON a SpinMate tool returned, from the content blocks of the MCP adapter."""
    if isinstance(result, (list, tuple)):
        for block in result:
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if text:
                return json.loads(text)
    if isinstance(result, str):
        return json.loads(result)
    return result


def build(tools: dict, checkpointer=None):
    """The graph. tools: the MCP tools by name (langchain_mcp_adapters)."""

    async def call(name, **args):
        return _json(await tools[name].ainvoke(args))

    def plan(s: Search):
        n = s.get("points", 7)
        lo, hi = (s["bracket"] if s.get("bracket") else (s["lo"], s["hi"]))
        if s.get("round", 0) == 0:
            values = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
        else:                                          # inside the bracket, its ends are known
            values = [lo + (hi - lo) * (k + 1) / (n + 1) for k in range(n)]
        r = s.get("round", 0) + 1
        return {"values": values, "round": r, "approved": False, "sweep_id": None,
                "log": [f"round {r}: {s['parameter']} from {values[0]:.4g} to {values[-1]:.4g}, {n} runs"]}

    async def start(s: Search):
        out = await call("start_sweep", parameters_text=s["parameters_text"], parameter=s["parameter"],
                         values=s["values"], engine=s.get("engine", "macrospin"),
                         label=f"threshold {s['parameter']} round {s['round']}", confirmed=bool(s.get("approved")))
        if out.get("needs_confirmation"):
            return {"sweep_id": None, "log": [f"needs approval: {out['runs']} runs, about "
                                              f"{out['estimated_wall_time_s'] / 60:.1f} min"],
                    "answer": {"pending": out}}
        if not out.get("ok"):
            return {"done": True, "answer": {"error": out.get("error")}, "log": [f"error: {out.get('error')}"]}
        return {"sweep_id": out["sweep_id"], "log": [f"started {out['sweep_id']} ({out['runs']} runs)"]}

    def after_start(s: Search):
        if s.get("done"):
            return END
        return "wait" if s.get("sweep_id") else "approve"

    def approve(s: Search):
        pending = (s.get("answer") or {}).get("pending", {})
        yes = interrupt({"question": f"Start {pending.get('runs')} runs, about "
                                     f"{pending.get('estimated_wall_time_s', 0) / 60:.1f} min?", **pending})
        if not yes:
            return {"done": True, "answer": {"stopped": "not approved"}, "log": ["not approved: stopped"]}
        return {"approved": True, "answer": None, "log": ["approved"]}

    def after_approve(s: Search):
        return END if s.get("done") else "start"

    async def wait(s: Search):
        info = await call("wait_for_sweep", sweep_id=s["sweep_id"], timeout_s=120)
        counts = info.get("status_counts", {})
        busy = counts.get("queued", 0) + counts.get("running", 0)
        return {"log": [f"{s['sweep_id']}: {counts}"], "answer": {"rows": info.get("rows", []), "busy": busy}}

    def after_wait(s: Search):
        return "wait" if (s.get("answer") or {}).get("busy") else "analyse"

    def analyse(s: Search):
        rows = [r for r in (s.get("answer") or {}).get("rows", []) if r.get("status") == "done"]
        new = [[float(r["value"]), met(r, s["criterion"]), r["run_id"]] for r in rows]
        pts = sorted({(v, ok) for v, ok, _ in (s.get("tried", []) + new)})
        flips = [(a, b) for (a, oa), (b, ob) in zip(pts, pts[1:]) if oa != ob]
        out = {"tried": new}
        if not flips:
            out.update(done=True, answer={"bracket": None, "note": "the criterion does not change in this range"},
                       log=["no change of the criterion in the range: stopped"])
            return out
        lo, hi = flips[0]
        width, centre = hi - lo, abs(lo + hi) / 2
        out["bracket"] = [lo, hi]
        msg = f"bracket [{lo:.4g}, {hi:.4g}]" + (f", {len(flips)} changes (only the first is followed)" if len(flips) > 1 else "")
        narrow = centre > 0 and width <= s.get("rel_width", 0.1) * centre
        if narrow or s["round"] >= s.get("max_rounds", 4):
            out.update(done=True, answer={"bracket": [lo, hi], "centre": (lo + hi) / 2, "rounds": s["round"],
                                          "reason": "narrow enough" if narrow else "max rounds"})
        out["log"] = [msg]
        return out

    def after_analyse(s: Search):
        return END if s.get("done") else "plan"

    g = StateGraph(Search)
    for name, fn in (("plan", plan), ("start", start), ("approve", approve), ("wait", wait), ("analyse", analyse)):
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "start")
    g.add_conditional_edges("start", after_start, ["wait", "approve", END])
    g.add_conditional_edges("approve", after_approve, ["start", END])
    g.add_conditional_edges("wait", after_wait, ["wait", "analyse"])
    g.add_conditional_edges("analyse", after_analyse, ["plan", END])
    return g.compile(checkpointer=checkpointer)
