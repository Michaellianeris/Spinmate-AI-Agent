"""Claude tool-use loop. The conversation lives in the browser and is sent on every turn."""
import copy
import os
import re

import anthropic

from . import openai_provider
from . import fx
from . import pricing
from . import tools
from . import prompts

DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5"
KNOWN_MODELS = ("claude-sonnet-5,claude-opus-5-5,claude-opus-4-5-20251101,claude-opus-4-1-20250805,"
                "claude-opus-4-20250514,claude-sonnet-4-5-20250929,claude-sonnet-4-20250514,"
                "claude-3-7-sonnet-20250219,claude-haiku-4-5-20251001,claude-3-5-haiku-20241022")
MODELS = [m.strip() for m in (os.environ.get("CLAUDE_MODELS") or KNOWN_MODELS).split(",") if m.strip()]
if DEFAULT_MODEL not in MODELS:
    MODELS.insert(0, DEFAULT_MODEL)


def list_models(api_key=None):
    """Every model the key can use (Sonnet, Opus, Haiku and any other), falling back to the configured list."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    err = None if key else "no API key"
    if key:
        try:
            found = [{"id": m.id, "name": getattr(m, "display_name", None) or m.id,
                      "provider": "anthropic"}
                     for m in anthropic.Anthropic(api_key=key).models.list(limit=1000)]
            if found:
                return found, "api", None
        except Exception as e:
            err = f"{type(e.__cause__ or e).__name__}: {e.__cause__ or e}"
    return [{"id": m, "name": m, "provider": "anthropic"} for m in MODELS], "configured", err


MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "4096"))
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "24"))
TOKEN_BUDGET = int(os.environ.get("TOKEN_BUDGET") or 0)
USED = {"input": 0, "output": 0, "cost": 0.0}


def tokens_used():
    return USED["input"] + USED["output"]


def _account_values(inp, out):
    USED["input"] += int(inp or 0)
    USED["output"] += int(out or 0)
    return int(inp or 0), int(out or 0)


def _cost(c):
    if c is not None:
        USED["cost"] += c
    return c or 0.0


def _account(resp, model=None):
    """Add the usage of one Anthropic response to the session counters; returns (input, output, cost USD)."""
    u = getattr(resp, "usage", None)
    g = lambda k: int(getattr(u, k, 0) or 0) if u else 0
    plain, cw, cr, out = g("input_tokens"), g("cache_creation_input_tokens"), g("cache_read_input_tokens"), g("output_tokens")
    i, o = _account_values(plain + cw + cr, out)
    return i, o, _cost(pricing.cost_usd(model, plain, out, cw, cr))


class AgentError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


def _prune_images(messages):
    """Keep plot images only in the newest user turn; older ones become a short note."""
    msgs = copy.deepcopy(messages)
    last_user = max((i for i, m in enumerate(msgs) if m["role"] == "user"), default=-1)
    for i, m in enumerate(msgs):
        if i == last_user or not isinstance(m["content"], list):
            continue
        for block in m["content"]:
            if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                block["content"] = [b if b.get("type") != "image"
                                    else {"type": "text", "text": "[plot shown earlier]"}
                                    for b in block["content"]]
    return msgs


def _dump(block):
    d = block.model_dump(exclude_none=True)
    d.pop("citations", None)
    return d


def _anthropic_stream(messages, api_key=None, model=None, mode=None, name=None, defaults_text=None, approved=False):
    """Run Claude until it answers without tool calls, yielding events as they happen.

    Events: text, tool_start, tool_done, then done (full message list) or error.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        yield {"type": "error", "status": 401,
               "error": "No API key. Paste a key from console.anthropic.com or set ANTHROPIC_API_KEY."}
        return
    client = anthropic.Anthropic(api_key=key)
    model = model or DEFAULT_MODEL
    messages = list(messages)
    parts = _system_parts(messages, mode, name, defaults_text)
    # the long prompt is cached; the short per-request notes follow it
    system = [{"type": "text", "text": parts[0], "cache_control": {"type": "ephemeral"}}] + \
             [{"type": "text", "text": t} for t in parts[1:]]
    turn_in = turn_out = 0
    turn_cost = 0.0
    priced = pricing.price(model) is not None

    def usage():
        return {"type": "usage", "input": turn_in, "output": turn_out, "session": tokens_used(), "budget": TOKEN_BUDGET,
                "cost_usd": round(turn_cost, 6) if priced else None, "eur_per_usd": fx.eur_per_usd(),
                "cost_estimate": pricing.is_estimate(model), "model": model}

    for _ in range(MAX_STEPS):
        if TOKEN_BUDGET and tokens_used() >= TOKEN_BUDGET:
            yield usage()
            yield {"type": "error", "status": 429, "error": f"Token budget of {TOKEN_BUDGET:,} reached. No run was started."}
            return
        try:
            resp = client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system,
                                          tools=tools.TOOLS, messages=_prune_images(messages), timeout=90)
        except anthropic.AuthenticationError:
            yield {"type": "error", "status": 401, "error": "The API key was rejected. Check it in console.anthropic.com."}
            return
        except anthropic.NotFoundError:
            yield {"type": "error", "status": 400, "error": f"Model '{model}' is not available for this key."}
            return
        except anthropic.RateLimitError:
            yield {"type": "error", "status": 429, "error": "Rate limit reached. Wait a moment and send again."}
            return
        except anthropic.APIStatusError as e:
            yield {"type": "error", "status": 502, "error": f"Claude API error {e.status_code}: {e.message}"}
            return
        except anthropic.APIConnectionError as e:
            cause = e.__cause__ or e
            yield {"type": "error", "status": 502, "error": f"Could not reach the Claude API ({type(cause).__name__}: {cause}). Check the network, proxy and certificates of the container."}
            return

        if isinstance(resp, str):
            title = re.search(r"<title>(.*?)</title>", resp, re.I | re.S)
            yield {"type": "error", "status": 502,
                   "error": "The network returned a web page instead of the Claude API"
                            + (f" (\"{title.group(1).strip()}\")" if title else "")
                            + ". A proxy or firewall is intercepting api.anthropic.com; open that page in a browser or ask IT to allow the API."}
            return

        i, o, c = _account(resp, model)
        turn_in += i
        turn_out += o
        turn_cost += c
        if TOKEN_BUDGET and tokens_used() >= TOKEN_BUDGET and resp.stop_reason == "tool_use":
            yield usage()
            yield {"type": "error", "status": 429, "error": f"Token budget of {TOKEN_BUDGET:,} reached. No run was started."}
            return
        messages.append({"role": "assistant", "content": [_dump(b) for b in resp.content]})
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                yield {"type": "text", "text": block.text}
        if resp.stop_reason != "tool_use":
            yield usage()
            yield {"type": "done", "messages": messages}
            return

        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            yield {"type": "tool_start", "id": block.id, "name": block.name, "input": block.input or {}}
            try:
                out, event = tools.call(block.name, block.input or {}, approved)
                is_error = '"ok": false' in out if isinstance(out, str) else False
            except Exception as e:  # tool failure goes back to Claude, not to the user
                out, event, is_error = f"tool failed: {e}", {"tool": block.name, "error": str(e)}, True
            yield {"type": "tool_done", "id": block.id, "event": event, "is_error": is_error}
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": out, "is_error": is_error})
        messages.append({"role": "user", "content": results})

    messages.append({"role": "assistant", "content": [
        {"type": "text", "text": "I stopped after too many tool calls in one turn. Tell me how to continue."}]})
    yield usage()
    yield {"type": "done", "messages": messages}


def _first_turn(messages):
    """True when the conversation has exactly one message typed by the user (tool results do not count)."""
    typed = 0
    for m in messages:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) or any(b.get("type") == "text" for b in (c or []) if isinstance(b, dict)):
            typed += 1
    return typed <= 1


def _system_parts(messages, mode=None, name=None, defaults_text=None):
    parts = [prompts.SYSTEM_PROMPT]
    if mode in prompts.MODE_NOTES:
        parts.append(prompts.MODE_NOTES[mode])
    note = prompts.name_note(name, _first_turn(messages))
    if note:
        parts.append(note)
    if defaults_text and defaults_text.strip():
        parts.append("The user's saved default parameters (priority 3). Use them as the base of every run unless "
                     "the message asks for something else:\n" + defaults_text.strip())
    return parts


def _system_text(messages, mode=None, name=None, defaults_text=None):
    return "\n\n".join(_system_parts(messages, mode, name, defaults_text))


def _openai_stream(messages, api_key=None, model=None, mode=None, name=None, defaults_text=None, approved=False):
    model = model or openai_provider.DEFAULT_MODEL
    messages = list(messages)
    input_items = openai_provider.to_input(messages)
    instructions = _system_text(messages, mode, name, defaults_text)
    turn_in = turn_out = 0
    turn_cost = 0.0
    priced = pricing.price(model) is not None

    def usage_event():
        return {"type": "usage", "input": turn_in, "output": turn_out,
                "session": tokens_used(), "budget": TOKEN_BUDGET,
                "cost_usd": round(turn_cost, 6) if priced else None, "eur_per_usd": fx.eur_per_usd(),
                "cost_estimate": pricing.is_estimate(model), "model": model}

    for _ in range(MAX_STEPS):
        if TOKEN_BUDGET and tokens_used() >= TOKEN_BUDGET:
            yield usage_event()
            yield {"type": "error", "status": 429,
                   "error": f"Token budget of {TOKEN_BUDGET:,} reached. No run was started."}
            return
        try:
            response = openai_provider.create(model, instructions, input_items, tools.TOOLS,
                                              api_key, MAX_TOKENS, 90)
        except openai_provider.OpenAIError as e:
            yield {"type": "error", "status": e.status, "error": str(e)}
            return

        inp, out = _account_values(*openai_provider.usage(response))
        cached = openai_provider.cached_tokens(response)
        turn_in += inp
        turn_out += out
        turn_cost += _cost(pricing.cost_usd(model, inp - cached, out, 0, cached))
        blocks, calls, replay = openai_provider.output_blocks(response)
        if not blocks and response.get("error"):
            yield {"type": "error", "status": 502, "error": str(response["error"])}
            return
        if TOKEN_BUDGET and tokens_used() >= TOKEN_BUDGET and calls:
            yield usage_event()
            yield {"type": "error", "status": 429,
                   "error": f"Token budget of {TOKEN_BUDGET:,} reached. No run was started."}
            return

        if blocks:
            messages.append({"role": "assistant", "content": blocks})
        for block in blocks:
            if block["type"] == "text" and block["text"].strip():
                yield {"type": "text", "text": block["text"]}
        if not calls:
            yield usage_event()
            yield {"type": "done", "messages": messages}
            return

        results = []
        for call in calls:
            yield {"type": "tool_start", "id": call["id"], "name": call["name"],
                   "input": call["input"]}
            try:
                value, event = tools.call(call["name"], call["input"], approved)
                is_error = '"ok": false' in value if isinstance(value, str) else False
            except Exception as e:
                value, event, is_error = f"tool failed: {e}", {"tool": call["name"], "error": str(e)}, True
            yield {"type": "tool_done", "id": call["id"], "event": event, "is_error": is_error}
            results.append({"type": "tool_result", "tool_use_id": call["id"],
                            "content": value, "is_error": is_error})
        result_message = {"role": "user", "content": results}
        messages.append(result_message)
        input_items.extend(replay)
        input_items.extend(openai_provider.to_input([result_message]))

    messages.append({"role": "assistant", "content": [{"type": "text",
        "text": "I stopped after too many tool calls in one turn. Tell me how to continue."}]})
    yield usage_event()
    yield {"type": "done", "messages": messages}


def chat_stream(messages, api_key=None, model=None, mode=None, name=None, defaults_text=None,
                approved=False,
                provider="anthropic"):
    """Provider-neutral tool loop; yields the same events for Anthropic and OpenAI."""
    stream = _openai_stream if provider == "openai" else _anthropic_stream
    yield from stream(messages, api_key, model, mode, name, defaults_text, approved)


def chat(messages, api_key=None, model=None, provider="anthropic"):
    """Blocking version of chat_stream. Returns (messages, tool events)."""
    events = []
    for ev in chat_stream(messages, api_key, model, provider=provider):
        if ev["type"] == "tool_done":
            events.append(ev["event"])
        elif ev["type"] == "error":
            raise AgentError(ev["error"], ev["status"])
        elif ev["type"] == "done":
            return ev["messages"], events
    raise AgentError("The conversation ended without an answer.", 500)
