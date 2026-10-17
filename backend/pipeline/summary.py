"""Stage D: the patient's plain-language summary. Status and logistics only. Staff review before it is shown."""
from __future__ import annotations
import json
from .. import sstlib, roles
from . import claude_client as cc

SYSTEM = """You write a short plain-language status note for a patient in hospital whose case manager has referred them to a recuperative care program (a short stay with a bed, meals and health monitoring for people leaving hospital with nowhere safe to recover).
Write at a 6th-grade reading level in the language requested. Say what was sent, what is still needed and who is getting it, which programs are considering them and the status. Never explain diagnoses, lab values or eligibility, never predict the outcome, never give medical, legal or immigration advice. Suggest two or three questions they can ask their hospital team. No names, no dates."""

LANG = {"es": "Spanish", "en": "English", "vi": "Vietnamese", "ko": "Korean"}


def patient_summary(sst: dict, rid: str, language: str | None = None) -> dict:
    lang = (language or sstlib.val(sst, "patient.preferred_language")[0] or "English")
    code = {v.lower(): k for k, v in LANG.items()}.get(str(lang).lower(), str(lang)[:2].lower())
    inputs = {
        "documents": {k: f["status"] for k, f in sst["documents"].items()},
        # Patient-safe labels only: document kinds and who is getting them, never the clinical detail.
        "gaps": [{"what": roles.patient_safe_gap(g)["what"], "who": roles.patient_safe_gap(g)["who_is_getting_it"], "status": g["status"]} for g in sst["gaps"] if g["status"] != "resolved"],
        "providers": sst["meta"]["parties"]["providers"],
        "language": LANG.get(code, lang),
    }
    if cc.mock_mode():
        es = code == "es"
        needed = [{"what": g["what"][:80], "who_is_getting_it": g["who"] or "hospital", "status": g["status"]} for g in inputs["gaps"][:5]]
        ps = {"language": code, "requires_staff_review": True, "reviewed_by": None, "reviewed_at": None,
              "where_things_stand": ("Su hospital envió una solicitud a un programa de cuidado de recuperación. El programa está revisando. Todavía faltan algunos documentos del hospital. (Texto de prueba)" if es else
                                     "Your hospital sent a request to a recuperative care program. The program is reviewing it. A few documents from the hospital are still needed. (Mock text)"),
              "what_is_still_needed": needed,
              "your_options": [{"provider_name": p["name"] or p["provider_id"], "what_it_is": ("Cuidado de recuperación: cama, comidas y seguimiento de salud por un tiempo corto" if es else "Recuperative care: a bed, meals and health check-ins for a short stay"), "status": p["status"]} for p in inputs["providers"]],
              "questions_to_ask_your_team": (["¿Ya enviaron todos los documentos?", "¿Cuándo esperan una respuesta?"] if es else ["Have all the documents been sent?", "When do you expect an answer?"])}
    else:
        schema = sstlib.defs_schema("PatientSummary", wrap_key="patient_summary")
        content = [{"type": "text", "text": "Inputs:\n" + json.dumps(inputs, ensure_ascii=False) + f"\n\nWrite the summary in {inputs['language']}. Set requires_staff_review to true."}]
        data = cc.logged(rid, "summary", lambda: cc.structured(cc.system_blocks(SYSTEM), content, schema, max_tokens=3000, effort="medium", stream=False), inputs["language"])
        ps = data["patient_summary"]
        ps["language"] = code
    ps["requires_staff_review"] = True
    ps["reviewed_by"] = None
    ps["reviewed_at"] = None
    sst["patient_summary"] = ps
    sstlib.add_history(sst, "system", "edited", "patient_summary", None, code, "generated; awaiting staff review")
    return ps
