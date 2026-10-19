"""Usage log and cost page, the admin page (only with ADMIN_TOKEN) and the page controls of 5-8."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent, mailer, usage
from app.server import app

ROOT = Path(__file__).resolve().parent.parent
http = TestClient(app)


@pytest.fixture
def tmp_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "FILE", tmp_path / "usage.jsonl")
    return tmp_path


def test_chat_answers_are_logged_per_provider(tmp_usage, monkeypatch):
    def fake_stream(messages, api_key=None, model=None, mode=None, name=None, defaults_text=None, provider="anthropic",
                    approved=False, lang=None):
        yield {"type": "text", "text": "ok"}
        yield {"type": "usage", "input": 1000, "output": 50, "cost_usd": 0.01, "model": model}
        yield {"type": "done", "messages": messages}
    monkeypatch.setattr(agent, "chat_stream", fake_stream)
    for prov, model in (("gemini", "gemini-2.5-flash"), ("openai", "gpt-5-nano")):
        with http.stream("POST", "/api/chat", headers={"x-gemini-api-key": "g", "x-openai-api-key": "o"},
                         json={"messages": [{"role": "user", "content": "hi"}], "provider": prov, "model": model,
                               "god": "hermes", "name": "Michael"}) as r:
            list(r.iter_lines())
    rows = [json.loads(x) for x in (tmp_usage / "usage.jsonl").read_text().splitlines()]
    assert [r["provider"] for r in rows] == ["gemini", "openai"] and rows[0]["name"] == "Michael"
    s = http.get("/api/usage?days=7").json()
    assert s["totals"]["gemini"]["answers"] == 1 and s["totals"]["openai"]["input"] == 1000
    assert s["days"][0]["tokens"] == 2100 and abs(s["days"][0]["cost_usd"] - 0.02) < 1e-9


def test_admin_is_off_without_a_token(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert http.get("/admin").status_code == 404
    assert http.get("/api/admin/summary", headers={"x-admin-token": "x"}).status_code == 404


def test_admin_with_token(monkeypatch, tmp_path, tmp_usage):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    monkeypatch.setattr(mailer, "ACCOUNTS", tmp_path / "accounts.json")
    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "outbox")
    (tmp_path / "accounts.json").write_text(json.dumps({"a@b.org": {"name": "A", "email": "a@b.org", "created": 1, "welcome_sent": 0}}))
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox" / "w.eml").write_text("To: a@b.org\n")
    assert http.get("/admin").status_code == 200
    assert http.get("/api/admin/summary", headers={"x-admin-token": "nope"}).status_code == 401
    s = http.get("/api/admin/summary", headers={"x-admin-token": "s3cret"}).json()
    assert s["accounts"][0]["email"] == "a@b.org" and s["outbox"][0]["name"] == "w.eml" and "usage" in s
    r = http.post("/api/admin/accounts/delete", headers={"x-admin-token": "s3cret"}, json={"email": "A@b.org"}).json()
    assert r["deleted"] and json.loads((tmp_path / "accounts.json").read_text()) == {}
    assert not (tmp_path / "outbox" / "w.eml").exists()


def test_page_has_usage_templates_and_second_opinion():
    html = (ROOT / "web" / "index.html").read_text()
    for el in ('id="usagedlg"', 'id="tplbtn"', 'id="tpldlg"', 'id="otherdlg"', "askother", "#FF5A42", "provChip(m.provider)",
               'aria-keyshortcuts="Control+K"', 'e.code === "KeyK"'):
        assert el in html
    relay = (ROOT / "relay" / "Code.gs").read_text()
    assert "function setupSheet" in relay and "log_(name, email, appUrl, 'sent')" in relay
