"""The physics validation suite: the fast cases and the resonance formulas must pass."""
from spinmate_core import validation as V


def test_fast_cases_pass():
    for r in V.run_all(V.FAST):
        assert r.status == "pass", (r.id, r.measured, r.note)


def test_afmr_matches_the_formula():
    r = V.afmr(h=1e-13)
    assert r.status == "pass", r.measured


def test_report_html():
    html = V.report_html([V.Result("x", "A case", "pass", {"a": 1.0}, {"a": 1.0}, "1 %")])
    assert "1 of 1 cases pass" in html and "F1" in html
