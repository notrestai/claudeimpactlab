"""Gaps: what to ask the hospital (or the patient) for, and the draft reply. Nothing is sent."""
from __future__ import annotations
from .. import sstlib

WHO_BY_SECTION = {
    "documents": "hospital_case_manager", "referral": "hospital_case_manager", "arrangements": "hospital_case_manager",
    "vitals": "hospital_nurse", "labs": "hospital_nurse", "infection_control": "hospital_nurse", "medications": "pharmacy",
    "function": "pt_ot", "cognition": "hospital_nurse", "mental_health": "physician", "substance_use": "physician",
    "conditions": "physician", "disease_specific": "physician", "imaging_cardiac": "physician", "lines_devices_oxygen": "hospital_nurse",
    "wounds": "hospital_nurse", "housing": "hospital_case_manager", "safety_legal": "hospital_case_manager", "patient": "patient",
}


def _who(paths):
    ann = sstlib.annotations()
    for p in paths:
        a = ann.get(p)
        if a and a["owner"] == "patient":
            return "patient"
    for p in paths:
        sec = p.split(".")[0]
        if sec in WHO_BY_SECTION:
            return WHO_BY_SECTION[sec]
    return "hospital_case_manager"


def build_gaps(sst: dict) -> list[dict]:
    ann = sstlib.annotations()
    profile = sstlib.load_profile(sst["eligibility"]["provider_id"] or "ihh")
    old = {(tuple(g["sst_fields"]), tuple(g["why_rule_ids"])): g for g in sst["gaps"]}
    new: list[dict] = []
    seen_docs = set()

    def add(what, rule_ids, paths, doc_ids, who, draft):
        key = (tuple(paths), tuple(rule_ids))
        prev = old.get(key)
        g = {"id": prev["id"] if prev else f"G{len(new) + 1}", "what": what, "why_rule_ids": rule_ids, "doc_ids": doc_ids,
             "sst_fields": paths, "who_can_supply": who, "status": prev["status"] if prev else "open",
             "requested_at": prev["requested_at"] if prev else None, "received_at": prev["received_at"] if prev else None,
             "draft_request_text": draft, "note": prev["note"] if prev else None}
        new.append(g)

    for r in sst["eligibility"]["rules"]:
        rule = profile["rules"][r["rule_id"]]
        if rule["kind"] in ("ask", "note", "any_of_general"):
            continue
        unknown = [p for p in r["sst_fields"] if sstlib.val(sst, p)[1] not in sstlib.KNOWN and sstlib.val(sst, p)[1] != "missing"]
        docs = sorted({d for p in unknown for d in ann.get(p, {}).get("docs", [])})
        if r["status"] == "cant_tell":
            names = [ann[p]["description"].split(".")[0] for p in unknown if p in ann][:3] or [rule["text"][:80]]
            what = "; ".join(names)
            add(what, [r["rule_id"]], unknown or r["sst_fields"], docs, _who(unknown or r["sst_fields"]),
                f"To review this referral we still need: {what}. (IHH rule {r['rule_id']}: {rule['text'][:140]})")
            seen_docs.update(docs)
        elif r["status"] == "needs_confirmation":
            add("Confirm before admission: " + r["basis"], [r["rule_id"]], r["sst_fields"], docs, "hospital_case_manager",
                f"Please confirm before admission: {r['basis']}")
        elif r["status"] == "not_met" and rule["kind"] in ("conditional", "document"):
            add("Arrange: " + r["basis"], [r["rule_id"]], r["sst_fields"], docs, "hospital_case_manager",
                f"Admission requires: {rule['text'][:160]}. Current record: {r['basis']}")
    for d, info in profile["documents"].items():
        paths = info["sst_fields"]
        if not paths or d in seen_docs:
            continue
        v, st = sstlib.val(sst, paths[0])
        if v is not True and d not in ("DOC-5", "DOC-9"):
            add(f"Send {info['text']}", [], paths, [d], "hospital_case_manager", f"Please send the {info['text']} ({d}).")
    for p, a in ann.items():
        f = sstlib.field(sst, p)
        if f and f["status"] == "conflict":
            add(f"Resolve conflict: {a['description'].split('.')[0]}", [], [p], a["docs"], _who([p]),
                f"The packet gives different values for {a['description'].split('.')[0].lower()}; please confirm which is current.")
    sst["gaps"] = new
    return new


def draft_reply(sst: dict) -> str:
    el = sst["eligibility"]
    profile = sstlib.load_profile(el["provider_id"] or "ihh")
    lines = ["Re: recuperative care referral " + sst["meta"]["referral_id"], ""]
    rec = el["recommendation"]
    if rec == "likely_ineligible":
        lines.append("At this time the referral does not meet our admission criteria, for the following reason(s):")
        for rid in el["hard_stops"]:
            r = next(x for x in el["rules"] if x["rule_id"] == rid)
            lines.append(f"  - {r['basis']} ({rid}: {profile['rules'][rid]['text'][:120]})")
    elif rec == "likely_eligible":
        lines.append("The referral appears to meet our admission criteria. A coordinator will confirm and call to arrange admission.")
    else:
        lines.append("To complete our review we need the following:")
        for g in sst["gaps"]:
            if g["status"] in ("open", "requested"):
                lines.append(f"  - {g['what']}" + (f" ({', '.join(g['doc_ids'])})" if g["doc_ids"] else ""))
    if el["open_questions"]:
        lines += ["", "Questions:"] + [f"  - {q['question']}" for q in el["open_questions"]]
    lines += ["", "Draft prepared by the SST tool for the referral coordinator's review. Not sent."]
    return "\n".join(lines)


def run_all(sst: dict, provider_id: str = "ihh") -> dict:
    """evaluate -> gaps -> score, in order. Returns the eligibility block."""
    from . import evaluate as ev, score as sc
    ev.evaluate(sst, provider_id)
    build_gaps(sst)
    sc.score(sst, provider_id)
    return sst["eligibility"]
