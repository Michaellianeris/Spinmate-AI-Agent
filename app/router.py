"""Automatic model choice for a persona (god) when the user has not pinned a model.

Every candidate the page sends (the persona's models from every provider whose key is present) gets

    U = capability + top bonus - lambda * cost - shortfall penalty

capability   0..1 from the model family and generation (strongest model of the persona scores highest)
cost         decades of list price above the cheapest candidate (log10 of the ratio)
lambda       how much this persona cares about price, reduced when the request is demanding
shortfall    penalty when capability is below what the request needs

The highest U is used; the fallbacks on an API error are the next ones by the same score, so the
order never depends on the provider. Pro models (very slow, ~10x the price) are only used when pinned.
"""
import math
import re

from . import pricing

# how much each persona leans to capability (1) rather than price (0)
PERSONA_WEIGHT = {"hermes": 0.40, "dionysus": 0.60, "apollo": 0.75, "zeus": 0.90}

HARD = re.compile(r"sweep|goal|smallest|largest|threshold|find|optimi|report|compare|explain|why|interpret|"
                  r"analy|fit|phase diagram|continue|combine|several|each|all runs|table|σ|sigma", re.I)
EASY = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|yes|no|run (it|the default)|γεια|ευχαριστ)\b", re.I)


def _version(l):
    m = re.search(r"(?:claude-(?:[a-z]+-)?|gpt-)(\d+)(?:[.-](\d{1,2})(?!\d))?", l)
    if m:
        return int(m.group(1)) + (int(m.group(2)) / 10 if m.group(2) else 0)
    m = re.match(r"o(\d+)", l)
    return 4.5 if m else 4.0          # o-series: about a 4.5-generation reasoning model


def capability(provider, model):
    """Rough 0..1 capability from the name only, good enough to rank models of one persona."""
    l = (model or "").lower()
    v = _version(l)
    if provider == "gemini":
        m = re.search(r"gemini-(\d+)(?:\.(\d))?", l)
        gv = int(m.group(1)) + (int(m.group(2)) / 10 if m and m.group(2) else 0) if m else 2.5
        if "flash-lite" in l:
            base = 0.40
        elif "flash" in l:
            base = 0.58
        elif "pro" in l:
            base = 0.80
        else:
            base = 0.84                 # new flagship lines (e.g. Argon) go to Apollo
        cap = base + (gv - 3) * 0.08    # Gemini 3.x is about the generation of Claude 5 and GPT-5
        if "preview" in l or "exp" in l:
            cap -= 0.001                 # prefer the stable id of the same model
        return max(0.05, min(1.0, cap))
    if provider == "anthropic":
        if "fable" in l or "mythos" in l:
            base = 0.88
        elif "opus" in l:
            base = 0.80
        elif "sonnet" in l:
            base = 0.68
        elif "haiku" in l:
            base = 0.50
        else:
            base = 0.60
    else:
        if "nano" in l:
            base = 0.35
        elif "mini" in l or "3.5" in l:
            base = 0.50
        elif re.match(r"o\d", l):
            base = 0.80
        elif v >= 5:
            base = 0.80                 # gpt-5.x is OpenAI's flagship line
        else:
            base = 0.62
        if re.search(r"-pro\b", l):
            base += 0.05
    cap = base + (v - 4) * 0.08
    if re.search(r"\d{8}$|\d{4}-\d{2}-\d{2}$", l):
        cap -= 0.001                     # prefer the alias over a dated snapshot
    return max(0.05, min(1.0, cap))


def complexity(messages, mode=None):
    """0 (a greeting, one plain run) .. 1 (deep, multi-step analysis)."""
    text = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                text = c
                break
            if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "text" for b in c):
                text = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                break
    c = 0.25
    if mode == "deep":
        c += 0.35
    hits = len(set(h.lower() for h in HARD.findall(text)))
    c += min(0.35, 0.12 * hits)
    if len(text) > 400:
        c += 0.10
    if sum(1 for m in messages or [] if m.get("role") == "user") > 6:
        c += 0.05
    if EASY.match(text) and len(text) < 60:
        c -= 0.20
    return max(0.0, min(1.0, c))


def _blended(model):
    p = pricing.price(model)
    return None if not p else (3 * p[0] + p[1]) / 4      # agent turns are input-heavy


def rank(candidates, god, messages, mode=None):
    """candidates: [{"provider", "model"}]. Returns (ordered list, reason text for the best)."""
    cands = [c for c in candidates if c.get("provider") and c.get("model")]
    seen, uniq = set(), []
    for c in cands:
        k = (c["provider"], c["model"])
        if k not in seen:
            seen.add(k)
            uniq.append({"provider": c["provider"], "model": c["model"]})
    # OpenAI pro models (very slow, ~10x the price) only when pinned; Gemini Pro is the normal top line
    auto = [c for c in uniq if c["provider"] == "gemini" or not re.search(r"-pro\b", c["model"].lower())]
    uniq = auto or uniq
    if not uniq:
        return [], ""
    w = PERSONA_WEIGHT.get(god, 0.6)
    cx = complexity(messages, mode)
    lam = (1 - w) * (1 - 0.7 * cx)
    need = 0.45 + 0.35 * cx
    prices = [_blended(c["model"]) for c in uniq]
    known = [p for p in prices if p]
    cheapest = min(known) if known else 1.0
    caps = [capability(c["provider"], c["model"]) for c in uniq]
    top = max(caps)
    bonus = 0.06 + 0.06 * w                  # the persona's strongest model gets priority
    for c, p, cap in zip(uniq, prices, caps):
        cost = math.log10(p / cheapest) if p else 0.5
        u = cap + (bonus if cap >= top - 1e-9 else 0) - lam * cost - 2 * max(0.0, need - cap)
        c.update(capability=round(cap, 3), cost=round(cost, 3), price=p, score=round(u, 4))
    uniq.sort(key=lambda c: -c["score"])
    b = uniq[0]
    level = "demanding" if cx >= 0.6 else "simple" if cx < 0.3 else "normal"
    why = (f"auto: {level} request · capability {b['capability']:.2f}"
           + (f" · ~${b['price']:.2f}/MTok" if b["price"] else " · price unknown")
           + (f" · beat {uniq[1]['model']}" if len(uniq) > 1 else ""))
    return uniq, why
