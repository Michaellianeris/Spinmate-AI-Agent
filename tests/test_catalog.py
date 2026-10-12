"""Run catalog: SQL guard always; the Postgres round trip only when TEST_DATABASE_URL is set."""
import json
import os

import pytest

from app import catalog


@pytest.mark.parametrize("sql", [
    "DELETE FROM runs",
    "select 1; drop table runs",
    "WITH x AS (DELETE FROM runs RETURNING *) SELECT * FROM x",
    "SELECT pg_sleep(10)",
    "UPDATE runs SET label = 'x'",
    "",
])
def test_only_single_selects_pass(sql):
    assert catalog._check_sql(sql)[1]


def test_select_and_with_pass():
    assert catalog._check_sql("SELECT run_id FROM runs WHERE label = 'delete me';")[0]
    assert catalog._check_sql("-- note\nWITH a AS (SELECT 1 AS x) SELECT x FROM a")[0]


def test_query_says_when_catalog_is_off(monkeypatch):
    monkeypatch.setattr(catalog, "DATABASE_URL", "")
    out = catalog.query("SELECT 1")
    assert out["ok"] is False and "DATABASE_URL" in out["error"]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL")
def test_runs_are_catalogued_and_queryable(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE", str(tmp_path))  # only used if simulations is not imported yet
    import psycopg
    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS runs, sweeps")
    monkeypatch.setattr(catalog, "DATABASE_URL", url)
    monkeypatch.setattr(catalog, "_ready", False)
    monkeypatch.setattr(catalog, "_down_until", 0.0)

    from app import simulations as S
    from app import tools
    ids = {}
    for a0 in (0.248e-12, -0.248e-12):
        r = S.start_run(f"--A0={a0!r}\n--t=2e-10\n", label=f"catalog test {a0:g}")
        assert r["ok"], r
        ids[r["run_id"]] = a0
    for rid in ids:
        assert S.wait_for_run(rid, 60)["status"] == "done"

    sql = ("SELECT run_id, (params->>'A0')::float8 AS a0, order_initial, order_final, status "
           f"FROM runs WHERE run_id IN ({', '.join(repr(i) for i in ids)}) ORDER BY a0")
    content, event = tools.call("query_runs", {"sql": sql})
    out = json.loads(content)
    assert out["ok"], out
    assert out["columns"] == ["run_id", "a0", "order_initial", "order_final", "status"]
    rows = {r[0]: r for r in out["rows"]}
    assert set(rows) == set(ids)
    for rid, a0 in ids.items():
        assert rows[rid][1] == pytest.approx(a0)
        assert rows[rid][4] == "done"
        assert rows[rid][3] == ("FM" if a0 > 0 else "AFM")

    # the database itself refuses writes inside a query
    bad = catalog.query("SELECT * FROM runs FOR UPDATE")
    assert bad["ok"] is False
