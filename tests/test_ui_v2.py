"""Version 2.0 page features: the validation badge and report, the engine list, the connect dialog."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.server import app

ROOT = Path(__file__).resolve().parent.parent
http = TestClient(app)


def test_validation_status_and_page():
    v = http.get("/api/validation").json()
    assert v["available"] and v["passed"] == v["total"] == 9 and v["date"]
    page = http.get("/validation")
    assert page.status_code == 200 and "phase-diagram.png" in page.text and "unchanged" not in page.text
    assert http.get("/api/validation/phase-diagram.png").headers["content-type"] == "image/png"


def test_engines_list_has_macrospin_first_class():
    eng = {e["name"]: e for e in http.get("/api/engines").json()["engines"]}
    assert eng["macrospin"]["ready"] and eng["macrospin"]["default"]


def test_page_has_the_new_controls():
    html = (ROOT / "web" / "index.html").read_text()
    for el in ('id="engine"', 'id="validbtn"', 'id="connectbtn"', 'id="cmd-code"', 'id="ks-gemini"'):
        assert el in html
