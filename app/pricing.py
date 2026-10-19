"""Token prices, to show an estimated cost next to the token counts.

USD per million tokens: input, output, cache write (5 min), cache read. Anthropic prices from
platform.claude.com/docs/en/about-claude/pricing (October 2026); OpenAI from its public price list; Gemini
from ai.google.dev/gemini-api/docs/pricing (October 2026, paid tier, prompts up to 200k tokens).
Override with PRICES_JSON='{"model-prefix": [in, out, cache_write, cache_read], ...}' and the
exchange rate with EUR_PER_USD (default: the daily ECB rate, see fx.py). Haiku 5.5 costs 5x more above 100k prompt tokens; not modelled here.
"""
import json
import os
import re

PRICES = {
    "claude-fable-5-1": (10, 50, 12.5, 0.25), "claude-mythos-5-1": (10, 50, 12.5, 0.25),
    "claude-fable-5": (10, 50, 12.5, 1.0), "claude-mythos-5": (10, 50, 12.5, 1.0),
    "claude-opus-5-5": (4, 20, 5, 0.20), "claude-sonnet-5-5": (2, 10, 2.5, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-opus-5": (5, 25, 6.25, 0.50), "claude-sonnet-5": (2, 10, 2.5, 0.20),
    "claude-opus-4-5": (5, 25, 6.25, 0.50), "claude-opus-4-6": (5, 25, 6.25, 0.50),
    "claude-opus-4-7": (5, 25, 6.25, 0.50), "claude-opus-4-8": (5, 25, 6.25, 0.50),
    "claude-opus-4": (15, 75, 18.75, 1.50), "claude-sonnet-4": (3, 15, 3.75, 0.30),
    "claude-3-7-sonnet": (3, 15, 3.75, 0.30), "claude-haiku-4-5": (1, 5, 1.25, 0.10),
    "claude-3-5-haiku": (0.8, 4, 1, 0.08),
    # OpenAI (standard tier, short context; cache write is not charged)
    "gpt-5.6-sol": (5, 30, 5, 0.50), "gpt-5.6-terra": (2, 12, 2, 0.20), "gpt-5.6-luna": (0.20, 1.20, 0.20, 0.02),
    "gpt-5.5-pro": (30, 180, 30, 30), "gpt-5.5": (5, 30, 5, 0.50),
    "gpt-5.4-pro": (30, 180, 30, 30), "gpt-5.4-mini": (0.75, 4.5, 0.75, 0.075),
    "gpt-5.4-nano": (0.20, 1.25, 0.20, 0.02), "gpt-5.4": (2.5, 15, 2.5, 0.25),
    "gpt-5.2-pro": (21, 168, 21, 21), "gpt-5.2": (1.75, 14, 1.75, 0.175), "gpt-5.1": (1.25, 10, 1.25, 0.125),
    "gpt-5-mini": (0.25, 2, 0.25, 0.025), "gpt-5-nano": (0.05, 0.40, 0.05, 0.005), "gpt-5": (1.25, 10, 1.25, 0.125),
    "o4-mini": (1.1, 4.4, 1.1, 0.275), "o3-mini": (1.1, 4.4, 1.1, 0.55), "o3": (2, 8, 2, 0.50), "o1": (15, 60, 15, 7.5),
    "gpt-4o-mini": (0.15, 0.60, 0.15, 0.075), "gpt-4o": (2.5, 10, 2.5, 1.25),
    "gpt-4.1-nano": (0.10, 0.40, 0.10, 0.025), "gpt-4.1-mini": (0.40, 1.60, 0.40, 0.10),
    "gpt-4.1": (2, 8, 2, 0.50),
    # Gemini (paid tier; implicit caching, so no cache write charge)
    "gemini-4-argon": (2, 10, 2, 0.10),
    "gemini-3.8-flash": (0.75, 3.75, 0.75, 0.075), "gemini-3.7-flash": (0.75, 3.75, 0.75, 0.075),
    "gemini-3.6-flash": (0.75, 3.75, 0.75, 0.075),
    "gemini-3.5-flash-lite": (0.30, 2.5, 0.30, 0.03), "gemini-3.5-flash": (1.5, 9, 1.5, 0.15),
    "gemini-3.1-pro": (2, 12, 2, 0.20), "gemini-3.1-flash-lite": (0.25, 1.5, 0.25, 0.025),
    "gemini-3-pro": (2, 12, 2, 0.20), "gemini-3-flash": (0.50, 3, 0.50, 0.05),
    "gemini-2.5-pro": (1.25, 10, 1.25, 0.125), "gemini-2.5-flash-lite": (0.10, 0.40, 0.10, 0.01),
    "gemini-2.5-flash": (0.30, 2.5, 0.30, 0.03),
    "gemini-2.0-flash-lite": (0.075, 0.30, 0.075, 0.02), "gemini-2.0-flash": (0.10, 0.40, 0.10, 0.025),
}
# used when a model id is not in the table: the price of its family's current model
FAMILY = [
    (r"fable|mythos", (10, 50, 12.5, 0.25)), (r"claude.*opus", (5, 25, 6.25, 0.50)),
    (r"claude.*sonnet", (3, 15, 3.75, 0.30)), (r"claude.*haiku", (1, 5, 1.25, 0.10)),
    (r"^gpt-.*-pro", (30, 180, 30, 30)), (r"^gpt-.*nano", (0.20, 1.25, 0.20, 0.02)),
    (r"^gpt-.*mini", (0.75, 4.5, 0.75, 0.075)), (r"^gpt-5", (2.5, 15, 2.5, 0.25)),
    (r"^o\d", (2, 8, 2, 0.50)), (r"^(gpt-|chatgpt-)", (2.5, 10, 2.5, 1.25)),
    (r"^gemini-.*flash-lite", (0.30, 2.5, 0.30, 0.03)), (r"^gemini-.*flash", (0.75, 3.75, 0.75, 0.075)),
    (r"^gemini-.*pro", (2, 12, 2, 0.20)), (r"^gemini-", (2, 10, 2, 0.10)),
]
try:
    PRICES.update({k: tuple(v) for k, v in json.loads(os.environ.get("PRICES_JSON") or "{}").items()})
except (ValueError, TypeError):
    pass


def exact_price(model):
    """Longest matching prefix, so claude-sonnet-5-5 is not priced as claude-sonnet-5."""
    model = (model or "").lower()
    for key in sorted(PRICES, key=len, reverse=True):
        # gpt-5 must not price gpt-5.2 or gpt-50: the next character may not continue the version
        if model.startswith(key) and model[len(key):len(key) + 1] not in tuple(".0123456789"):
            return PRICES[key]
    return None


def price(model):
    """Table price, else the family estimate (opus, sonnet, haiku, gpt-*-mini, ...), else None."""
    p = exact_price(model)
    if p:
        return p
    l = (model or "").lower()
    for pat, fp in FAMILY:
        if re.search(pat, l):
            return fp
    return None


def is_estimate(model):
    return exact_price(model) is None and price(model) is not None


def cost_usd(model, uncached_in=0, out=0, cache_write=0, cache_read=0):
    p = price(model)
    if not p:
        return None
    return (uncached_in * p[0] + out * p[1] + cache_write * p[2] + cache_read * p[3]) / 1e6
