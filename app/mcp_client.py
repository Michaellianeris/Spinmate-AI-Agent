"""The web app's agent reaches the SpinMate tools through the MCP server, like any other agent.
The server runs in this process (in memory), so a call costs a few milliseconds."""
import base64
import json

import anyio
from mcp import Client

import spinmate_mcp
from spinmate_core import tools

_server = spinmate_mcp.build()
_defs = None
PLOT_TOOLS = ("get_run", "get_sweep")   # the model sees images only from these; the page shows figures itself


async def _list():
    async with Client(_server) as c:
        return (await c.list_tools()).tools


async def _call(name, args):
    async with Client(_server) as c:
        return await c.call_tool(name, args)


def tool_defs():
    """Tool definitions for the model, without "confirmed": in the web app only the Run button approves."""
    global _defs
    if _defs is None:
        out = []
        for t in anyio.run(_list):
            schema = json.loads(json.dumps(t.input_schema))
            schema.get("properties", {}).pop("confirmed", None)
            out.append({"name": t.name, "description": t.description, "input_schema": schema})
        _defs = out
    return _defs


def call(name, args, approved=False):
    """Same contract as spinmate_core.tools.call: (content for the model, event for the page)."""
    args = {k: v for k, v in (args or {}).items() if k != "confirmed"}
    if name in spinmate_mcp.server.GATED:
        args["confirmed"] = bool(approved)
    result = anyio.run(_call, name, args)
    data = result.structured_content
    texts = [c.text for c in result.content if c.type == "text"]
    if isinstance(data, dict):
        if data.get("needs_confirmation"):
            data = {**data, "error": tools.confirmation_message(data["runs"], data["estimated_wall_time_s"])}
        text = json.dumps(data, default=str)
    else:
        text = "\n".join(texts)
    images = [c for c in result.content if c.type == "image"] if name in PLOT_TOOLS else []
    if images:
        content = [{"type": "text", "text": text}] + [
            {"type": "image", "source": {"type": "base64", "media_type": i.mime_type, "data": i.data}} for i in images]
    else:
        content = text
    return content, tools.event_for(name, data if isinstance(data, dict) else {})
