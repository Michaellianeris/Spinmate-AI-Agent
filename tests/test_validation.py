"""The physics validation suite: the fast cases must pass, and finding F1 stays visible."""
from spinmate_core import validation as V


def test_fast_cases_pass():
    for r in V.run_all(V.FAST):
        assert r.status == "pass", (r.id, r.measured, r.note)


def test_afmr_shows_the_doubled_anisotropy():
    r = V.afmr(h=1e-13)
    assert abs(r.measured["f GHz"] - r.expected["f GHz with 2 Ku"]) / r.expected["f GHz with 2 Ku"] < 0.01
    assert "F1" in r.note


def test_report_html():
    html = V.report_html([V.Result("x", "A case", "pass", {"a": 1.0}, {"a": 1.0}, "1 %")])
    assert "1 of 1 cases pass" in html and "F1" in html
