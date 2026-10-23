"""The LangGraph example (examples/langgraph): the threshold-search graph against stand-in MCP tools.
Skipped unless langgraph is installed (it lives in its own environment, see examples/langgraph/requirements.txt)."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("langgraph")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples" / "langgraph"))
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

import spinmate_graph as G  # noqa: E402

THRESHOLD = 3.3


class Tool:
    """A stand-in for one MCP tool: answers like SpinMate, from a known threshold."""
    def __init__(self, name, server):
        self.name, self.server = name, server

    async def ainvoke(self, args):
        return [{"type": "text", "text": json.dumps(self.server.call(self.name, args))}]


class Server:
    def __init__(self, confirm_above=99):
        self.sweeps, self.confirm_above = {}, confirm_above

    def call(self, name, a):
        if name == "start_sweep":
            if len(a["values"]) > self.confirm_above and not a.get("confirmed"):
                return {"ok": False, "needs_confirmation": True, "runs": len(a["values"]), "estimated_wall_time_s": 600}
            sid = f"s{len(self.sweeps) + 1:04d}"
            self.sweeps[sid] = [{"run_id": f"r{sid}{i}", "value": v, "status": "done",
                                 "order_final": "FM" if v > THRESHOLD else "AFM"} for i, v in enumerate(a["values"])]
            return {"ok": True, "sweep_id": sid, "runs": len(a["values"])}
        if name == "wait_for_sweep":
            return {"status_counts": {"done": len(self.sweeps[a["sweep_id"]])}, "rows": self.sweeps[a["sweep_id"]]}
        raise KeyError(name)


def tools(server):
    return {n: Tool(n, server) for n in ("start_sweep", "wait_for_sweep")}


START = {"parameters_text": "--t=1e-9\n", "parameter": "A0", "lo": 0.0, "hi": 10.0,
         "criterion": {"field": "order_final", "equals": "FM"}, "points": 6, "max_rounds": 4, "rel_width": 0.05}


def test_bracket_narrows_to_the_threshold():
    g = G.build(tools(Server()), MemorySaver())
    out = asyncio.run(g.ainvoke(START, {"configurable": {"thread_id": "a"}}))
    lo, hi = out["answer"]["bracket"]
    assert lo < THRESHOLD <= hi and hi - lo <= 0.05 * THRESHOLD and out["answer"]["reason"] == "narrow enough"


def test_large_sweeps_wait_for_approval():
    srv = Server(confirm_above=3)
    g = G.build(tools(srv), MemorySaver())
    cfg = {"configurable": {"thread_id": "b"}}
    asyncio.run(g.ainvoke(START, cfg))
    state = g.get_state(cfg)
    assert state.next == ("approve",) and not srv.sweeps            # stopped before any run
    asks = 1
    while g.get_state(cfg).next:                                     # every round of 6 runs asks again
        out = asyncio.run(g.ainvoke(Command(resume=True), cfg))
        asks += 1
    assert asks == len(srv.sweeps) + 1 and out["answer"]["bracket"]
    g2 = G.build(tools(Server(confirm_above=3)), MemorySaver())
    asyncio.run(g2.ainvoke(START, {"configurable": {"thread_id": "c"}}))
    out = asyncio.run(g2.ainvoke(Command(resume=False), {"configurable": {"thread_id": "c"}}))
    assert out["answer"] == {"stopped": "not approved"}


def test_no_change_in_the_range():
    out = asyncio.run(G.build(tools(Server()), MemorySaver()).ainvoke({**START, "lo": 5.0, "hi": 9.0},
                                                                      {"configurable": {"thread_id": "d"}}))
    assert out["answer"]["bracket"] is None


def test_criterion():
    assert G.met({"neel_z_switches": 2}, {"field": "neel_z_switches", "above": 0})
    assert not G.met({"phase": "uniform"}, {"field": "phase", "equals": "skyrmion"})
