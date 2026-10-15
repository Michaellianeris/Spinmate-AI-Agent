"""Compare, figures, reports, run annotations, the confirmation gate and pricing."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def two_runs():
    from app import simulations as S
    ids = []
    for a0 in ("0.248e-12", "-0.248e-12"):
        r = S.start_run(f"--A0={a0}\n--t=1e-10\n", label=f"feat {a0}")
        assert r["ok"], r
        ids.append(r["run_id"])
    for rid in ids:
        assert S.wait_for_run(rid, 60)["status"] == "done"
    return ids


@pytest.fixture(scope="module")
def http():
    from app.server import app
    return TestClient(app)


def test_compare_lists_differing_parameters(two_runs, http):
    out = http.get("/api/compare", params={"ids": ",".join(two_runs)}).json()
    assert out["ok"] and out["run_ids"] == two_runs
    assert set(out["differing_parameters"]) == {"A0"}
    orders = [r["metrics"]["order_final"] for r in out["runs"]]
    assert orders == ["FM", "AFM"]
    assert http.get("/api/compare", params={"ids": two_runs[0]}).status_code == 400


@pytest.mark.parametrize("fmt,magic", [("pdf", b"%PDF"), ("png", b"\x89PNG"), ("svg", b"<?xml")])
def test_figure_formats(two_runs, http, fmt, magic):
    r = http.post("/api/figure", json={"run_ids": two_runs, "quantities": ["nz", "dot"], "format": fmt,
                                       "style": "ieee", "t0_ns": 0, "t1_ns": 0.05})
    assert r.status_code == 200 and r.content.startswith(magic)
    assert "attachment" in r.headers["content-disposition"]


def test_figure_rejects_empty_window(two_runs, http):
    r = http.post("/api/figure", json={"run_ids": two_runs[:1], "t0_ns": 5, "t1_ns": 6})
    assert r.status_code == 400


def test_report_html_and_zip(two_runs, http):
    out = http.post("/api/reports", json={"run_ids": two_runs, "title": "Test report",
                                          "notes": "**FM** for A0 > 0.\n\n- second point"}).json()
    assert out["ok"]
    page = http.get(out["html_url"]).text
    assert "Test report" in page and "<b>FM</b>" in page and "data:image/png;base64" in page
    assert "Parameters that differ between runs" in page
    z = http.get(out["zip_url"])
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert http.get("/api/reports/../../etc.html").status_code == 404


def test_star_and_tags(two_runs, http):
    r = http.post(f"/api/runs/{two_runs[0]}/meta", json={"starred": True, "tags": ["paper", "ferh", "paper"]}).json()
    assert r["starred"] is True and r["tags"] == ["ferh", "paper"]
    run = http.get(f"/api/runs/{two_runs[0]}").json()
    assert run["starred"] and run["tags"] == ["ferh", "paper"]
    assert http.post("/api/runs/r9999/meta", json={"starred": True}).status_code == 404


def test_large_sweep_waits_for_confirmation(monkeypatch):
    from app import simulations as S
    from app import tools
    monkeypatch.setattr(tools, "CONFIRM_RUNS", 3)
    args = {"parameters_text": "--t=1e-11\n", "parameter": "A0", "values": [1e-13, 2e-13, 3e-13, 4e-13]}
    before = len(list(S.SWEEPS.iterdir()))
    content, event = tools.call("start_sweep", args)
    out = json.loads(content)
    assert out["needs_confirmation"] and out["runs"] == 4 and event["needs_confirmation"]
    assert len(list(S.SWEEPS.iterdir())) == before          # nothing started
    content, _ = tools.call("start_sweep", args, approved=True)
    out = json.loads(content)
    assert out["ok"] and out["runs"] == 4
    S.wait_for_sweep(out["sweep_id"], 60)


def test_pricing_prefers_the_longest_prefix():
    from app import pricing
    assert pricing.price("claude-sonnet-5-5") == (2, 10, 2.5, 0.10)
    assert pricing.price("claude-sonnet-5") == (2, 10, 2.5, 0.20)
    assert pricing.price("gpt-4.1-mini-2025-04-14")[0] == 0.40
    assert pricing.price("unknown-model") is None
    # 1M uncached input + 1M output on Sonnet 5.5 = 2 + 10 USD
    assert pricing.cost_usd("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)


def test_compare_and_report_tools(two_runs):
    from app import tools
    content, event = tools.call("compare_runs", {"run_ids": two_runs})
    assert json.loads(content)["ok"] and event["run_ids"] == two_runs
    content, event = tools.call("make_report", {"run_ids": two_runs, "title": "t", "notes": "n"})
    out = json.loads(content)
    assert out["ok"] and event["html_url"] == out["html_url"]


def test_chat_approval_reaches_the_tools(http, monkeypatch):
    import types
    from app import agent, tools
    monkeypatch.setattr(tools, "CONFIRM_RUNS", 1)

    class Block:
        def __init__(self, **kw): self.__dict__.update(kw)
        def model_dump(self, exclude_none=True): return dict(self.__dict__)

    def fake_client(api_key):
        def create(**kw):
            last = kw["messages"][-1]["content"]
            if isinstance(last, str):
                return types.SimpleNamespace(stop_reason="tool_use", usage=None, content=[Block(
                    type="tool_use", id="a", name="start_sweep",
                    input={"parameters_text": "--t=1e-11\n", "parameter": "A0", "values": [1e-13, 2e-13]})])
            return types.SimpleNamespace(stop_reason="end_turn", usage=None, content=[Block(type="text", text="ok")])
        return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))

    monkeypatch.setattr(agent.anthropic, "Anthropic", fake_client)

    def run(approve):
        with http.stream("POST", "/api/chat", headers={"x-api-key": "k"},
                         json={"messages": [{"role": "user", "content": "sweep"}], "approve": approve}) as r:
            events = [json.loads(line) for line in r.iter_lines() if line]
        return next(e["event"] for e in events if e["type"] == "tool_done")

    held = run(False)
    assert held["needs_confirmation"] and held["runs"] == 2
    started = run(True)
    assert started.get("sweep_id") and not started.get("needs_confirmation")


def test_stored_figure_and_card_files(two_runs, http):
    out = http.post("/api/figures", json={"run_ids": two_runs, "quantities": ["nz"], "style": "nature"}).json()
    assert out["ok"] and out["figure"].startswith("fig")
    assert http.get(out["png_url"]).content.startswith(b"\x89PNG")
    pdf = http.get(out["pdf_url"])
    assert pdf.content.startswith(b"%PDF") and "attachment" in pdf.headers["content-disposition"]
    assert http.get(out["svg_url"]).status_code == 200
    assert http.get(f"/api/figures/{out['figure']}.json").json()["run_ids"] == two_runs
    from app import tools
    content, event = tools.call("make_figure", {"run_ids": two_runs[:1], "quantities": ["m1z", "m2z"]})
    assert json.loads(content)["ok"] and event["png_url"].endswith(".png")


def test_delete_runs(http):
    from app import simulations as S
    r = S.start_run("--t=1e-11\n", label="to delete")
    assert S.wait_for_run(r["run_id"], 60)["status"] == "done"
    out = http.post("/api/runs/delete", json={"run_ids": [r["run_id"], "r9999"]}).json()
    assert out["deleted"] == [r["run_id"]] and out["refused"][0]["run_id"] == "r9999"
    assert http.get(f"/api/runs/{r['run_id']}").status_code == 404


def test_pricing_covers_gpt5_and_families():
    from app import pricing
    assert pricing.price("gpt-5.5") == (5, 30, 5, 0.50)
    assert pricing.price("gpt-5.4-mini-2026-03-01")[0] == 0.75
    assert pricing.price("gpt-5.2")[0] == 1.75 and pricing.price("gpt-5")[0] == 1.25   # gpt-5 does not price gpt-5.2
    assert pricing.is_estimate("claude-opus-9") and pricing.price("claude-opus-9")[0] == 5
    assert not pricing.is_estimate("claude-sonnet-5-5")


def test_router_prefers_the_strongest_and_saves_on_simple_requests():
    from app import router
    A = lambda *m: [{"provider": "anthropic", "model": x} for x in m]
    O = lambda *m: [{"provider": "openai", "model": x} for x in m]
    msg = lambda t: [{"role": "user", "content": t}]
    zeus = A("claude-opus-4-8", "claude-opus-5-5") + O("o3", "gpt-5.5-pro")
    ranked, why = router.rank(zeus, "zeus", msg("hi"), "fast")
    assert ranked[0]["model"] == "claude-opus-5-5" and "gpt-5.5-pro" not in [c["model"] for c in ranked]
    assert why.startswith("auto:")
    dio = A("claude-sonnet-5-5") + O("gpt-5.5", "gpt-5.4")
    hard = router.rank(dio, "dionysus", msg("find the smallest current with a sweep and explain why"), "deep")[0]
    assert hard[0]["model"] == "gpt-5.5"                       # strongest when the request needs it
    easy = router.rank(dio, "dionysus", msg("hi"), "fast")[0]
    assert easy[0]["model"] != "gpt-5.5"                       # cheaper model enough for a greeting
    assert router.complexity(msg("hi"), "fast") < router.complexity(msg("sweep and compare"), "deep")


def test_chat_auto_routes_by_score(http, monkeypatch):
    from app import agent
    seen = []

    def fake(messages, api_key=None, model=None, provider="anthropic", **kw):
        seen.append((provider, model))
        yield {"type": "done", "messages": messages}

    monkeypatch.setattr(agent, "chat_stream", fake)
    with http.stream("POST", "/api/chat", headers={"x-api-key": "a", "x-openai-api-key": "o"},
                     json={"messages": [{"role": "user", "content": "explain and compare the runs"}], "mode": "deep",
                           "auto": True, "god": "zeus", "provider": "openai", "model": "o3",
                           "candidates": [{"provider": "openai", "model": "o3"},
                                          {"provider": "anthropic", "model": "claude-opus-5-5"}]}) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    model = next(e for e in events if e["type"] == "model")
    assert seen == [("anthropic", "claude-opus-5-5")] and model["auto"] and model["reason"].startswith("auto:")


def test_transcribe_endpoint(http, monkeypatch):
    from app import openai_provider
    got = {}

    def fake(audio, filename, mime, language, api_key, prompt):
        got.update(size=len(audio), mime=mime, language=language, key=api_key)
        return "run AFM to FM"

    monkeypatch.setattr(openai_provider, "transcribe", fake)
    r = http.post("/api/transcribe?language=el", headers={"x-openai-api-key": "o"},
                  files={"file": ("speech.webm", b"\x1aE\xdf\xa3" + b"0" * 2000, "audio/webm")})
    assert r.json() == {"text": "run AFM to FM"}
    assert got == {"size": 2004, "mime": "audio/webm", "language": "el", "key": "o"}
    assert http.post("/api/transcribe", files={"file": ("s.webm", b"", "audio/webm")}).status_code == 400


def test_report_math_is_mathml():
    from app import mathtext, reports
    page = reports.md_to_html(r"**$n_z$ flips**, m_1z = -m_2z, A0 = 0.248e-12 J/m, `SOT_DC_Amp` and settle_time_s"
                              "\n\n$$f = \\frac{\\gamma}{2\\pi}\n\\sqrt{2H_E H_K}$$")
    assert "<b><math" in page and page.count("<math") == 5 and 'display="block"' in page
    assert "<code>SOT_DC_Amp</code>" in page and "settle_time_s" in page          # identifiers untouched
    assert "$" not in page
    assert mathtext.to_markdown("n_z and --A0=1e-12") == "$n_{z}$ and --A0=1e-12"
    assert "<code>" in mathtext.mathml(r"\frac{")                                  # bad LaTeX does not break the page
