"""Gemini provider: history translation, thought signatures, the tool loop, routing and prices."""
import json

from app import agent, gemini_provider as G, pricing, router


def test_history_round_trip_keeps_signatures_and_names():
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": [{"type": "text", "text": "running"},
                                              {"type": "tool_use", "id": "t1", "name": "get_run", "input": {"run_id": "r1"},
                                               "gemini_signature": "SIG"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "text", "text": '{"ok": true}'},
                {"type": "image", "source": {"media_type": "image/png", "data": "AAA"}}]}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c9", "name": "list_runs", "input": {}}]}]
    c = G.to_contents(msgs)
    assert [x["role"] for x in c] == ["user", "model", "user", "model"]
    call = c[1]["parts"][1]
    assert call["functionCall"]["name"] == "get_run" and call["thoughtSignature"] == "SIG"
    assert c[2]["parts"][0]["functionResponse"] == {"name": "get_run", "response": {"ok": True}}
    assert c[2]["parts"][1]["inlineData"]["data"] == "AAA"
    # a call made by another provider has no signature: the documented skip value is sent
    assert c[3]["parts"][0]["thoughtSignature"] == G.NO_SIGNATURE
    clean = G.strip(msgs)
    assert "gemini_signature" not in clean[1]["content"][1] and msgs[1]["content"][1]["gemini_signature"] == "SIG"


def test_output_blocks_and_usage():
    resp = {"candidates": [{"content": {"parts": [
        {"text": "thinking", "thought": True},
        {"functionCall": {"name": "validate_parameters", "args": {"parameters_text": "--t=1e-11"}}, "thoughtSignature": "S1"},
        {"text": "checking"}]}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 7, "thoughtsTokenCount": 5,
                          "cachedContentTokenCount": 40}}
    blocks, calls = G.output_blocks(resp, step=2)
    assert [b["type"] for b in blocks] == ["tool_use", "text"]
    assert calls[0]["id"] == "gm_2_1" and calls[0]["gemini_signature"] == "S1"
    assert G.usage(resp) == (100, 12) and G.cached_tokens(resp) == 40


def test_gemini_agent_loop(monkeypatch):
    replies = iter([
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "validate_parameters",
                                                                 "args": {"parameters_text": "--t=1e-11\n"}},
                                                "thoughtSignature": "S"}]}}],
         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2}},
        {"candidates": [{"content": {"parts": [{"text": "valid"}]}}],
         "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3}},
    ])
    seen = []

    def fake(model, instructions, contents, tools, api_key=None, max_tokens=4096, timeout=90):
        seen.append(contents)
        return next(replies)
    monkeypatch.setattr(G, "create", fake)
    events = list(agent.chat_stream([{"role": "user", "content": "check"}], api_key="k",
                                    model="gemini-2.5-flash", provider="gemini"))
    assert [e["type"] for e in events] == ["tool_start", "tool_done", "text", "usage", "done"]
    assert events[3]["input"] == 22 and events[3]["output"] == 5 and events[3]["cost_usd"] > 0
    second = seen[1]
    assert second[1]["parts"][0]["thoughtSignature"] == "S"
    assert json.loads(json.dumps(second[2]["parts"][0]["functionResponse"]["response"]))["ok"] is True


def test_list_models_filters_to_chat_models(monkeypatch):
    def fake(path, key, method="GET", body=None, timeout=90):
        return {"models": [
            {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemma-3-27b-it", "supportedGenerationMethods": ["generateContent"]}]}
    monkeypatch.setattr(G, "_request", fake)
    models, source, err = G.list_models("key")
    assert source == "api" and [m["id"] for m in models] == ["gemini-2.5-pro"] and models[0]["provider"] == "gemini"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    models, source, _ = G.list_models(None)
    assert source == "configured" and all(m["provider"] == "gemini" for m in models)


def test_gemini_prices_and_routing():
    assert pricing.price("gemini-2.5-flash-lite")[0] == 0.10
    assert pricing.price("gemini-3.1-pro-preview")[1] == 12
    assert pricing.price("gemini-3-flash-preview")[0] == 0.50
    assert pricing.is_estimate("gemini-9-flash")
    assert router.capability("gemini", "gemini-3.1-pro-preview") > router.capability("gemini", "gemini-3-flash-preview") \
        > router.capability("gemini", "gemini-3.1-flash-lite")
    zeus = [{"provider": "gemini", "model": "gemini-2.5-pro"}, {"provider": "gemini", "model": "gemini-3.1-pro-preview"},
            {"provider": "anthropic", "model": "claude-opus-5-5"}]
    ranked, why = router.rank(zeus, "zeus", [{"role": "user", "content": "sweep A0 and explain the threshold"}], "deep")
    assert ranked[0]["model"] == "claude-opus-5-5" and ranked[1]["model"] == "gemini-3.1-pro-preview"


def test_chat_endpoint_accepts_gemini(client_http=None):
    from fastapi.testclient import TestClient
    from app.server import app
    r = TestClient(app).post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "provider": "gemini",
                                                "model": "gemini-2.5-flash"})
    # no key anywhere: a clear 401 that names the Gemini variable
    if r.status_code == 401:
        assert "GEMINI_API_KEY" in r.json()["error"]
    else:
        assert r.status_code == 200
