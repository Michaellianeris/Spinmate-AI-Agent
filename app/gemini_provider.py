"""Small Gemini API client (Google AI Studio keys) used by the SpinMate agent.

Standard library only, like openai_provider. The conversation stays in SpinMate's canonical (Anthropic-style)
block format; this module translates it to and from generateContent.
"""
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
KNOWN_MODELS = tuple(m.strip() for m in (os.environ.get("GEMINI_MODELS") or
                     "gemini-3.1-pro-preview,gemini-3-flash-preview,gemini-3.1-flash-lite,"
                     "gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite").split(",") if m.strip())
# Gemini 3 wants the thought signature of every earlier function call back; calls made by another
# provider have none, and this documented value skips the check for them
NO_SIGNATURE = "skip_thought_signature_validator"


class GeminiError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def env_key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _request(path, key, method="GET", body=None, timeout=90):
    headers = {"x-goog-api-key": key, "Accept": "application/json"}
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
        status = 401 if e.code in (401, 403) else 429 if e.code == 429 else 400 if e.code in (400, 404) else 502
        if e.code == 400 and "api key" in msg.lower():
            status = 401
        raise GeminiError(f"Gemini API error {e.code}: {msg}", status)
    except urllib.error.URLError as e:
        raise GeminiError(f"Could not reach the Gemini API ({e.reason})", 502)
    if "json" not in content_type.lower() and raw.lstrip().startswith("<"):
        raise GeminiError("The network returned a web page instead of the Gemini API. A proxy or firewall may be "
                          "intercepting generativelanguage.googleapis.com.", 502)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise GeminiError("Gemini returned an invalid response.", 502)


SKIP = ("embedding", "image", "tts", "audio", "live", "veo", "imagen", "aqa", "robotics", "computer-use",
        "learnlm", "banana", "native")


def _chat_model(m):
    """Gemini text models that can call tools; Gemma, image, speech and embedding models are hidden."""
    mid = m.get("name", "").removeprefix("models/").lower()
    return (mid.startswith("gemini-") and "generateContent" in (m.get("supportedGenerationMethods") or [])
            and not any(x in mid for x in SKIP))


def list_models(api_key=None):
    key = api_key or env_key()
    err = None if key else "no API key"
    if key:
        try:
            found, token = [], ""
            for _ in range(5):
                out = _request("/models?pageSize=1000" + (f"&pageToken={urllib.parse.quote(token)}" if token else ""), key)
                found += [m for m in out.get("models", []) if _chat_model(m)]
                token = out.get("nextPageToken")
                if not token:
                    break
            if found:
                return [{"id": m["name"].removeprefix("models/"), "name": m.get("displayName") or m["name"],
                         "provider": "gemini"} for m in found], "api", None
        except Exception as e:
            err = str(e)
    return [{"id": m, "name": m, "provider": "gemini"} for m in KNOWN_MODELS], "configured", err


def tool_schemas(tools):
    return [{"functionDeclarations": [{"name": t["name"], "description": t["description"],
                                       "parametersJsonSchema": t["input_schema"]} for t in tools]}] if tools else []


def _result_parts(block, name):
    """A tool result becomes a functionResponse; plot images follow it as inline data."""
    content = block.get("content", "")
    texts, images = ([content], []) if isinstance(content, str) else ([], [])
    if not isinstance(content, str):
        for item in content or []:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif item.get("type") == "image":
                src = item.get("source", {})
                images.append({"inlineData": {"mimeType": src.get("media_type", "image/png"), "data": src.get("data", "")}})
    text = "\n".join(texts)
    try:
        value = json.loads(text)
        value = value if isinstance(value, dict) else {"result": value}
    except ValueError:
        value = {"result": text}
    return [{"functionResponse": {"name": name, "response": value}}] + images


def to_contents(messages):
    """Translate SpinMate's canonical history to Gemini contents (roles user and model)."""
    out, names = [], {}
    for message in messages:
        role, content = message.get("role"), message.get("content")
        g_role = "model" if role == "assistant" else "user"
        if isinstance(content, str):
            if content:
                out.append({"role": g_role, "parts": [{"text": content}]})
            continue
        parts = []
        for b in content or []:
            t = b.get("type")
            if t == "text" and b.get("text"):
                part = {"text": b["text"]}
                if role == "assistant" and b.get("gemini_signature"):
                    part["thoughtSignature"] = b["gemini_signature"]
                parts.append(part)
            elif t == "tool_use" and role == "assistant":
                names[b["id"]] = b["name"]
                parts.append({"functionCall": {"name": b["name"], "args": b.get("input") or {}},
                              "thoughtSignature": b.get("gemini_signature") or NO_SIGNATURE})
            elif t == "tool_result":
                parts += _result_parts(b, names.get(b.get("tool_use_id"), "tool"))
        if parts:
            out.append({"role": g_role, "parts": parts})
    return out


def create(model, instructions, contents, tools, api_key=None, max_tokens=4096, timeout=90):
    key = api_key or env_key()
    if not key:
        raise GeminiError("No Gemini API key. Add one in Settings or set GEMINI_API_KEY.", 401)
    body = {"contents": contents, "systemInstruction": {"parts": [{"text": instructions}]},
            "generationConfig": {"maxOutputTokens": max_tokens + 2048}}   # thinking counts as output
    if tools:
        body["tools"] = tool_schemas(tools)
    path = f"/models/{urllib.parse.quote(model or DEFAULT_MODEL)}:generateContent"
    return _request(path, key, "POST", body, timeout)


def output_blocks(response, step=0):
    """Return (canonical blocks, function calls). Thought signatures ride along on the blocks."""
    cands = response.get("candidates") or []
    if not cands:
        reason = (response.get("promptFeedback") or {}).get("blockReason")
        raise GeminiError(f"Gemini returned no answer{f' ({reason})' if reason else ''}.", 502)
    blocks, calls = [], []
    for i, part in enumerate((cands[0].get("content") or {}).get("parts") or []):
        if part.get("thought"):
            continue
        sig = part.get("thoughtSignature")
        if "functionCall" in part:
            fc = part["functionCall"]
            call = {"type": "tool_use", "id": fc.get("id") or f"gm_{step}_{i}", "name": fc.get("name", ""),
                    "input": fc.get("args") or {}}
            if sig:
                call["gemini_signature"] = sig
            blocks.append(call)
            calls.append(call)
        elif part.get("text"):
            block = {"type": "text", "text": part["text"]}
            if sig:
                block["gemini_signature"] = sig
            blocks.append(block)
    return blocks, calls


def usage(response):
    """(input tokens, output tokens); thinking tokens are billed as output."""
    u = response.get("usageMetadata") or {}
    return int(u.get("promptTokenCount") or 0), int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)


def cached_tokens(response):
    return int((response.get("usageMetadata") or {}).get("cachedContentTokenCount") or 0)


def text(messages, instructions, model=None, api_key=None, max_tokens=400):
    response = create(model, instructions, to_contents(messages), [], api_key, max_tokens, 60)
    blocks, _ = output_blocks(response)
    return "".join(b["text"] for b in blocks if b["type"] == "text").strip(), response


def strip(messages):
    """Removes the Gemini-only fields, so the same history can go to Anthropic or OpenAI."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(b, dict) and "gemini_signature" in b for b in c):
            m = {**m, "content": [{k: v for k, v in b.items() if k != "gemini_signature"} if isinstance(b, dict) else b
                                  for b in c]}
        out.append(m)
    return out
