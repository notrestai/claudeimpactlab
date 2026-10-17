"""One place that talks to Claude. Structured outputs, page images, caching, usage logging, mock mode."""
from __future__ import annotations
import base64, json, os, time
from .. import sstlib, store

MODEL_MAIN = os.environ.get("SST_MODEL", "claude-opus-5-5")
MODEL_FAST = os.environ.get("SST_MODEL_FAST", "claude-sonnet-5-5")


# The key is read from the first of these that is set. Some hosted sandboxes drop ANTHROPIC_API_KEY from the
# environment by name (Claude Code sessions authenticate through the account), so SST_API_KEY is the fallback.
API_KEY_VARS = ("ANTHROPIC_API_KEY", "SST_API_KEY")


def api_key() -> str | None:
    for name in API_KEY_VARS:
        v = os.environ.get(name)
        if v:
            return v
    return None


def mock_mode() -> bool:
    return os.environ.get("SST_MOCK") == "1" or not api_key()


class ClaudeError(RuntimeError):
    pass


_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key(), max_retries=2, timeout=600)
    return _client


# Schemas the API refused to compile into an output grammar ("compiled grammar is too large"), by content hash.
# Those calls ask for JSON in the prompt instead and validate the answer locally against the same schema.
_too_large: set[str] = set()


def _schema_key(schema: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def _call(c, kwargs: dict, stream: bool):
    if stream:
        with c.messages.stream(**kwargs) as s:
            return s.get_final_message()
    return c.messages.create(**kwargs)


def _parse_json_text(text: str):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise ClaudeError(f"bad JSON: {e}")


def structured(system, content: list, schema: dict, *, model: str = MODEL_MAIN, max_tokens: int = 16000,
               effort: str = "high", stream: bool = True):
    """Returns (parsed_json, usage_dict, model). Raises ClaudeError on refusal, truncation or bad JSON.

    Uses the API's constrained JSON output when the schema compiles. Some SST sections are too large for the
    output grammar; for those the schema goes into the prompt and the reply is validated here with jsonschema."""
    import anthropic
    c = client()
    key = _schema_key(schema)
    by_prompt = key in _too_large
    if not by_prompt:
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": content}],
                      output_config={"format": {"type": "json_schema", "schema": schema}, "effort": effort})
        try:
            msg = _call(c, kwargs, stream)
        except anthropic.BadRequestError as e:
            if "grammar is too large" not in str(e):
                raise
            _too_large.add(key)
            by_prompt = True
    if by_prompt:
        ask = {"type": "text", "text": "Reply with one JSON object and nothing else (no prose, no code fence). "
                                       "It must validate against this JSON schema exactly, every required key present:\n"
               + json.dumps(schema, ensure_ascii=False)}
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": content + [ask]}],
                      output_config={"effort": effort})
        msg = _call(c, kwargs, stream)
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise ClaudeError(f"stopped: {msg.stop_reason}")
    text = next((b.text for b in msg.content if b.type == "text"), None)
    if text is None:
        raise ClaudeError("no text block in response")
    data = _parse_json_text(text)
    if by_prompt:
        import jsonschema
        errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
        if errors:
            e = errors[0]
            raise ClaudeError(f"schema: {'/'.join(map(str, e.path))}: {e.message[:120]}")
    usage = msg.usage.model_dump() if hasattr(msg.usage, "model_dump") else dict(msg.usage)
    return data, usage, msg.model


def system_blocks(*texts: str) -> list:
    """System prompt as cached blocks: stable text first, cache breakpoint on the last block."""
    blocks = [{"type": "text", "text": t} for t in texts if t]
    if blocks:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def page_blocks(rid: str, pages: list[int], total: int) -> list:
    """[Page N of M] label + image for each page. Cache breakpoint on the last image so section calls share the prefix."""
    from . import ingest
    blocks = []
    for n in pages:
        data = base64.standard_b64encode(ingest.load_page_bytes(rid, n)).decode()
        blocks.append({"type": "text", "text": f"[Page {n} of {total}]"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    if blocks:
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


def logged(rid: str, stage: str, fn, detail: str | None = None):
    """Run fn() -> (result, usage, model); record the run. Re-raises after logging."""
    started = sstlib.now_iso()
    try:
        result, usage, model = fn()
        store.add_run(rid, stage, started, sstlib.now_iso(), model, usage, True, None, detail)
        return result
    except Exception as e:
        store.add_run(rid, stage, started, sstlib.now_iso(), None, None, False, f"{type(e).__name__}: {str(e)[:300]}", detail)
        raise
