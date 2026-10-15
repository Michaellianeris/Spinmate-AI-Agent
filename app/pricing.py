"""Token prices, to show an estimated cost next to the token counts.

USD per million tokens: input, output, cache write (5 min), cache read. Anthropic prices from
platform.claude.com/docs/en/about-claude/pricing (October 2026); OpenAI from its public price list.
Override with PRICES_JSON='{"model-prefix": [in, out, cache_write, cache_read], ...}' and the
exchange rate with EUR_PER_USD. Haiku 5.5 costs 5x more above 100k prompt tokens; not modelled here.
"""
import json
import os

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
    "gpt-4.1-nano": (0.10, 0.40, 0.10, 0.025), "gpt-4.1-mini": (0.40, 1.60, 0.40, 0.10),
    "gpt-4.1": (2, 8, 2, 0.50),
}
try:
    PRICES.update({k: tuple(v) for k, v in json.loads(os.environ.get("PRICES_JSON") or "{}").items()})
except (ValueError, TypeError):
    pass
EUR_PER_USD = float(os.environ.get("EUR_PER_USD") or 0.89)


def price(model):
    """Longest matching prefix, so claude-sonnet-5-5 is not priced as claude-sonnet-5."""
    model = (model or "").lower()
    for key in sorted(PRICES, key=len, reverse=True):
        if model.startswith(key):
            return PRICES[key]
    return None


def cost_usd(model, uncached_in=0, out=0, cache_write=0, cache_read=0):
    p = price(model)
    if not p:
        return None
    return (uncached_in * p[0] + out * p[1] + cache_write * p[2] + cache_read * p[3]) / 1e6
