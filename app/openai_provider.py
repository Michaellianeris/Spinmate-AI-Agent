"""Small OpenAI Responses API client used by the SpinMate agent.

The project deliberately uses the standard library here rather than adding a second SDK. The browser
conversation stays in SpinMate's canonical (Anthropic-style) block format; this module translates it to
and from the Responses API.
"""
import json
import os
import ssl
import urllib.error
import urllib.request

BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1")
KNOWN_MODELS = tuple(m.strip() for m in (os.environ.get("OPENAI_MODELS") or
                     "gpt-5,gpt-5-mini,gpt-5-nano,gpt-4.1,gpt-4.1-mini,gpt-4.1-nano,gpt-4o,o3,o4-mini").split(",")
                     if m.strip())


class OpenAIError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _request(path, key, method="GET", body=None, timeout=90):
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as res:
            raw = res.read().decode("utf-8", "replace")
            content_type = res.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(raw).get("error", {}).get("message") or raw[:300]
        except Exception:
            msg = raw[:300]
        status = 401 if e.code in (401, 403) else 429 if e.code == 429 else 400 if e.code == 404 else 502
        raise OpenAIError(f"OpenAI API error {e.code}: {msg}", status)
    except urllib.error.URLError as e:
        raise OpenAIError(f"Could not reach the OpenAI API ({e.reason})", 502)
    if "json" not in content_type.lower() and raw.lstrip().startswith("<"):
        raise OpenAIError("The network returned a web page instead of the OpenAI API. A proxy or firewall may be intercepting api.openai.com.", 502)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise OpenAIError("OpenAI returned an invalid response.", 502)


def _chat_model(model_id):
    """Keep text/reasoning models; hide audio, image, embedding and moderation endpoints."""
    name = model_id.lower()
    if not (name.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4", "o5"))):
        return False
    return not any(x in name for x in ("audio", "realtime", "transcribe", "tts", "image", "embedding",
                                        "moderation", "search", "computer-use"))


def list_models(api_key=None):
    key = api_key or os.environ.get("OPENAI_API_KEY")
    err = None if key else "no API key"
    if key:
        try:
            found = sorted((m["id"] for m in _request("/models", key).get("data", []) if _chat_model(m.get("id", ""))),
                           reverse=True)
            if found:
                return [{"id": m, "name": m, "provider": "openai"} for m in found], "api", None
        except Exception as e:
            err = str(e)
    return [{"id": m, "name": m, "provider": "openai"} for m in KNOWN_MODELS], "configured", err


def tool_schemas(tools):
    return [{"type": "function", "name": t["name"], "description": t["description"],
             "parameters": t["input_schema"]} for t in tools]


def _text_from_tool_result(block):
    content = block.get("content", "")
    if isinstance(content, str):
        return content, []
    texts, images = [], []
    for item in content or []:
        if item.get("type") == "text":
            texts.append(item.get("text", ""))
        elif item.get("type") == "image":
            source = item.get("source", {})
            images.append(f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}")
    return "\n".join(texts), images


def to_input(messages):
    """Translate SpinMate's canonical history to Responses API input items."""
    out = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            if text:
                out.append({"role": "assistant", "content": text})
            for block in content:
                if block.get("type") == "tool_use":
                    out.append({"type": "function_call", "call_id": block["id"], "name": block["name"],
                                "arguments": json.dumps(block.get("input") or {})})
        elif role == "user":
            texts = [b.get("text", "") for b in content if b.get("type") == "text" and b.get("text")]
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
            images = []
            for block in content:
                if block.get("type") == "tool_result":
                    text, pics = _text_from_tool_result(block)
                    out.append({"type": "function_call_output", "call_id": block["tool_use_id"], "output": text})
                    images.extend(pics)
            if images:
                out.append({"role": "user", "content": [{"type": "input_text", "text": "Plots returned by the tools:"}] +
                            [{"type": "input_image", "image_url": p} for p in images]})
    return out


def create(model, instructions, input_items, tools, api_key=None, max_tokens=4096, timeout=90):
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise OpenAIError("No OpenAI API key. Add one in Settings or set OPENAI_API_KEY.", 401)
    body = {"model": model or DEFAULT_MODEL, "instructions": instructions, "input": input_items,
            "tools": tool_schemas(tools), "max_output_tokens": max_tokens, "store": False,
            "parallel_tool_calls": True}
    return _request("/responses", key, "POST", body, timeout)


def output_blocks(response):
    """Return (canonical blocks, function calls, replayable output items)."""
    blocks, calls = [], []
    for item in response.get("output", []):
        if item.get("type") == "message":
            text = "".join(x.get("text", "") for x in item.get("content", []) if x.get("type") == "output_text")
            if text.strip():
                blocks.append({"type": "text", "text": text})
        elif item.get("type") == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            call = {"type": "tool_use", "id": item["call_id"], "name": item["name"], "input": args}
            blocks.append(call)
            calls.append(call)
    return blocks, calls, response.get("output", [])


def usage(response):
    value = response.get("usage") or {}
    return int(value.get("input_tokens") or 0), int(value.get("output_tokens") or 0)


def cached_tokens(response):
    value = response.get("usage") or {}
    return int(((value.get("input_tokens_details") or {}).get("cached_tokens")) or 0)


def text(messages, instructions, model=None, api_key=None, max_tokens=400):
    response = create(model, instructions, to_input(messages), [], api_key, max_tokens, 60)
    blocks, _, _ = output_blocks(response)
    return "".join(b["text"] for b in blocks if b["type"] == "text").strip(), response
