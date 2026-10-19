"""Prompt evaluation: runs the fixed requests in evals/cases.py through the real agent and real simulations,
then writes a report (tools used, model calls, tokens, answer length, checks).

  python -m evals.run_eval --label v2                 # current prompt
  python -m evals.run_eval --label v1 --prompt v1     # the old prompt, for comparison
  python -m evals.run_eval --compare evals/results/v1.json evals/results/v2.json

Needs ANTHROPIC_API_KEY (or OPENAI_API_KEY / GEMINI_API_KEY with --provider openai / gemini). Uses a temporary workspace and a SQLite
catalog, so your own runs are not touched. In Docker:
  docker compose exec -e ANTHROPIC_API_KEY=sk-... sublattice-agent python -m evals.run_eval --label v2
"""
import argparse
import importlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def setup_env(keep_env):
    if keep_env:
        return
    tmp = Path(tempfile.mkdtemp(prefix="spinmate-eval-"))
    os.environ["WORKSPACE"] = str(tmp / "data")
    os.environ["DATABASE_URL"] = "sqlite:///" + str(tmp / "catalog.db")
    os.environ.setdefault("MAX_PARALLEL", str(max(2, (os.cpu_count() or 2) - 1)))


def load_prompt(spec):
    if not spec:
        return None
    if spec == "v1":
        return importlib.import_module("evals.prompt_v1")
    s = importlib.util.spec_from_file_location("prompt_override", spec)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def answer_lines(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("NEXT:")]


class Ctx:
    """What one case did: events, tools, tokens and the final answer."""

    def __init__(self, S):
        self.S = S
        self.tools, self.run_ids, self.answer, self.answers = [], [], "", []
        self.calls = self.tok_in = self.tok_out = 0
        self.errors = []
        self.runs_started = 0

    def last_run(self, full=False):
        sims = [r for r in self.run_ids if (self.S.run_info(r) or {}).get("kind") != "combined"]
        return self.S.run_info(sims[-1], full=full) if sims else None


def run_case(case, agent, S, args):
    ctx = Ctx(S)
    messages = []
    for turn in case["turns"]:
        messages = messages + [{"role": "user", "content": turn}]
        texts = []
        for ev in agent.chat_stream(messages, args.key, args.model, case.get("mode"), case.get("name"), None,
                                    provider=args.provider):
            t = ev["type"]
            if t == "text":
                texts.append(ev["text"])
            elif t == "tool_start":
                ctx.tools.append(ev["name"])
            elif t == "tool_done":
                e = ev.get("event") or {}
                if e.get("run_id") and e.get("tool") in ("start_simulation", "continue_run", "combine_runs"):
                    ctx.run_ids.append(e["run_id"])
                if e.get("tool") == "start_simulation" and e.get("run_id"):
                    ctx.runs_started += 1
                if e.get("tool") == "start_sweep" and e.get("runs"):
                    ctx.runs_started += int(e["runs"])
                if e.get("tool") == "continue_run" and e.get("run_id"):
                    ctx.runs_started += 1
            elif t == "usage":
                ctx.tok_in += ev.get("input", 0)
                ctx.tok_out += ev.get("output", 0)
            elif t == "error":
                ctx.errors.append(ev.get("error"))
            elif t == "done":
                messages = ev["messages"]
        ctx.answers.append("\n".join(texts))
    ctx.calls = sum(1 for m in messages if m["role"] == "assistant")
    ctx.answer = ctx.answers[-1] if ctx.answers else ""
    for rid in ctx.run_ids:                       # let started runs finish before checking them
        S.wait_for_run(rid, 120)
    return ctx


def check(case, ctx):
    out = []
    for t in case.get("tools", []):
        out.append((t in ctx.tools, f"called {t}"))
    for t in case.get("no_tools", []):
        out.append((t not in ctx.tools, f"did not call {t}"))
    if case.get("order"):
        run = ctx.last_run()
        got = ((run or {}).get("summary") or {}).get("order_final")
        out.append((got == case["order"], f"order_final {got} (expected {case['order']})"))
    if case.get("max_lines"):
        n = len(answer_lines(ctx.answer))
        out.append((n <= case["max_lines"], f"{n} lines (max {case['max_lines']})"))
    for rx in case.get("expect", []):
        out.append((bool(re.search(rx, ctx.answer, re.I | re.M)), f"mentions /{rx}/"))
    for rx in case.get("forbid", []):
        out.append((not re.search(rx, ctx.answer, re.I | re.M), f"avoids /{rx}/"))
    if case.get("check"):
        ok, msg = case["check"](ctx)
        out.append((ok, msg))
    if ctx.errors:
        out.append((False, "errors: " + "; ".join(map(str, ctx.errors))))
    return out


def report(results, label, prompt_version, model):
    passed = sum(r["passed"] for r in results)
    total = sum(r["checks"] for r in results)
    lines = [f"# SpinMate prompt eval: {label}", "",
             f"prompt v{prompt_version} · model {model} · {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"**Checks passed: {passed}/{total}** · cases fully passed: "
             f"{sum(r['passed'] == r['checks'] for r in results)}/{len(results)} · "
             f"tokens in {sum(r['tok_in'] for r in results):,} · out {sum(r['tok_out'] for r in results):,} · "
             f"model calls {sum(r['calls'] for r in results)}", "",
             "| case | checks | model calls | tokens in | tokens out | answer lines | tools |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['passed']}/{r['checks']} | {r['calls']} | {r['tok_in']:,} | "
                     f"{r['tok_out']:,} | {r['lines']} | {' → '.join(r['tools'])} |")
    for r in results:
        lines += ["", f"## {r['id']}", ""]
        lines += [f"- {'PASS' if ok else 'FAIL'}: {msg}" for ok, msg in r["detail"]]
        lines += ["", "```text", r["answer"].strip()[:1500], "```"]
    return "\n".join(lines) + "\n"


def compare(a_path, b_path):
    a, b = (json.loads(Path(p).read_text()) for p in (a_path, b_path))
    rows = {r["id"]: r for r in a["results"]}
    print(f"{'case':16} {'checks':>13} {'calls':>9} {'tokens in':>17} {'lines':>9}")
    print(f"{'':16} {a['label'] + ' / ' + b['label']:>13}")
    for r in b["results"]:
        o = rows.get(r["id"])
        if not o:
            continue
        print(f"{r['id']:16} {o['passed']}/{o['checks']} -> {r['passed']}/{r['checks']:<3} "
              f"{o['calls']:>3} -> {r['calls']:<3} {o['tok_in']:>7,} -> {r['tok_in']:<7,} {o['lines']:>3} -> {r['lines']}")
    for d in (a, b):
        res = d["results"]
        print(f"{d['label']}: checks {sum(x['passed'] for x in res)}/{sum(x['checks'] for x in res)}, "
              f"tokens in {sum(x['tok_in'] for x in res):,}, out {sum(x['tok_out'] for x in res):,}, "
              f"calls {sum(x['calls'] for x in res)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="eval")
    ap.add_argument("--prompt", help="'v1' for the old prompt, or a path to a prompts.py")
    ap.add_argument("--model")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai", "gemini"])
    ap.add_argument("--cases", help="comma separated case ids (default: all)")
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--keep-env", action="store_true", help="use WORKSPACE and DATABASE_URL as they are")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"))
    args = ap.parse_args()
    if args.compare:
        return compare(*args.compare)

    setup_env(args.keep_env)
    sys.path.insert(0, str(HERE.parent))
    from app import agent
    from app import simulations as S
    from evals.cases import CASES
    override = load_prompt(args.prompt)
    if override:
        agent.prompts = override
    args.key = os.environ.get({"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}.get(args.provider, "ANTHROPIC_API_KEY"))
    if not args.key:
        sys.exit("set ANTHROPIC_API_KEY (or OPENAI_API_KEY / GEMINI_API_KEY with --provider openai / gemini)")
    model = args.model or {"openai": agent.openai_provider.DEFAULT_MODEL,
                           "gemini": agent.gemini_provider.DEFAULT_MODEL}.get(args.provider, agent.DEFAULT_MODEL)
    wanted = set(args.cases.split(",")) if args.cases else None

    results = []
    for case in CASES:
        if wanted and case["id"] not in wanted:
            continue
        t0 = time.time()
        print(f"{case['id']} ...", end=" ", flush=True)
        ctx = run_case(case, agent, S, args)
        detail = check(case, ctx)
        r = {"id": case["id"], "tools": ctx.tools, "calls": ctx.calls, "tok_in": ctx.tok_in, "tok_out": ctx.tok_out,
             "lines": len(answer_lines(ctx.answer)), "answer": ctx.answer, "answers": ctx.answers,
             "detail": detail, "passed": sum(ok for ok, _ in detail), "checks": len(detail),
             "seconds": round(time.time() - t0, 1)}
        results.append(r)
        print(f"{r['passed']}/{r['checks']} checks, {ctx.tok_in:,} tokens in, {r['seconds']} s")

    version = getattr(agent.prompts, "PROMPT_VERSION", "?")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(json.dumps(
        {"label": args.label, "prompt_version": version, "model": model, "results": results}, indent=1))
    (out / f"{args.label}.md").write_text(report(results, args.label, version, model))
    print(f"report: {out / (args.label + '.md')}")


if __name__ == "__main__":
    main()
