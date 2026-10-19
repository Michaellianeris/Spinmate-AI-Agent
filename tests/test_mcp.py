"""The MCP server: tools, resources, the confirmation gate, stdio and HTTP."""
import json
import socket
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

import spinmate_mcp
from spinmate_mcp import server as M

ROOT = Path(__file__).resolve().parent.parent


def run(fn):
    return anyio.run(fn)


def text(result):
    return json.loads(next(c.text for c in result.content if c.type == "text"))


def test_tools_and_schemas():
    async def go():
        async with Client(spinmate_mcp.build()) as c:
            assert "validate_parameters" in (c.instructions or "")
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert {"validate_parameters", "start_simulation", "wait_for_run", "get_run", "make_figure"} <= set(tools)
            assert "confirmed" in tools["start_simulation"].input_schema["properties"]
            assert "confirmed" not in tools["get_run"].input_schema["properties"]
            bad = await c.call_tool("validate_parameters", {"parameters_text": "--nonsense=1"})
            ok = await c.call_tool("validate_parameters", {"parameters_text": "--A0=0.248e-12\n--t=1e-10\n"})
            return bad, ok
    bad, ok = run(go)
    assert text(ok)["ok"] and not ok.is_error
    assert text(bad)["ok"] is False


def test_run_plot_figure_and_resources():
    async def go():
        async with Client(spinmate_mcp.build()) as c:
            started = text(await c.call_tool("start_simulation", {"parameters_text": "--A0=0.248e-12\n--t=1e-10\n",
                                                                  "label": "mcp test"}))
            rid = started["run_id"]
            done = text(await c.call_tool("wait_for_run", {"run_id": rid, "timeout_s": 60}))
            plot = await c.call_tool("get_run", {"run_id": rid, "include_plot": True})
            fig = await c.call_tool("make_figure", {"run_ids": [rid], "quantities": ["nz"]})
            res = {r.uri for r in (await c.list_resources()).resources}
            params = (await c.read_resource("spinmate://parameters")).contents[0].text
            brief = json.loads((await c.read_resource(f"spinmate://runs/{rid}")).contents[0].text)
            ptxt = (await c.read_resource(f"spinmate://runs/{rid}/parameters.txt")).contents[0].text
            return rid, done, plot, fig, res, params, brief, ptxt
    rid, done, plot, fig, res, params, brief, ptxt = run(go)
    assert done["status"] == "done" and done["summary"]["order_final"]
    assert any(c.type == "image" for c in plot.content)
    f = text(fig)
    assert f["ok"] and f["pdf_url"].startswith("http") and any(c.type == "image" for c in fig.content)
    assert {"spinmate://parameters", "spinmate://physics/scales", "spinmate://runs"} <= {str(u) for u in res}
    assert "integration step" in params
    assert brief["id"] == rid and "--A0=0.248e-12" in ptxt


def test_confirmation_gate(monkeypatch):
    from app import tools
    monkeypatch.setattr(tools, "CONFIRM_RUNS", 1)
    args = {"parameters_text": "--t=1e-11\n", "parameter": "A0", "values": [1e-13, 2e-13]}

    async def go():
        async with Client(spinmate_mcp.build()) as c:
            held = text(await c.call_tool("start_sweep", args))
            started = text(await c.call_tool("start_sweep", {**args, "confirmed": True}))
            return held, started
    held, started = run(go)
    assert held["needs_confirmation"] and "confirmed=true" in held["error"]
    assert started.get("sweep_id")


def test_stdio_transport():
    params = StdioServerParameters(command=sys.executable, args=["-m", "spinmate_mcp"], cwd=str(ROOT))

    async def go():
        async with Client(params) as c:
            return [t.name for t in (await c.list_tools()).tools]
    assert "start_simulation" in run(go)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_http_inside_the_web_app():
    import uvicorn
    from app.server import app
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)

    async def go():
        async with Client(f"http://127.0.0.1:{port}/mcp") as c:
            names = [t.name for t in (await c.list_tools()).tools]
            ok = text(await c.call_tool("validate_parameters", {"parameters_text": "--t=1e-10\n"}))
            return names, ok
    try:
        names, ok = run(go)
    finally:
        srv.should_exit = True
    assert "get_run" in names and ok["ok"]


def test_unknown_resource_is_an_error():
    with pytest.raises(ValueError):
        M.read("spinmate://nothing")


def test_physics_prompts_and_guide():
    async def go():
        async with Client(spinmate_mcp.build()) as c:
            names = {p.name for p in (await c.list_prompts()).prompts}
            got = await c.get_prompt("find_threshold", {"parameter": "SOT_DC_Amp", "behaviour": "the Néel vector switches"})
            guide = (await c.read_resource("spinmate://physics/guide")).contents[0].text
            return names, got, guide
    names, got, guide = run(go)
    assert {"design_experiment", "find_threshold", "interpret_run"} <= names
    text = got.messages[0].content.text
    assert "SOT_DC_Amp" in text and "Reading the results" in text and "Conditions: none" in text
    assert "4Ku/Ms" in guide
    with pytest.raises(ValueError):
        M.prompt_text("interpret_run", {})


def test_claude_code_files_are_up_to_date():
    from spinmate_mcp import agent_files
    for path, text in agent_files.render().items():
        assert (ROOT / path).read_text() == text, f"{path} is stale: run python -m spinmate_mcp.agent_files"
