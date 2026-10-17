"""Stage E: readiness score. Pure code, explains itself. Not a probability of approval."""
from __future__ import annotations
from .. import sstlib

DEFAULT_WEIGHTS = {"completeness": 0.3, "rule_coverage": 0.3, "rules_met": 0.3, "arrangements": 0.1}
SCORED_KINDS = {"must", "exclude", "document", "conditional", "form_must", "form_exclude"}
ALWAYS_DOCS = ["DOC-1", "DOC-2", "DOC-3", "DOC-4", "DOC-6", "DOC-7", "DOC-8", "DOC-10"]


def _pct(n, d):
    return int(round(100 * n / d)) if d else 0


def score(sst: dict, provider_id: str | None = None) -> dict:
    provider_id = provider_id or sst["eligibility"]["provider_id"] or "ihh"
    profile = sstlib.load_profile(provider_id)
    weights = profile.get("score_weights") or DEFAULT_WEIGHTS
    rules = {r["rule_id"]: r for r in sst["eligibility"]["rules"]}

    required = list(ALWAYS_DOCS)
    if sstlib.val(sst, "conditions.flags.active_infection")[0] is True:
        required.append("DOC-9")
    doc_fields = {d: profile["documents"][d]["sst_fields"] for d in profile["documents"]}
    present, missing = 0, []
    for d in required:
        paths = doc_fields.get(d, [])
        if paths and sstlib.val(sst, paths[0])[0] is True:
            present += 1
        else:
            missing.append(d)
    completeness = {"score": _pct(present, len(required)), "present": present, "required": len(required), "missing_doc_ids": missing}

    applicable = [r for rid, r in rules.items() if profile["rules"][rid]["kind"] in SCORED_KINDS and r["status"] != "not_applicable"]
    answerable = [r for r in applicable if r["status"] in ("met", "not_met", "needs_confirmation")]
    cant = [r["rule_id"] for r in applicable if r["status"] in ("cant_tell", "pending")]
    coverage = {"score": _pct(len(answerable), len(applicable)), "answerable": len(answerable), "applicable": len(applicable), "cant_tell_rule_ids": cant}

    met = [r for r in answerable if r["status"] in ("met", "needs_confirmation")]
    not_met = [r["rule_id"] for r in answerable if r["status"] == "not_met"]
    rules_met = {"score": _pct(len(met), len(answerable)), "met": len(met), "not_met": len(not_met), "not_met_rule_ids": not_met}

    cond = [r for rid, r in rules.items() if profile["rules"][rid]["kind"] == "conditional" and r["status"] != "not_applicable"]
    confirmed = [r for r in cond if r["status"] == "met"]
    pending = [r["rule_id"] for r in cond if r["status"] in ("needs_confirmation", "cant_tell", "not_met")]
    arrangements = {"score": _pct(len(confirmed), len(cond)) if cond else 100, "confirmed": len(confirmed), "needed": len(cond), "pending_rule_ids": pending}

    hard = len(sst["eligibility"]["hard_stops"])
    total = int(round(weights["completeness"] * completeness["score"] + weights["rule_coverage"] * coverage["score"]
                      + weights["rules_met"] * rules_met["score"] + weights["arrangements"] * arrangements["score"]))
    band = "blocked_by_hard_stop" if hard else ("ready_to_decide" if total >= 85 else "nearly_ready" if total >= 65 else "needs_documents")

    shortfalls = sorted([("documents", 100 - completeness["score"], f"{len(missing)} of {len(required)} checklist documents are missing"),
                         ("rule coverage", 100 - coverage["score"], f"{len(cant)} of {len(applicable)} applicable rules cannot be checked yet"),
                         ("rules met", 100 - rules_met["score"], f"{len(not_met)} answerable rule(s) are not met"),
                         ("arrangements", 100 - arrangements["score"], f"{len(pending)} arrangement(s) still to confirm")], key=lambda x: -x[1])
    if hard:
        expl = f"Blocked: {hard} excluding rule(s) met ({', '.join(sst['eligibility']['hard_stops'])}). The number shows how complete the packet was anyway."
    else:
        top = [s for s in shortfalls if s[1] > 0][:2]
        expl = (f"Readiness {total}: " + "; ".join(t[2] for t in top) + ".") if top else f"Readiness {total}: everything applicable is answered and met."
    actions = []
    for g in sst["gaps"]:
        if g["status"] in ("open", "requested") and g["why_rule_ids"]:
            actions.append(f"{g['id']}: {g['what']}")
        if len(actions) == 3:
            break
    scores = {"computed_at": sstlib.now_iso(), "readiness": total, "band": band,
              "components": {"completeness": completeness, "rule_coverage": coverage, "rules_met": rules_met, "arrangements": arrangements, "hard_stop_count": hard},
              "weights": weights, "explanation": expl, "top_actions": actions}
    sst["eligibility"]["scores"] = scores
    sstlib.add_history(sst, "system", "scored", "eligibility.scores", None, str(total), band)
    return scores
