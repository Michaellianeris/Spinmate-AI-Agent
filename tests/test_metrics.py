"""Run metrics in the summary and the per-request prompt notes."""
import sqlite3

import numpy as np

from app import agent, catalog, prompts, runner


def test_switches_ignore_ringing_and_count_reversals():
    t = np.arange(8.0)
    ringing = [-1, -0.2, 0.3, -0.1, 0.2, -0.3, -0.9, -1]
    assert runner.switches(t, ringing) == []
    reversal = [-1, -0.6, 0.1, 0.7, 1, 0.4, -0.8, -1]
    assert runner.switches(t, reversal) == [3.0, 6.0]


def test_settle_time():
    t = np.linspace(0, 1, 101)
    m = np.tile([0.0, 0.0, 1.0], (101, 1))
    m[:20, 0] = 0.5                        # moving until t = 0.19
    assert abs(runner.settle_time(t, m, m) - 0.2) < 1e-9
    m[:95, 0] = 0.5                        # still moving at 90 % of the run
    assert runner.settle_time(t, m, m) is None


def test_name_is_greeted_only_on_the_first_message():
    first = [{"role": "user", "content": "hi"}]
    later = first + [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "{}"}]},
                     {"role": "user", "content": "and now?"}]
    assert agent._first_turn(first) and not agent._first_turn(later)
    assert "Greet" in agent._system_parts(first, "fast", "Michalis")[2]
    assert "Do not greet" in agent._system_parts(later, "fast", "Michalis")[2]
    assert agent._system_parts(first, "fast", "")[1] == prompts.MODE_NOTES["fast"]


def test_old_catalog_gets_new_columns(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    v1 = catalog.SCHEMA_SQLITE.replace(" neel_z_switches INTEGER, first_neel_z_switch_s REAL,\n  settle_time_s REAL,", "")
    assert "settle_time_s" not in v1
    with sqlite3.connect(db) as c:
        c.executescript(v1)
    monkeypatch.setattr(catalog, "DATABASE_URL", "sqlite:///" + str(db))
    monkeypatch.setattr(catalog, "_ready", False)
    monkeypatch.setattr(catalog, "_down_until", 0.0)
    assert catalog._available()
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
    assert {"neel_z_switches", "first_neel_z_switch_s", "settle_time_s"} <= cols
