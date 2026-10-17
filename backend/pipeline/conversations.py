"""Stage G: coordination conversations. Consent gate, identifier scrub, summary, proposed SST updates a person accepts."""
from __future__ import annotations
import json
from .. import sstlib, scrub
from . import claude_client as cc

SYSTEM = """You read the transcript of a coordination conversation about a hospital referral to a recuperative care program (a call with the hospital case manager, or a conversation with the patient).
Summarize in three to five plain sentences: what was asked, what was promised, by when. List follow-ups with who owns them. Then propose updates to the structured record: only use field paths from the list given, quote the exact words that support each one, and give the timestamp or position if present. Propose, do not decide. Never output names, dates of birth, phone numbers or record numbers."""

PATIENT_PATHS = {"patient.preferred_language", "patient.interpreter_needed", "patient.has_phone", "patient.has_car", "patient.service_animal"}


def _schema():
    full = sstlib.load_schema()
    conv = full["$defs"]["ConversationRow"]["properties"]
    return sstlib._clean_for_output({
        "type": "object",
        "properties": {"summary": {"type": "string"}, "follow_ups": conv["follow_ups"], "extracted_items": conv["extracted_items"]},
        "required": ["summary", "follow_ups", "extracted_items"], "additionalProperties": False, "$defs": full["$defs"],
    })


def _mock_items(text: str, kind: str) -> list[dict]:
    items = []
    low = text.lower()
    def add(path, value, needle):
        i = low.find(needle)
        if i >= 0:
            start = max(0, text.rfind(".", 0, i) + 1); end = text.find(".", i)
            items.append({"sst_path": path, "proposed_value": value, "quote": text[start:end if end > 0 else None].strip()[:200], "timestamp": None, "status": "proposed", "decided_by": None})
    if kind == "patient_conversation":
        add("patient.has_phone", "true", "phone"); add("patient.preferred_language", "Spanish", "spanish")
    else:
        add("arrangements.pcp_followup_scheduled", "true", "follow-up"); add("infection_control.mrsa_swab_result", "pending", "mrsa")
        add("arrangements.home_health_status", "ordered", "home health"); add("vitals.set_count", "5", "vitals")
    return items


def add_conversation(sst: dict, rid: str, payload: dict, actor_role: str) -> dict:
    if not payload.get("consent_confirmed"):
        raise PermissionError("consent_confirmed must be true before a transcript is stored")
    kind = payload.get("kind") or "hospital_call"
    clean, flags = scrub.scrub(payload.get("transcript_text") or "")
    row = {"id": f"CONV-{len(sst['conversations']) + 1}", "kind": kind, "participants": payload.get("participants") or ["provider", "hospital"],
           "started_at": sstlib.now_iso(), "duration_seconds": payload.get("duration_seconds"), "consent_confirmed": True,
           "consent_note": payload.get("consent_note"), "transcript_source": payload.get("transcript_source") or "pasted_text",
           "language": payload.get("language") or "en", "transcript_text": clean, "identifier_scrub": scrub.result_label(flags),
           "summary": None, "extracted_items": [], "follow_ups": [], "status": "draft"}
    allowed = sorted(PATIENT_PATHS if kind == "patient_conversation" else set(sstlib.annotations()) - PATIENT_PATHS)
    if cc.mock_mode():
        row["summary"] = "MOCK summary (no API key): " + clean[:240]
        row["extracted_items"] = _mock_items(clean, kind)
        row["follow_ups"] = [{"what": g["what"][:80], "owner_role": "hospital", "due": None, "gap_id": g["id"]} for g in sst["gaps"] if g["status"] == "open"][:2]
    else:
        guide = "\n".join(f"- {p}: {sstlib.annotations()[p]['kind']} {sstlib.annotations()[p]['enum'] or ''}. {sstlib.annotations()[p]['description']}" for p in allowed)
        gaps = [{"id": g["id"], "what": g["what"], "status": g["status"]} for g in sst["gaps"] if g["status"] != "resolved"]
        content = [{"type": "text", "text": f"Conversation kind: {kind}. Open gaps:\n{json.dumps(gaps)}\n\nAllowed field paths:\n{guide}\n\nTranscript:\n{clean}"}]
        data = cc.logged(rid, "conversation", lambda: cc.structured(cc.system_blocks(SYSTEM), content, _schema(), max_tokens=4000, effort="medium", stream=False), kind)
        row["summary"] = data["summary"]
        row["follow_ups"] = data["follow_ups"]
        row["extracted_items"] = [i | {"status": "proposed", "decided_by": None} for i in data["extracted_items"] if i.get("sst_path") in allowed]
    sst["conversations"].append(row)
    sstlib.add_history(sst, actor_role, "transcribed", "conversations." + row["id"], None, kind, f"scrub {row['identifier_scrub']}; {len(row['extracted_items'])} proposed")
    return row


def _coerce(path: str, text: str):
    a = sstlib.annotations()[path]
    t = (text or "").strip()
    if a["kind"] == "boolean":
        return t.lower() in ("true", "yes", "y", "1", "si", "sí")
    if a["kind"] in ("integer", "number"):
        try:
            return int(float(t)) if a["kind"] == "integer" else float(t)
        except ValueError:
            return None
    if a["kind"] == "list":
        return [s.strip() for s in t.split(",") if s.strip()]
    if a["kind"] == "enum":
        return t if t in a["enum"] else None
    return t or None


def decide_item(sst: dict, rid: str, cid: str, idx: int, status: str, actor_role: str, actor_label: str | None = None) -> dict:
    conv = next((c for c in sst["conversations"] if c["id"] == cid), None)
    if conv is None:
        raise KeyError(cid)
    item = conv["extracted_items"][idx]
    if status not in ("accepted", "rejected"):
        raise ValueError("status must be accepted or rejected")
    item["status"] = status
    item["decided_by"] = actor_label or actor_role
    if status == "accepted":
        value = _coerce(item["sst_path"], item["proposed_value"])
        sstlib.set_field(sst, item["sst_path"], value, "entered", actor_role,
                         evidence=[sstlib.transcript_evidence(cid, item.get("timestamp"), item["quote"])],
                         note=f"Accepted from {cid}", actor_label=actor_label, action="accepted")
    else:
        sstlib.add_history(sst, actor_role, "rejected", f"conversations.{cid}.extracted_items.{idx}", None, item["sst_path"], None, actor_label)
    if all(i["status"] != "proposed" for i in conv["extracted_items"]):
        conv["status"] = "reviewed"
    return item
