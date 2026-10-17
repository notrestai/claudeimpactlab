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
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": content}],
                  output_config={"format": {"type": "json_schema", "schema": schema}, "effort": effort})
    c = client()
    if stream:
        with c.messages.stream(**kwargs) as s:
            msg = s.get_final_message()
    else:
        msg = c.messages.create(**kwargs)
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise ClaudeError(f"stopped: {msg.stop_reason}")
    text = next((b.text for b in msg.content if b.type == "text"), None)
    if text is None:
        raise ClaudeError("no text block in response")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeError(f"bad JSON: {e}")
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
