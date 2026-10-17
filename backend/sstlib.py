"""Load, navigate, change and validate SST documents. Everything else imports this."""
from __future__ import annotations
import copy, datetime, json, pathlib, uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
SST_DIR = ROOT / "sst"
PROVIDERS_DIR = ROOT / "providers"

KNOWN = {"found", "entered", "confirmed"}
UNKNOWN = {"pending", "not_in_packet", "redacted", "cant_tell", "conflict"}
ROLES = ("provider", "hospital", "patient")

_cache: dict[str, Any] = {}


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def _load(name: str, path: pathlib.Path):
    if name not in _cache:
        _cache[name] = json.loads(path.read_text())
    return _cache[name]


def load_schema() -> dict:
    return _load("schema", SST_DIR / "sst.schema.json")


def load_blank() -> dict:
    return copy.deepcopy(_load("blank", SST_DIR / "sst.blank.json"))


def load_example() -> dict:
    return copy.deepcopy(_load("example", SST_DIR / "example.fictional.json"))


def load_profile(provider_id: str = "ihh") -> dict:
    return _load(f"profile:{provider_id}", PROVIDERS_DIR / provider_id / "profile.json")


def load_criteria_text(provider_id: str = "ihh") -> str:
    return (PROVIDERS_DIR / provider_id / "admission-criteria.md").read_text()


def list_providers() -> list[dict]:
    out = []
    for d in sorted(PROVIDERS_DIR.iterdir()):
        p = d / "profile.json"
        if p.exists():
            prof = json.loads(p.read_text())
            out.append({"provider_id": prof["provider_id"], "name": prof["name"], "profile_version": prof["profile_version"]})
    return out


def new_sst(referral_id: str | None = None) -> dict:
    sst = load_blank()
    sst["meta"]["referral_id"] = referral_id or f"REF-{uuid.uuid4().hex[:8].upper()}"
    sst["meta"]["created_at"] = now_iso()
    sst["meta"]["updated_at"] = sst["meta"]["created_at"]
    return sst


# ---------- schema annotations: path -> owner, rules, docs, patient_visible, kind ----------

def annotations() -> dict[str, dict]:
    if "annotations" in _cache:
        return _cache["annotations"]
    schema = load_schema()
    defs = schema["$defs"]
    out: dict[str, dict] = {}

    def walk(node: dict, path: list[str]):
        for key, sub in node.get("properties", {}).items():
            ref = sub.get("$ref", "")
            if ref.startswith("#/$defs/Field_"):
                fdef = defs[ref.split("/")[-1]]
                vs = fdef["properties"]["value"]
                if "enum" in vs:
                    kind = "enum"
                elif vs.get("type") == "array":
                    kind = "rows" if "$ref" in vs.get("items", {}) else "list"
                else:
                    t = vs.get("type")
                    kind = (t[0] if isinstance(t, list) else t) or "text"
                out[".".join(path + [key])] = {
                    "owner": sub.get("x-owner", "hospital"),
                    "rules": sub.get("x-rules", []),
                    "docs": sub.get("x-docs", []),
                    "patient_visible": sub.get("x-patient_visible", False),
                    "description": sub.get("description", ""),
                    "kind": kind,
                    "enum": [e for e in vs.get("enum", []) if e is not None],
                    "section": path[0] if path else key,
                }
            elif sub.get("type") == "object" and "properties" in sub:
                walk(sub, path + [key])

    for sec, node in schema["properties"].items():
        if isinstance(node, dict) and node.get("type") == "object" and "properties" in node:
            walk(node, [sec])
    _cache["annotations"] = out
    return out


def field_paths() -> list[str]:
    return list(annotations().keys())


# ---------- navigation ----------

def get(sst: dict, path: str) -> Any:
    node = sst
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def field(sst: dict, path: str) -> dict | None:
    f = get(sst, path)
    return f if isinstance(f, dict) and "status" in f and "value" in f else None


def val(sst: dict, path: str):
    """(value, status) for a wrapped field; (None, 'missing') if the path is not a field."""
    f = field(sst, path)
    if f is None:
        return None, "missing"
    return f["value"], f["status"]


def known(sst: dict, path: str) -> bool:
    return val(sst, path)[1] in KNOWN


def set_field(sst: dict, path: str, value, status: str, by: str, evidence: list | None = None,
              note: str | None = None, confidence: str | None = None, conflicts: list | None = None,
              actor_label: str | None = None, action: str = "edited") -> dict:
    f = field(sst, path)
    if f is None:
        raise KeyError(f"not an SST field: {path}")
    old = json.dumps(f["value"]) if f["value"] is not None else None
    f["value"] = value
    f["status"] = status
    f["evidence"] = evidence if evidence is not None else f.get("evidence", [])
    if conflicts is not None:
        f["conflicts"] = conflicts
    f["updated_by"] = by
    f["updated_at"] = now_iso()
    if confidence is not None:
        f["confidence"] = confidence
    if note is not None:
        f["note"] = note
    add_history(sst, by, action, path, old, json.dumps(value) if value is not None else None, note, actor_label)
    return f


def add_history(sst: dict, actor_role: str, action: str, path: str | None, old=None, new=None,
                note: str | None = None, actor_label: str | None = None):
    sst["history"].append({
        "ts": now_iso(), "actor_role": actor_role, "actor_label": actor_label or actor_role,
        "action": action, "path": path, "old_value": old, "new_value": new, "note": note,
    })
    sst["meta"]["updated_at"] = now_iso()


def packet_evidence(page: int, doc_type: str | None, quote: str) -> dict:
    return {"source": "packet", "page": page, "doc_type": doc_type, "ref": None, "timestamp": None, "quote": quote}


def transcript_evidence(conversation_id: str, timestamp: str | None, quote: str) -> dict:
    return {"source": "transcript", "page": None, "doc_type": None, "ref": conversation_id, "timestamp": timestamp, "quote": quote}


# ---------- validation ----------

def validate(sst: dict) -> list[str]:
    import jsonschema
    v = jsonschema.Draft202012Validator(load_schema())
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in sorted(v.iter_errors(sst), key=lambda e: list(e.path))]


# ---------- structured-output schemas ----------

def _clean_for_output(node):
    """Drop x-* annotations and turn enum-with-null into anyOf, recursively."""
    if isinstance(node, list):
        return [_clean_for_output(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k.startswith("x-"):
            continue
        out[k] = _clean_for_output(v)
    if "enum" in out and None in out["enum"]:
        vals = [e for e in out["enum"] if e is not None]
        desc = out.get("description")
        out = {"anyOf": [{"type": "string", "enum": vals}, {"type": "null"}]}
        if desc:
            out["description"] = desc
    return out


def _prune_defs(node: dict, defs: dict) -> dict:
    """Keep only the $defs the node reaches through $ref (transitively). The API compiles every definition it is
    given into the output grammar; sending all 47 SST definitions on every call made the grammar too large."""
    keep: set[str] = set()

    def walk(n):
        if isinstance(n, dict):
            ref = n.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref[len("#/$defs/"):]
                if name not in keep and name in defs:
                    keep.add(name)
                    walk(defs[name])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return {k: defs[k] for k in defs if k in keep}


# Bookkeeping the extractor never needs to produce; the pipeline fills them after the call (extract._postprocess).
OUTPUT_DROP = {"Field_": ("updated_by", "updated_at"), "Evidence": ("source", "ref", "timestamp")}


def _slim_defs(defs: dict) -> dict:
    """Drop the bookkeeping properties from Field_* and Evidence so the output grammar stays small."""
    out = {}
    for name, d in defs.items():
        drop = OUTPUT_DROP["Field_"] if name.startswith("Field_") else OUTPUT_DROP.get(name, ())
        if drop and isinstance(d, dict) and "properties" in d:
            d = dict(d)
            d["properties"] = {k: v for k, v in d["properties"].items() if k not in drop}
            d["required"] = [r for r in d.get("required", []) if r not in drop]
        out[name] = d
    return out


def section_schema(section: str) -> dict:
    """A self-contained schema for one top-level SST section, for Claude's structured output."""
    schema = load_schema()
    sec = schema["properties"][section]
    return _clean_for_output({
        "type": "object",
        "properties": {section: sec},
        "required": [section],
        "additionalProperties": False,
        "$defs": _slim_defs(_prune_defs(sec, schema["$defs"])),
    })


def defs_schema(def_name: str, wrap_key: str | None = None, as_array: bool = False) -> dict:
    """Schema for one $def (for example PageRow rows or an AnalysisRow)."""
    schema = load_schema()
    item = {"$ref": f"#/$defs/{def_name}"}
    body = {"type": "array", "items": item} if as_array else item
    key = wrap_key or def_name.lower()
    return _clean_for_output({
        "type": "object", "properties": {key: body}, "required": [key], "additionalProperties": False,
        "$defs": _prune_defs(body, schema["$defs"]),
    })


def section_field_guide(section: str) -> str:
    """Plain list of the section's fields and meanings, for prompts."""
    lines = []
    for path, a in annotations().items():
        if a["section"] != section:
            continue
        kind = a["kind"] + (" (" + ", ".join(a["enum"]) + ")" if a["enum"] else "")
        lines.append(f"- {path.split('.', 1)[1]}: {kind}. {a['description']}")
    return "\n".join(lines)


SECTIONS = [s for s in load_blank().keys() if s not in ("meta", "eligibility", "gaps", "patient_summary", "conversations", "analyses", "history")]
