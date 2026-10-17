"""Stage F: read-only Analyze. Reads the SST (and pages), cites, never writes to the record."""
from __future__ import annotations
import json
from .. import sstlib
from . import claude_client as cc

MODES = {
    "briefing": "Write a six-sentence briefing of this referral for the coordinator: who (age, main problem), function, cognition and mental health, infection control, what the record does not contain, and anything that conflicts. Describe the record; do not predict approval.",
    "question": "Answer the coordinator's question from the record only. If the record does not contain what is needed, say so plainly and list it under unknowns.",
    "compare_sources": "List every place where the referral form (page labelled referral_form) and the chart (H&P, progress notes, PT/OT, nursing) disagree, with both readings and pages. If there are none, say so.",
    "what_would_change": "For each open gap, say which rule results would change if it were closed and how. Order by impact on readiness. Do not predict the final decision.",
}
SYSTEM = """You analyze a structured referral record (the SST) for a recuperative care referral coordinator. Read-only.
Every sentence must rest on a field in the record or a page of the packet, and you list the field paths and page numbers you used.
If the record does not contain what a question needs, say so and list it under unknowns. Never guess, never give medical advice, never state a probability of approval, never output names, dates of birth, record numbers or phone numbers."""

EXCLUDE = ("patient_summary", "history", "analyses", "conversations", "meta")


def analyze(sst: dict, rid: str, mode: str, question: str | None, actor_role: str, with_pages: bool = True) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}")
    row = {"id": f"AN-{len(sst['analyses']) + 1}", "ts": sstlib.now_iso(), "actor_role": actor_role, "mode": mode,
           "question": question, "answer": "", "cited_sst_paths": [], "cited_pages": [], "unknowns": [], "model": None}
    if cc.mock_mode():
        el = sst["eligibility"]
        cant = [r for r in el["rules"] if r["status"] == "cant_tell"][:5]
        row["answer"] = (f"MOCK analysis (no API key). Recommendation so far: {el['recommendation'] or 'not evaluated'}. "
                         f"{len(el['hard_stops'])} hard stop(s). {len(cant)} rule(s) cannot be checked: " + "; ".join(f"{r['rule_id']} ({r['basis']})" for r in cant))
        row["cited_sst_paths"] = sorted({p for r in cant for p in r["sst_fields"]})[:10]
        row["unknowns"] = [r["basis"] for r in cant]
        row["model"] = "mock"
    else:
        record = {k: v for k, v in sst.items() if k not in EXCLUDE}
        total = sst["meta"]["packet"]["page_count"] or 0
        content = (cc.page_blocks(rid, list(range(1, total + 1)), total) if with_pages and total else [])
        content += [{"type": "text", "text": "The structured record (SST):\n" + json.dumps(record, ensure_ascii=False)},
                    {"type": "text", "text": MODES[mode] + (f"\n\nQuestion: {question}" if question else "")}]
        schema = sstlib.defs_schema("AnalysisRow", wrap_key="analysis")
        data = cc.logged(rid, f"analyze:{mode}", lambda: cc.structured(cc.system_blocks(SYSTEM), content, schema, max_tokens=6000, effort="high"), question)
        a = data["analysis"]
        valid_paths = set(sstlib.annotations()) | {"eligibility", "gaps"}
        row.update({"answer": a["answer"], "cited_sst_paths": [p for p in a.get("cited_sst_paths", []) if p in valid_paths or p.startswith(("eligibility", "gaps"))],
                    "cited_pages": [p for p in a.get("cited_pages", []) if isinstance(p, int) and 1 <= p <= max(total, 1)],
                    "unknowns": a.get("unknowns", []), "model": cc.MODEL_MAIN})
        if not row["cited_sst_paths"] and not row["cited_pages"]:
            row["answer"] = "The analysis cited nothing in the record and was discarded. Unknowns: " + "; ".join(row["unknowns"])
    sst["analyses"].append(row)
    sstlib.add_history(sst, actor_role, "analyzed", "analyses." + row["id"], None, mode, (question or "")[:120])
    return row
