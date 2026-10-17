"""Who may see and change what. The backend filters; the frontend never hides anything itself."""
from __future__ import annotations
import copy
from . import sstlib

ROLES = sstlib.ROLES


def check_role(role: str | None) -> str:
    if role not in ROLES:
        raise PermissionError(f"X-Role must be one of {ROLES}")
    return role


def can_write(role: str, path: str) -> bool:
    a = sstlib.annotations().get(path)
    if a is None:
        return False
    if role == "provider":
        return True
    return a["owner"] == role


DOC_LABELS = {"DOC-1": "the referral form", "DOC-2": "the patient information page", "DOC-3": "the insurance authorization form",
              "DOC-4": "the doctor's admission summary", "DOC-5": "recent doctor's notes", "DOC-6": "recent vital signs",
              "DOC-7": "the medication list", "DOC-8": "therapy or nursing notes", "DOC-9": "a lab result", "DOC-10": "the medication supply"}
WHO_LABELS = {"hospital_case_manager": "your hospital case manager", "hospital_nurse": "your hospital nurse", "physician": "your hospital doctor",
              "pt_ot": "the therapy team", "pharmacy": "the hospital pharmacy", "patient": "you", "provider": "the program"}


def patient_safe_gap(g: dict) -> dict:
    """What the patient may see about a gap: which kind of document and who is getting it. Never the clinical detail."""
    if g.get("doc_ids"):
        what = "Still needed from the hospital: " + ", ".join(DOC_LABELS.get(d, "a document") for d in g["doc_ids"][:2])
    elif g.get("who_can_supply") == "patient":
        what = "Something only you can answer; your team will ask you"
    else:
        what = "A detail the program asked the hospital to confirm"
    return {"id": g["id"], "what": what, "who_is_getting_it": WHO_LABELS.get(g.get("who_can_supply"), "the hospital"),
            "status": g["status"], "requested_at": g.get("requested_at"), "received_at": g.get("received_at")}


def filter_for_role(sst: dict, role: str) -> dict:
    if role == "provider":
        return sst
    out = copy.deepcopy(sst)
    if role == "hospital":
        out["analyses"] = []
        out["eligibility"]["coordinator_decision"] = {k: None for k in out["eligibility"]["coordinator_decision"]}
        return out
    # patient: status and logistics only
    ann = sstlib.annotations()
    patient = {
        "meta": {
            "sst_version": out["meta"]["sst_version"], "referral_id": out["meta"]["referral_id"],
            "created_at": out["meta"]["created_at"], "updated_at": out["meta"]["updated_at"], "fictional": out["meta"]["fictional"],
            "packet": {"source_file": None, "page_count": out["meta"]["packet"]["page_count"], "pages": []},
            "parties": out["meta"]["parties"],
        },
        "documents": {k: {"value": f["value"], "status": f["status"]} for k, f in out["documents"].items()},
        "patient_fields": {},
        "gaps": [patient_safe_gap(g) for g in out["gaps"]],
        "patient_summary": out["patient_summary"] if not out["patient_summary"]["requires_staff_review"] else {
            "language": out["patient_summary"]["language"], "requires_staff_review": True, "reviewed_by": None, "reviewed_at": None,
            "where_things_stand": None, "what_is_still_needed": [], "your_options": [], "questions_to_ask_your_team": []},
        "conversations": [{"id": c["id"], "kind": c["kind"], "started_at": c["started_at"], "summary": c["summary"],
                           "follow_ups": c["follow_ups"], "status": c["status"]} for c in out["conversations"] if c["kind"] == "patient_conversation"],
    }
    for path, a in ann.items():
        if a["owner"] == "patient":
            f = sstlib.field(out, path)
            if f is not None:
                patient["patient_fields"][path] = {"value": f["value"], "status": f["status"], "description": a["description"]}
    return patient
