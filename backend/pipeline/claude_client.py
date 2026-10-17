"""One place that talks to Claude. Structured outputs, page images, caching, usage logging, mock mode."""
from __future__ import annotations
import base64, json, os, time
from .. import sstlib, store

MODEL_MAIN = os.environ.get("SST_MODEL", "claude-opus-5-5")
MODEL_FAST = os.environ.get("SST_MODEL_FAST", "claude-sonnet-5-5")


def mock_mode() -> bool:
    return os.environ.get("SST_MOCK") == "1" or not os.environ.get("ANTHROPIC_API_KEY")


class ClaudeError(RuntimeError):
    pass


_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=2, timeout=600)
    return _client


def structured(system, content: list, schema: dict, *, model: str = MODEL_MAIN, max_tokens: int = 16000,
               effort: str = "high", stream: bool = True):
    """Returns (parsed_json, usage_dict, model). Raises ClaudeError on refusal, truncation or bad JSON."""
    import anthropic, jsonschema
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": content}],
                  output_config={"format": {"type": "json_schema", "schema": schema}, "effort": effort})
    c = client()

    def send(kw):
        if stream:
            with c.messages.stream(**kw) as s:
                return s.get_final_message()
        return c.messages.create(**kw)

    fell_back = False
    try:
        msg = send(kwargs)
    except anthropic.BadRequestError as e:
        if "grammar" not in str(e).lower():
            raise
        # Grammar too large for constrained decoding: ask for plain JSON and validate it ourselves.
        fell_back = True
        kw2 = dict(kwargs)
        kw2["output_config"] = {"effort": effort}
        kw2["messages"] = [{"role": "user", "content": list(content) + [{"type": "text", "text":
            "Respond with ONLY a JSON object, no prose and no code fences, that validates against this JSON Schema:\n" + json.dumps(schema)}]}]
        msg = send(kw2)
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise ClaudeError(f"stopped: {msg.stop_reason}")
    text = next((b.text for b in msg.content if b.type == "text"), None)
    if text is None:
        raise ClaudeError("no text block in response")
    if fell_back:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        start, end = text.find("{"), text.rfind("}")
        text = text[start:end + 1] if start >= 0 else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeError(f"bad JSON: {e}")
    if fell_back:
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            raise ClaudeError(f"fallback JSON failed schema: {e.message[:200]}")
    usage = msg.usage.model_dump() if hasattr(msg.usage, "model_dump") else dict(msg.usage)
    if fell_back:
        usage["fallback_plain_json"] = True
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
