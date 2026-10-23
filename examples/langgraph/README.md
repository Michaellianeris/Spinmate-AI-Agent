# SpinMate with LangGraph

A threshold search as a [LangGraph](https://langchain-ai.github.io/langgraph/) graph on top of SpinMate's MCP server.
SpinMate's own agent stays as it is; this is a second way in, for agent workflows you build yourself.

```
plan → start_sweep → (approve) → wait_for_sweep → analyse → plan … → done
```

- **A threshold search as a graph.** Sweep the parameter, wait, classify each run with the criterion, narrow the
  bracket where it changes, up to 4 rounds or until the bracket is narrower than 10 % of its centre.
- **Checkpointing.** The state is saved in SQLite after every step. Stop the script, restart the computer, run
  the same command with the same `--thread`: it goes on from the last step, for example waiting again for a sweep
  of 100 ns runs that kept running on the server.
- **Human in the loop.** A sweep that SpinMate thinks is large (more than `CONFIRM_RUNS` runs or `CONFIRM_MINUTES`)
  stops the graph with `interrupt()`; the script asks *Start 40 runs, about 12 min? [y/N]* and resumes with the
  answer (`--yes` answers yes).

## Run it

SpinMate must be running (the web app serves MCP at `/mcp`). Use a separate environment:
`langchain-mcp-adapters` needs `mcp` < 2 and SpinMate's server uses `mcp` 2.

```bash
python -m venv .venv-langgraph && . .venv-langgraph/bin/activate
pip install -r examples/langgraph/requirements.txt
cd examples/langgraph
python threshold.py --parameter A0 --lo 0 --hi 1e-14 --criterion order_final=FM --lines="--t=2e-9" --thread a0
```

```
· round 1: A0 from 0 to 1e-14, 6 runs
· started s0001 (6 runs)
· bracket [0, 2e-15]
· round 2: A0 from 2.857e-16 to 1.714e-15, 6 runs
· bracket [1.714e-15, 2e-15]
· round 3: A0 from 1.755e-15 to 1.959e-15, 6 runs
· bracket [1.714e-15, 1.755e-15]
{"bracket": [1.714e-15, 1.755e-15], "centre": 1.735e-15, "rounds": 3, "reason": "narrow enough"}
```

The AFM/FM boundary of the default material is |Ku| = 2|A0|/l², A0 = 1.7395e-15 J/m: the graph finds 1.735e-15
in three rounds of six runs.

Other criteria: `neel_z_switches>0` (switching by a current), `phase=skyrmion` with `--engine mm2d`
(e.g. `--parameter D --lo 2e-3 --hi 5e-3`), `radius_nm<5`. `--url` points at another server
(`http://host:8421/mcp`), `--file` reads the parameters from a .txt file.

`spinmate_graph.py` builds the graph from the MCP tools, so it fits in a larger LangGraph app next to your own
nodes or a `create_react_agent` that answers free-form questions with the same tools.
`tests/test_langgraph_example.py` runs the graph against stand-in tools (skipped where langgraph is not installed).
