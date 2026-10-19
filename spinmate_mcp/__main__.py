"""python -m spinmate_mcp            stdio, for Claude Desktop, Claude Code and other local agents
python -m spinmate_mcp --http     streamable HTTP on 127.0.0.1:8422/mcp (the web app also serves it at /mcp)"""
import argparse

import anyio


def main():
    ap = argparse.ArgumentParser(prog="spinmate-mcp")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8422)
    a = ap.parse_args()

    from .server import build
    server = build()
    if a.http:
        import uvicorn
        uvicorn.run(server.streamable_http_app(host=a.host), host=a.host, port=a.port, log_level="warning")
        return

    from mcp.server.stdio import stdio_server

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    anyio.run(run)


if __name__ == "__main__":
    main()
