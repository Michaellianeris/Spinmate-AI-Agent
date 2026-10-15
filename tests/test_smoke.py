"""Smoke tests: parameter parsing, a short real run, the HTTP API and the agent loop (mocked Claude)."""
import json
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("WORKSPACE", str(tmp_path_factory.mktemp("ws")))
    monkeypatch_module.setenv("MAX_PARALLEL", "2")
    from fastapi.testclient import TestClient
    from app.server import app
    return TestClient(app)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def wait_done(client, run_id, timeout=60):
    for _ in range(timeout * 2):
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.5)
    raise TimeoutError(run_id)


def test_examples_are_valid():
    from app import params
    for f in (ROOT / "examples").glob("*.txt"):
        assert params.validate(f.read_text())["ok"], f.name


def test_parser_errors_match_original():
    from app import params
    assert "invalid float value" in params.validate("--Ku=abc")["error"]
    assert "unrecognized arguments" in params.validate("--nope=1")["error"]


def test_run_and_api(client):
    out = client.post("/api/runs", json={"parameters_text": "--t=0.2e-9 --SOT_DC_Amp=0", "label": "t"}).json()
    r = wait_done(client, out["run_id"])
    assert r["status"] == "done"
    assert r["summary"]["points"] == 2000
    assert client.get(f"/api/runs/{r['id']}/files/Two_Spin_Dynamics.png").status_code == 200
    assert client.get(f"/api/runs/{r['id']}/files/meta.json").status_code == 404


def test_upload_and_chat_without_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    up = client.post("/api/files", files={"file": ("c.txt", b"--t=1e-10", "text/plain")}).json()
    assert up["check"]["ok"]
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401


def test_agent_loop_with_fake_claude(client):
    from app import agent

    class Block:
        def __init__(self, **kw): self.__dict__.update(kw)
        def model_dump(self, exclude_none=True): return dict(self.__dict__)

    replies = iter([
        ("tool_use", [Block(type="tool_use", id="a", name="start_simulation",
                            input={"parameters_text": "--t=0.1e-9 --SOT_DC_Amp=0"})]),
        ("end_turn", [Block(type="text", text="started")]),
    ])

    class Fake:
        def __init__(self, api_key):
            self.messages = types.SimpleNamespace(
                create=lambda **kw: types.SimpleNamespace(**dict(zip(("stop_reason", "content"), next(replies)))))

    real = agent.anthropic.Anthropic
    agent.anthropic.Anthropic = Fake
    try:
        msgs, events = agent.chat([{"role": "user", "content": "go"}], api_key="k")
    finally:
        agent.anthropic.Anthropic = real
    assert events[0]["tool"] == "start_simulation" and events[0]["run_id"].startswith("r")
    result = json.loads(msgs[2]["content"][0]["content"])
    assert result["ok"]
    assert wait_done(client, result["run_id"])["status"] == "done"


def test_openai_agent_loop_translates_tools_and_usage(client):
    from app import agent, openai_provider

    replies = iter([
        {"output": [{"type": "function_call", "call_id": "call_1", "name": "start_simulation",
                     "arguments": json.dumps({"parameters_text": "--t=0.1e-9 --SOT_DC_Amp=0"})}],
         "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "started"}]}],
         "usage": {"input_tokens": 12, "output_tokens": 3}},
    ])
    seen = []

    def fake_create(model, instructions, input_items, tools, api_key=None, max_tokens=4096, timeout=90):
        seen.append((model, input_items))
        return next(replies)

    real = openai_provider.create
    openai_provider.create = fake_create
    try:
        events = list(agent.chat_stream([{"role": "user", "content": "go"}], api_key="k",
                                        model="gpt-test", provider="openai"))
    finally:
        openai_provider.create = real
    assert [e["type"] for e in events] == ["tool_start", "tool_done", "text", "usage", "done"]
    assert events[3]["input"] == 22 and events[3]["output"] == 5
    messages = events[-1]["messages"]
    assert messages[1]["content"][0]["type"] == "tool_use"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert any(i.get("type") == "function_call_output" for i in seen[1][1])
    result = json.loads(messages[2]["content"][0]["content"])
    assert wait_done(client, result["run_id"])["status"] == "done"


def test_models_endpoint_combines_providers(client):
    from app import agent, openai_provider

    real_agent, real_openai = agent.list_models, openai_provider.list_models
    agent.list_models = lambda key=None: ([{"id": "claude-x", "name": "Claude X", "provider": "anthropic"}], "api", None)
    openai_provider.list_models = lambda key=None: ([{"id": "gpt-x", "name": "gpt-x", "provider": "openai"}], "api", None)
    try:
        data = client.get("/api/models").json()
    finally:
        agent.list_models, openai_provider.list_models = real_agent, real_openai
    assert {m["provider"] for m in data["models"]} == {"anthropic", "openai"}
    assert data["sources"] == {"anthropic": "api", "openai": "api"}


def test_series_endpoint_downsamples_and_zooms(client):
    out = client.post("/api/runs", json={"parameters_text": "--t=0.3e-9 --SOT_DC_Amp=0"}).json()
    run = wait_done(client, out["run_id"])
    s = client.get(f"/api/runs/{run['id']}/series?points=100").json()
    assert s["n_total"] == 3000 and s["n_returned"] <= 100
    assert len(s["t_ns"]) == len(s["m1z"]) == len(s["m2x"])
    z = client.get(f"/api/runs/{run['id']}/series?t0_ns=0.1&t1_ns=0.2&points=5000").json()
    assert 0.099 <= z["t_ns"][0] <= 0.101 and z["n_returned"] == z["n_window"]
    assert client.get("/api/runs/r9999/series").status_code == 404


def test_chat_streams_events_in_order(client):
    from app import agent

    class Block:
        def __init__(self, **kw): self.__dict__.update(kw)
        def model_dump(self, exclude_none=True): return dict(self.__dict__)

    replies = iter([
        ("tool_use", [Block(type="text", text="Checking."),
                      Block(type="tool_use", id="a", name="validate_parameters",
                            input={"parameters_text": "--t=1e-10"})]),
        ("end_turn", [Block(type="text", text="Valid.")]),
    ])

    class Fake:
        def __init__(self, api_key):
            self.messages = types.SimpleNamespace(
                create=lambda **kw: types.SimpleNamespace(**dict(zip(("stop_reason", "content"), next(replies)))))

    real = agent.anthropic.Anthropic
    agent.anthropic.Anthropic = Fake
    try:
        with client.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": "go"}]},
                           headers={"x-api-key": "k"}) as r:
            assert r.status_code == 200
            events = [json.loads(line) for line in r.iter_lines() if line]
    finally:
        agent.anthropic.Anthropic = real
    assert [e["type"] for e in events] == ["model", "text", "tool_start", "tool_done", "text", "usage", "done"]
    assert events[0]["provider"] == "anthropic"
    assert events[-1]["messages"][-1]["role"] == "assistant"


def test_chat_falls_back_to_the_next_provider(client):
    from app import agent

    seen = []

    def fake_stream(messages, api_key=None, model=None, mode=None, name=None, defaults_text=None, provider="anthropic", approved=False):
        seen.append((provider, model))
        if provider == "anthropic":
            yield {"type": "error", "status": 429, "error": "no credit"}
            return
        yield {"type": "text", "text": "ok"}
        yield {"type": "done", "messages": messages}

    real = agent.chat_stream
    agent.chat_stream = fake_stream
    try:
        with client.stream("POST", "/api/chat", headers={"x-api-key": "a", "x-openai-api-key": "o"},
                           json={"messages": [{"role": "user", "content": "go"}], "provider": "anthropic",
                                 "model": "claude-haiku-4-5", "fallbacks": [{"provider": "openai", "model": "gpt-5-nano"}]}) as r:
            events = [json.loads(line) for line in r.iter_lines() if line]
    finally:
        agent.chat_stream = real
    assert seen == [("anthropic", "claude-haiku-4-5"), ("openai", "gpt-5-nano")]
    assert {k: events[0][k] for k in ("type", "provider", "model")} == {"type": "model", "provider": "openai", "model": "gpt-5-nano"}
    assert events[0]["reason"].startswith("fallback")
    assert events[-1]["type"] == "done"


def test_continue_and_combine_match_one_long_run(client):
    """0.2 ns + 0.2 ns continued (AC field on) must equal one 0.4 ns run: time does not restart."""
    import numpy as np
    from app import simulations as S
    drive = "--SOT_DC_Amp=0 --flag3 --H_Amp=0.05 --Hex_AC=1,0,0 --Fr=20e9 --A0=0.248e-12"
    one = client.post("/api/runs", json={"parameters_text": f"--t=0.4e-9 {drive}"}).json()["run_id"]
    first = client.post("/api/runs", json={"parameters_text": f"--t=0.2e-9 {drive}"}).json()["run_id"]
    assert wait_done(client, first)["status"] == "done"
    cont = client.post(f"/api/runs/{first}/continue", json={}).json()
    assert cont["parent_run"] == first and abs(cont["t_start_ns"] - 0.2) < 1e-9
    second = wait_done(client, cont["run_id"])
    assert second["status"] == "done" and second["summary"]["t_offset_s"] > 0
    assert wait_done(client, one)["status"] == "done"

    comb = client.post("/api/runs/combine", json={"run_id": second["id"]}).json()
    assert comb["chain"] == [first, second["id"]] and abs(comb["t_end_ns"] - 0.4) < 1e-9
    a = np.loadtxt(S.RUNS / comb["run_id"] / "output1.dat")
    ref = np.loadtxt(S.RUNS / one / "output1.dat")
    common, ia, ir = np.intersect1d(np.round(a[:, 0] * 1e15), np.round(ref[:, 0] * 1e15), return_indices=True)
    assert len(common) >= len(ref) - 1
    assert np.max(np.abs(a[ia, 1:] - ref[ir, 1:])) < 1e-9
    info = client.get(f"/api/runs/{comb['run_id']}").json()
    assert info["kind"] == "combined" and "Two_Spin_Dynamics.png" in info["files"]
    assert client.get(f"/api/runs/{comb['run_id']}/export.csv").status_code == 200


def test_combine_needs_two_runs(client):
    assert client.post("/api/runs/combine", json={"run_ids": ["r0001"]}).status_code == 400
