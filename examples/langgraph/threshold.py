"""Threshold search with LangGraph over SpinMate's MCP server.

    python threshold.py --parameter A0 --lo 0 --hi 1e-14 --criterion order_final=FM \
        --lines="--t=2e-9" --thread a0-search

Start it again with the same --thread after a stop or a restart: it continues from the last checkpoint.
Large sweeps stop and ask (human in the loop); --yes answers yes for you."""
import argparse
import asyncio
import json

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from spinmate_graph import build


def criterion(text):
    for op, key in ((">", "above"), ("<", "below"), ("=", "equals")):
        if op in text:
            field, value = text.split(op, 1)
            try:
                value = float(value) if key != "equals" else json.loads(value)
            except ValueError:
                pass
            return {"field": field.strip(), key: value}
    raise SystemExit("criterion: field=value, field>number or field<number")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8421/mcp", help="SpinMate's MCP endpoint")
    ap.add_argument("--engine", default="macrospin")
    ap.add_argument("--parameter", required=True)
    ap.add_argument("--lo", type=float, required=True)
    ap.add_argument("--hi", type=float, required=True)
    ap.add_argument("--criterion", required=True, help="e.g. order_final=FM, neel_z_switches>0, phase=skyrmion")
    ap.add_argument("--lines", default="", help="parameter lines, separated by spaces or newlines")
    ap.add_argument("--file", help="a parameter .txt file")
    ap.add_argument("--points", type=int, default=7)
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--rel-width", type=float, default=0.1)
    ap.add_argument("--thread", default="threshold", help="checkpoint id: the same id resumes")
    ap.add_argument("--db", default="spinmate-langgraph.sqlite")
    ap.add_argument("--yes", action="store_true", help="approve large sweeps without asking")
    a = ap.parse_args()
    text = open(a.file).read() if a.file else "\n".join(a.lines.split()) + "\n"
    client = MultiServerMCPClient({"spinmate": {"url": a.url, "transport": "streamable_http"}})
    tools = {t.name: t for t in await client.get_tools()}
    async with AsyncSqliteSaver.from_conn_string(a.db) as saver:
        graph = build(tools, saver)
        cfg = {"configurable": {"thread_id": a.thread}}
        state = await graph.aget_state(cfg)
        if state.next:                                  # an unfinished search: go on from the checkpoint
            print(f"resuming {a.thread} at {state.next}")
            inp = None
        else:
            inp = {"parameters_text": text, "engine": a.engine, "parameter": a.parameter, "lo": a.lo, "hi": a.hi,
                   "criterion": criterion(a.criterion), "points": a.points, "max_rounds": a.rounds,
                   "rel_width": a.rel_width}
        seen = len(state.values.get("log", [])) if state.values else 0
        while True:
            async for ev in graph.astream(inp, cfg, stream_mode="values"):
                log = ev.get("log", [])
                for line in log[seen:]:
                    print("·", line)
                seen = len(log)
            state = await graph.aget_state(cfg)
            if not state.next:
                break
            ask = state.tasks[0].interrupts[0].value if state.tasks and state.tasks[0].interrupts else {}
            yes = a.yes or input(f"{ask.get('question', 'Go on?')} [y/N] ").strip().lower().startswith("y")
            inp = Command(resume=yes)
        print(json.dumps(state.values.get("answer"), indent=1))


if __name__ == "__main__":
    asyncio.run(main())
