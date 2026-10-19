"""Token use and cost of the assistant, kept on disk: one line per answer in WORKSPACE/usage.jsonl."""
import json
import threading
import time
from collections import defaultdict

from . import fx
from . import simulations as S

FILE = S.WORKSPACE / "usage.jsonl"
PROVIDERS = ("anthropic", "openai", "gemini")
_lock = threading.Lock()


def record(provider, model, inp, out, cost_usd, god=None, mode=None, name=None, kind="chat"):
    row = {"ts": round(time.time(), 1), "provider": provider, "model": model, "input": int(inp or 0),
           "output": int(out or 0), "cost_usd": cost_usd, "god": god, "mode": mode, "name": name, "kind": kind}
    with _lock:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        with FILE.open("a") as f:
            f.write(json.dumps(row) + "\n")


def rows(days=30):
    if not FILE.exists():
        return []
    since = time.time() - days * 86400
    out = []
    for line in FILE.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", 0) >= since:
            out.append(r)
    return out


def summary(days=30):
    """Per day and provider: answers, tokens and cost (USD and EUR), newest day first, plus totals and models."""
    rate = fx.eur_per_usd()
    days_map = defaultdict(lambda: {p: {"answers": 0, "input": 0, "output": 0, "cost_usd": 0.0} for p in PROVIDERS})
    models = defaultdict(lambda: {"provider": "", "answers": 0, "input": 0, "output": 0, "cost_usd": 0.0})
    unpriced = 0
    for r in rows(days):
        day = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
        p = r.get("provider") if r.get("provider") in PROVIDERS else "anthropic"
        cell = days_map[day][p]
        cost = r.get("cost_usd")
        unpriced += cost is None
        for target in (cell, models[r.get("model") or "?"]):
            target["answers"] += 1
            target["input"] += r.get("input", 0)
            target["output"] += r.get("output", 0)
            target["cost_usd"] += cost or 0.0
        models[r.get("model") or "?"]["provider"] = p
    out_days = [{"day": d, "providers": v,
                 "cost_usd": sum(x["cost_usd"] for x in v.values()),
                 "tokens": sum(x["input"] + x["output"] for x in v.values())}
                for d, v in sorted(days_map.items(), reverse=True)]
    totals = {p: {k: sum(d["providers"][p][k] for d in out_days) for k in ("answers", "input", "output", "cost_usd")}
              for p in PROVIDERS}
    return {"days": out_days, "totals": totals, "eur_per_usd": rate, "period_days": days, "unpriced_answers": unpriced,
            "models": sorted(({"model": m, **v} for m, v in models.items()), key=lambda x: -x["cost_usd"])}
