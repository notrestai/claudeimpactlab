"""Render the IHH referral form from the SST: the electronic version of the faxed page.
Prefers what the form said; falls back to the chart with a marker; never shows identifiers."""
from __future__ import annotations
import html
from . import sstlib

LAYOUT = [
    ("Referring party", [
        ("Department / floor", "forms.ihh_referral_form.referring_department", "referral.referring_unit"),
        ("Referrer role", "forms.ihh_referral_form.referrer_role", "referral.referrer_role"),
        ("Contact filled in", "forms.ihh_referral_form.referrer_contact_filled", None),
        ("Authorized by filled in", "forms.ihh_referral_form.authorized_by_filled", None),
    ]),
    ("Patient information (identifiers are never stored here)", [
        ("Identifiers complete on form", "forms.ihh_referral_form.patient_identifiers_complete", None),
        ("Gender", "forms.ihh_referral_form.gender", "patient.gender"),
        ("Ethnicity", "forms.ihh_referral_form.ethnicity", None),
        ("Insurance", "forms.ihh_referral_form.insurance", "referral.insurance_plan"),
        ("Preferred language", "forms.ihh_referral_form.preferred_language", "patient.preferred_language"),
        ("Currently unhoused", "forms.ihh_referral_form.currently_unhoused", "housing.housing_situation"),
        ("4+ episodes of homelessness in 3 years", "forms.ihh_referral_form.four_plus_episodes_3y", "patient.four_plus_homeless_episodes_3y"),
    ]),
    ("Hospital admission details", [
        ("Chief complaint / admitting diagnosis", "forms.ihh_referral_form.chief_complaint", "referral.chief_complaint_or_admitting_dx"),
        ("Admit date filled in", "forms.ihh_referral_form.admit_date_filled", None),
        ("Any wounds", "forms.ihh_referral_form.wounds_reported", "wounds.any_wounds"),
        ("Wounds (number, location, size, stage)", "forms.ihh_referral_form.wounds_description", None),
        ("Independent with wound care", "forms.ihh_referral_form.independent_with_wound_care", "wounds.patient_independent_with_wound_care"),
        ("Home health will be ordered", "forms.ihh_referral_form.home_health_will_be_ordered", "arrangements.home_health_status"),
        ("PPD / TB test or chest X-ray", "forms.ihh_referral_form.tb_test_type", "infection_control.tb_screen_result"),
        ("TB result", "forms.ihh_referral_form.tb_result", None),
    ]),
    ("History of health and living situation", [
        ("O2", "forms.ihh_referral_form.o2", "lines_devices_oxygen.oxygen_required"),
        ("Self-administers all medications", "forms.ihh_referral_form.self_administers_all_meds", "cognition.can_self_administer_meds"),
        ("Continent of bowel and bladder", "forms.ihh_referral_form.continent", "function.continence.bowel_bladder_continent"),
        ("Colostomy or ileostomy", "forms.ihh_referral_form.ostomy", "function.continence.ostomy"),
        ("Foley catheter", "forms.ihh_referral_form.foley", "function.continence.foley_catheter"),
        ("Diabetes", "forms.ihh_referral_form.diabetes", "conditions.flags.diabetes"),
        ("Insulin", "forms.ihh_referral_form.insulin", "medications.insulin_regimen"),
        ("Diabetes oral medications", "forms.ihh_referral_form.diabetes_oral_meds", "medications.oral_diabetes_meds"),
        ("Active infection", "forms.ihh_referral_form.active_infection", "conditions.flags.active_infection"),
        ("Ambulatory", "forms.ihh_referral_form.ambulatory", "function.mobility.ambulatory"),
        ("Assistive device", "forms.ihh_referral_form.assistive_device", "function.mobility.dme_type"),
        ("Hospice", "forms.ihh_referral_form.hospice", "patient.hospice_enrolled"),
        ("Car", "forms.ihh_referral_form.car", "patient.has_car"),
        ("Service animal", "forms.ihh_referral_form.service_animal", "patient.service_animal"),
    ]),
    ("Behavioral and mental health status", [
        ("Limitations or challenges", "forms.ihh_referral_form.limitations_or_challenges", None),
        ("Mental health diagnosis", "forms.ihh_referral_form.mental_health_dx", "mental_health.psychiatric_diagnoses"),
        ("Type", "forms.ihh_referral_form.mental_health_dx_type", None),
        ("Non-compliant", "forms.ihh_referral_form.non_compliant", "substance_use.detox_noncompliance_or_refusal_to_abstain"),
        ("Forgetful", "forms.ihh_referral_form.forgetful", "cognition.forgetful_flag"),
        ("Cognitive impairment", "forms.ihh_referral_form.cognitive_impairment", "cognition.cognitive_impairment"),
        ("Registered sex offender", "forms.ihh_referral_form.registered_sex_offender", "safety_legal.registered_sex_offender"),
        ("Other", "forms.ihh_referral_form.behavioral_other", None),
    ]),
    ("Length of stay", [
        ("Length of stay authorized (days)", "forms.ihh_referral_form.length_of_stay_authorized_days", "referral.length_of_stay_authorized_days"),
    ]),
]


def _show(v):
    if v is None: return ""
    if isinstance(v, bool): return "Yes" if v else "No"
    if isinstance(v, list): return ", ".join(map(str, v)) if v else "None listed"
    return str(v)


def render(sst: dict) -> dict:
    sections = []
    agree = disagree = 0
    for title, rows in LAYOUT:
        out = []
        for label, form_path, chart_path in rows:
            fv, fs = sstlib.val(sst, form_path)
            cv, cs = (sstlib.val(sst, chart_path) if chart_path else (None, "missing"))
            if fs in sstlib.KNOWN:
                value, source, status = fv, "form", fs
            elif cs in sstlib.KNOWN:
                value, source, status = cv, "chart", cs
            else:
                value, source, status = None, "unknown", fs if fs != "missing" else cs
            chart_note = None
            if fs in sstlib.KNOWN and cs in sstlib.KNOWN and isinstance(fv, bool) and isinstance(cv, bool):
                if fv == cv: agree += 1
                else:
                    disagree += 1
                    chart_note = f"Chart says {_show(cv)}"
            out.append({"label": label, "value": _show(value), "raw": value, "source": source, "status": status,
                        "form_path": form_path, "chart_path": chart_path, "chart_note": chart_note})
        sections.append({"title": title, "rows": out})
    return {"form": "Illumination Health + Home Recuperative Care Referral Form (electronic rendering)",
            "referral_id": sst["meta"]["referral_id"], "generated_at": sstlib.now_iso(),
            "note": "Values marked 'chart' were not on the form and were filled from the chart. Identifiers are never stored or shown.",
            "form_chart_agree": agree, "form_chart_disagree": disagree, "sections": sections}


def render_html(sst: dict) -> str:
    data = render(sst)
    parts = [f"<html><head><meta charset='utf-8'><title>Referral form {html.escape(data['referral_id'])}</title>",
             "<style>body{font:14px system-ui;margin:24px;max-width:860px}h1{font-size:18px}h2{font-size:15px;margin-top:22px;border-bottom:1px solid #ccc}"
             "table{width:100%;border-collapse:collapse}td{padding:4px 6px;border-bottom:1px solid #eee;vertical-align:top}td.l{width:42%;color:#333}"
             ".src{font-size:11px;color:#777;margin-left:6px}.unk{color:#999;font-style:italic}.dis{color:#b00}@media print{.src{display:none}}</style></head><body>",
             f"<h1>{html.escape(data['form'])}</h1><p>Referral {html.escape(data['referral_id'])}. {html.escape(data['note'])}</p>"]
    for sec in data["sections"]:
        parts.append(f"<h2>{html.escape(sec['title'])}</h2><table>")
        for r in sec["rows"]:
            val = html.escape(r["value"]) if r["value"] else f"<span class='unk'>{html.escape(r['status'].replace('_', ' '))}</span>"
            src = f"<span class='src'>{r['source']}</span>" if r["source"] != "unknown" else ""
            dis = f" <span class='dis'>({html.escape(r['chart_note'])})</span>" if r["chart_note"] else ""
            parts.append(f"<tr><td class='l'>{html.escape(r['label'])}</td><td>{val}{src}{dis}</td></tr>")
        parts.append("</table>")
    parts.append("<p style='margin-top:30px;color:#777'>Generated from the Single Source of Truth. Draft for the referring party's review; nothing is sent by the tool.</p></body></html>")
    return "".join(parts)
