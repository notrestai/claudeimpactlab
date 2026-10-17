#!/usr/bin/env python3
"""
Builds everything derived from the SST field table, so they never drift apart:

  sst/sst.schema.json              JSON Schema (draft 2020-12) for one SST document
  sst/sst.blank.json               an empty SST, every field "pending" (what the extractor fills)
  sst/example.fictional.json       a made-up, clearly fictional SST for UI work and tests
  providers/ihh/profile.json       IHH's rules mapped to the SST fields that answer them
  docs/02b-sst-field-reference.md  one table per section: field, type, who supplies it, rules it feeds

Run:  python3 sst/build.py
Edit the FIELDS table below to add or change a field, then re-run.
"""
import json, pathlib, sys, copy, datetime, re

ROOT = pathlib.Path(__file__).resolve().parents[1]

STATUS = ["pending", "found", "entered", "confirmed", "not_in_packet", "redacted", "cant_tell", "conflict"]
STATUS_MEANING = {
    "pending":       "Nobody has looked yet. The blank SST starts here.",
    "found":         "Read from the packet. Evidence lists the page(s) and quote.",
    "entered":       "Typed in by a party (hospital, patient or provider), not read from the packet.",
    "confirmed":     "A person checked an extracted value and confirmed it.",
    "not_in_packet": "Looked for it; the packet does not contain it. Unknown, never 'normal'.",
    "redacted":      "The packet has the field but it is blacked out for privacy (names, dates). Not a missing document.",
    "cant_tell":     "Information is present but not enough to answer (needs a date or hours, or is illegible).",
    "conflict":      "Two places in the packet disagree. Both are listed under conflicts; a person decides.",
}
ACTORS = ["extractor", "hospital", "patient", "provider", "system"]
OWNERS = ["hospital", "patient", "provider", "system"]


def F(path, kind, owner, rules=(), docs=(), pv=False, desc=""):
    return dict(path=path, kind=kind, owner=owner, rules=list(rules), docs=list(docs), pv=pv, desc=desc)


SECTIONS = {
    "documents": "The referral checklist from IHH's form (DOC-1 to DOC-10): is each document in the packet, and on which pages.",
    "referral": "Facts about the referral itself: who sent it, why the patient is in hospital, insurance and authorization, and where the patient is coming from (inpatient vs emergency department changes several rules).",
    "patient": "Non-identifying facts about the person. No names, dates of birth, record numbers or addresses are ever stored here.",
    "housing": "Why recuperative care is being requested (IHH general criteria GEN-1 to GEN-3).",
    "function": "What the person can do for themselves: daily living activities, continence, mobility and transfers.",
    "cognition": "Orientation and the ability to manage one's own medications and learn.",
    "mental_health": "Psychiatric stability and the behaviours IHH cannot accept.",
    "substance_use": "Alcohol and drug facts that the admission rules turn on.",
    "safety_legal": "Registered sex offender status, handled case by case under IHH's rules.",
    "infection_control": "Swabs, isolation and the infections that block admission until cleared.",
    "medications": "The medication list and the medication facts the rules ask about.",
    "lines_devices_oxygen": "IV lines, oxygen, breathing devices, catheters, dialysis and fixation devices.",
    "wounds": "Every wound, with the details IHH's wound rules need.",
    "vitals": "The last sets of vital signs and what they show.",
    "labs": "Glucose, troponin, cultures and the few other labs the rules name.",
    "imaging_cardiac": "Chest imaging, EKG and heart function.",
    "conditions": "Diagnoses and the condition flags that map onto IHH's eligible and ineligible lists by body system.",
    "disease_specific": "The extra questions IHH asks for diabetes, infection, respiratory and cardiac cases.",
    "arrangements": "Things that must be set up before the person arrives: home health, wound care, therapy, hospice, follow-up visits, oxygen delivery.",
    "forms": "The forms inside the packet as written: IHH's referral form and the CalAIM authorization form. Kept separate from the chart sections so the record can show where the form and the chart disagree, and so a filled electronic form can be generated from the SST.",
}

FIELDS = [
    # ---- documents (checklist) ----
    F("documents.doc_1_referral_form", "bool", "hospital", [], ["DOC-1"], True, "IHH referral form present"),
    F("documents.doc_2_demographics", "bool", "hospital", [], ["DOC-2"], True, "Face sheet / demographic page present"),
    F("documents.doc_3_authorization", "bool", "hospital", [], ["DOC-3"], True, "CalOptima / CalAIM authorization form, LOA or auth present"),
    F("documents.doc_4_history_and_physical", "bool", "hospital", [], ["DOC-4"], True, "History and physical present"),
    F("documents.doc_5_progress_notes", "bool", "hospital", [], ["DOC-5"], True, "Current progress notes present"),
    F("documents.doc_6_vitals_last_24h", "bool", "hospital", ["ID-1", "RESP-2", "CARD-1"], ["DOC-6"], True, "Vital signs from the last 24 hours present"),
    F("documents.doc_7_medication_list", "bool", "hospital", ["COG-1"], ["DOC-7"], True, "Medication list present"),
    F("documents.doc_8_pt_ot_or_nursing_notes", "bool", "hospital", ["MOB-2"], ["DOC-8"], True, "PT/OT or nursing notes present"),
    F("documents.doc_9_cultures", "bool", "hospital", ["ID-Q2"], ["DOC-9"], True, "Blood or wound culture results present (if an infection is involved)"),
    F("documents.doc_10_meds_30_day_supply", "bool", "hospital", [], ["DOC-10"], True, "30-day supply of all medications arranged (usually confirmed by the hospital, rarely in the packet)"),

    # ---- referral ----
    F("referral.referring_facility", "text", "hospital", [], ["DOC-1"], True, "Hospital name. Redacted in the event packets."),
    F("referral.referring_unit", "text", "hospital", [], ["DOC-1"], False, "Department or floor"),
    F("referral.referrer_role", "text", "hospital", [], ["DOC-1"], False, "Case manager, social worker or nurse who sent it"),
    F("referral.source_setting", "enum:inpatient|emergency_department|other", "hospital", ["INF-1", "INF-2", "DM-1", "DM-2", "DM-4"], ["DOC-4"], True, "Where the patient is being referred from. Several rules only apply to inpatient or only to the emergency department."),
    F("referral.chief_complaint_or_admitting_dx", "text", "hospital", ["GEN-1"], ["DOC-1", "DOC-4"], True, "Why the patient is in hospital, as written"),
    F("referral.admit_date", "text", "hospital", ["MH-4", "DM-3"], ["DOC-1", "DOC-4"], False, "Admission date. Redacted in the event packets, so hour-based rules are cant_tell."),
    F("referral.anticipated_discharge_date", "text", "hospital", [], ["DOC-1"], True, "When the hospital expects to discharge"),
    F("referral.length_of_stay_authorized_days", "int", "hospital", [], ["DOC-1", "DOC-3"], True, "Days authorized, from the referral form"),
    F("referral.insurance_plan", "text", "hospital", [], ["DOC-2", "DOC-3"], True, "Health plan (for example CalOptima Health)"),
    F("referral.authorization_status", "enum:not_requested|requested|approved|denied", "hospital", [], ["DOC-3"], True, "Status of the health plan authorization for recuperative care"),
    F("referral.sent_to_other_providers", "bool", "hospital", [], [], True, "Whether the hospital also referred to other recuperative care providers"),

    # ---- patient (non-identifying) ----
    F("patient.age_years", "int", "hospital", [], ["DOC-2", "DOC-4"], True, "Age in years. Charts sometimes disagree page to page; record a conflict."),
    F("patient.gender", "text", "hospital", [], ["DOC-2"], True, "As recorded"),
    F("patient.preferred_language", "text", "patient", [], ["DOC-1", "DOC-2"], True, "Preferred language. Drives the patient view's language."),
    F("patient.interpreter_needed", "bool", "patient", [], [], True, "Needs an interpreter"),
    F("patient.has_phone", "bool", "patient", [], [], True, "Has a working phone (patient-supplied)"),
    F("patient.has_car", "bool", "patient", [], ["DOC-1"], True, "Referral form asks this"),
    F("patient.service_animal", "bool", "patient", [], ["DOC-1"], True, "Referral form asks this"),
    F("patient.hospice_enrolled", "bool", "hospital", ["EOL-1"], ["DOC-1", "DOC-4"], True, "On hospice"),
    F("patient.four_plus_homeless_episodes_3y", "bool", "hospital", [], ["DOC-1"], False, "Referral form: 4+ episodes of homelessness in the past 3 years"),

    # ---- housing / basis ----
    F("housing.housing_situation", "enum:unhoused|at_risk|inadequate_environment|housed", "hospital", ["GEN-1", "FORM-M1"], ["DOC-1", "DOC-4"], True, "Housing status as documented"),
    F("housing.too_ill_to_recover_outside", "bool", "hospital", ["GEN-1", "GEN-2"], ["DOC-4", "DOC-5"], False, "Too ill or frail to recover on the street or in a shelter, as a clinician states it about this patient (a form's eligibility wording does not count)"),
    F("housing.lives_alone_no_supports", "bool", "hospital", ["GEN-2"], ["DOC-4"], False, "Lives alone with no formal supports"),
    F("housing.readmission_risk", "bool", "hospital", ["GEN-3"], ["DOC-4", "DOC-5"], False, "At risk of hospitalization or re-hospitalization"),

    # ---- function ----
    F("function.adls.bathing", "enum:independent|seated_shower_independent|needs_assist", "hospital", ["ADL-1", "ADL-2", "FORM-M4", "FORM-X2"], ["DOC-8"], False, "Bathing / showering. A seated shower done alone counts as independent."),
    F("function.adls.gathers_supplies_and_transfers_to_shower", "bool", "hospital", ["ADL-2"], ["DOC-8"], False, "Can gather own supplies and get to the shower alone"),
    F("function.adls.dressing", "enum:independent|needs_assist", "hospital", ["ADL-1", "FORM-M4"], ["DOC-8"], False, "Dressing"),
    F("function.adls.toileting", "enum:independent|changes_own_briefs|needs_assist", "hospital", ["ADL-3", "FORM-M4"], ["DOC-8"], False, "Toileting, or changing own briefs"),
    F("function.adls.eating", "enum:independent|needs_assist", "hospital", ["ADL-1"], ["DOC-8"], False, "Eating"),
    F("function.adls.hygiene_independent", "bool", "hospital", ["ADL-1", "ADL-2"], ["DOC-8"], False, "Personal hygiene done independently"),
    F("function.continence.bowel_bladder_continent", "bool", "hospital", [], ["DOC-1", "DOC-8"], False, "Continent of bowel and bladder"),
    F("function.continence.uses_briefs", "bool", "hospital", ["ADL-3"], ["DOC-8"], False, "Uses briefs / diapers"),
    F("function.continence.ostomy", "bool", "hospital", [], ["DOC-1"], False, "Colostomy or ileostomy"),
    F("function.continence.foley_catheter", "bool", "hospital", ["GU-E6"], ["DOC-1", "DOC-8"], False, "Indwelling catheter"),
    F("function.continence.can_empty_catheter_bag", "bool", "hospital", ["GU-E6"], ["DOC-8"], False, "Can empty the catheter bag alone"),
    F("function.continence.self_catheterizes", "bool", "hospital", ["GU-E5", "GU-X1"], ["DOC-8"], False, "Intermittent catheterization done by the patient (eligible) vs by staff (not eligible)"),
    F("function.mobility.ambulatory", "bool", "hospital", ["MOB-3", "FORM-M6"], ["DOC-1", "DOC-8"], False, "Walks"),
    F("function.mobility.ambulation_distance_ft", "number", "hospital", ["MOB-3", "FORM-M6", "FORM-X3"], ["DOC-8"], False, "Farthest documented walking distance in feet (PT note). Rest breaks are fine."),
    F("function.mobility.ambulation_assist_level", "enum:independent|modified_independent|supervision|contact_guard|min_assist|mod_assist|max_assist|non_ambulatory", "hospital", ["MOB-3"], ["DOC-8"], False, "Assistance level for walking, as PT writes it"),
    F("function.mobility.dme_type", "list", "hospital", ["MOB-1"], ["DOC-1", "DOC-8"], True, "Equipment used: walker, cane, wheelchair, shower bench"),
    F("function.mobility.dme_independent", "bool", "hospital", ["MOB-1", "FORM-X3"], ["DOC-8"], False, "Uses the equipment without help"),
    F("function.mobility.wheelchair_dependent", "bool", "hospital", ["MOB-4"], ["DOC-8"], False, "Relies on a wheelchair. Eligible if transfers are independent (outline wins over the form)."),
    F("function.mobility.transfers_independent", "bool", "hospital", ["MOB-4", "FORM-M6", "FORM-X3"], ["DOC-8"], False, "Bed to chair and similar transfers done alone"),
    F("function.mobility.weight_bearing_status", "text", "hospital", ["MSK-E1", "MSK-X1"], ["DOC-8"], False, "For example NWB left leg"),
    F("function.mobility.pt_ot_recommendation", "text", "hospital", ["MOB-2"], ["DOC-8"], False, "Where PT/OT recommends discharge to (home, SNF, acute rehab)"),

    # ---- cognition ----
    F("cognition.orientation", "enum:AOx4|AOx3|AOx2|AOx1|AOx0", "hospital", ["COG-2", "FORM-M3"], ["DOC-4", "DOC-5", "DOC-8"], False, "Alert and oriented level, latest documented"),
    F("cognition.cognitive_impairment", "bool", "hospital", ["FORM-X1", "NEU-X4"], ["DOC-4", "DOC-5"], False, "Dementia, Alzheimer's or other cognitive impairment documented"),
    F("cognition.forgetful_flag", "bool", "hospital", ["COG-1"], ["DOC-1"], False, "Referral form flags forgetfulness"),
    F("cognition.can_self_administer_meds", "bool", "hospital", ["COG-1", "FORM-M5"], ["DOC-1", "DOC-8"], False, "Takes own medications"),
    F("cognition.can_self_administer_insulin", "bool", "hospital", ["COG-1", "DM-6"], ["DOC-8"], False, "Gives own insulin (must be confirmed when on insulin)"),
    F("cognition.can_receive_education", "bool", "hospital", ["COG-1"], ["DOC-8"], False, "Can take in medical teaching"),
    F("cognition.understands_glucometer", "bool", "hospital", ["DM-5"], ["DOC-8"], False, "Knows how to check own blood sugar"),

    # ---- mental health ----
    F("mental_health.psychiatric_diagnoses", "list", "hospital", ["MH-1"], ["DOC-4"], False, "Diagnoses as written"),
    F("mental_health.stable_on_medications", "bool", "hospital", ["MH-1", "FORM-M2"], ["DOC-4", "DOC-5"], False, "Mentally stable on current medications"),
    F("mental_health.suicidal_ideation", "bool", "hospital", ["MH-2"], ["DOC-4", "DOC-5"], False, "Current suicidal ideation"),
    F("mental_health.command_hallucinations", "bool", "hospital", ["MH-3"], ["DOC-4", "DOC-5"], False, "Commanding auditory or visual hallucinations"),
    F("mental_health.conservatorship_or_fsp", "bool", "hospital", ["MH-5"], ["DOC-4"], False, "Under, qualifies for, or history of conservatorship or Full Service Partnership"),
    F("mental_health.aggressive_toward_staff_or_patients", "bool", "hospital", ["OTH-1", "FORM-X7"], ["DOC-5", "DOC-8"], False, "Verbal or physical aggression toward staff or other patients documented during this stay. A danger-to-others hold or threats toward family before admission are not this; leave cant_tell and note them for a person to judge"),
    F("mental_health.psychiatry_consult_present", "bool", "hospital", ["MH-1"], ["DOC-5"], False, "A psychiatry consult is in the packet"),

    # ---- substance use ----
    F("substance_use.admitted_with_acute_alcohol_intoxication", "bool", "hospital", ["MH-4"], ["DOC-4"], False, "Admitted intoxicated"),
    F("substance_use.hours_since_intoxication_admission", "number", "hospital", ["MH-4"], ["DOC-4", "DOC-5"], False, "Hours held since admission. cant_tell when dates are redacted."),
    F("substance_use.ciwa_latest_score", "number", "hospital", ["MH-4"], ["DOC-5", "DOC-8"], False, "Latest CIWA-Ar score (under 8 is acceptable)"),
    F("substance_use.withdrawal_signs", "bool", "hospital", ["MH-4", "NEU-E1"], ["DOC-5", "DOC-8"], False, "Signs of alcohol withdrawal documented"),
    F("substance_use.detox_noncompliance_or_refusal_to_abstain", "bool", "hospital", ["FORM-X5"], ["DOC-5"], False, "Form exclusion: non-compliance with detox or refusal to abstain"),
    F("substance_use.active_substances", "list", "hospital", [], ["DOC-4"], False, "Substances in active use, as written"),

    # ---- safety / legal ----
    F("safety_legal.registered_sex_offender", "bool", "hospital", ["OTH-2", "FORM-X6"], ["DOC-1"], False, "Registered sex offender. Case by case; never auto-deny."),
    F("safety_legal.offense_involved_violence", "bool", "hospital", ["OTH-2"], ["DOC-1"], False, "If a registered offender, whether the offense involved violence"),

    # ---- infection control ----
    F("infection_control.mrsa_swab_result", "enum:negative|positive|pending|not_done", "hospital", ["INF-1", "FORM-X4"], ["DOC-5", "DOC-9"], False, "MRSA nasal swab result (needed if coming from inpatient)"),
    F("infection_control.covid_test_result", "enum:negative|positive|pending|not_done", "hospital", ["INF-2"], ["DOC-5", "DOC-9"], False, "COVID test result"),
    F("infection_control.covid_test_within_24h_of_admission", "bool", "hospital", ["INF-2"], ["DOC-5"], False, "Whether the swab is within 24 hours of arrival. Usually cant_tell before an admission date is set."),
    F("infection_control.isolation_status", "enum:none|contact|droplet|airborne", "hospital", ["OTH-4", "ID-Q3"], ["DOC-5", "DOC-8"], False, "Current isolation precautions"),
    F("infection_control.tb_screen_result", "enum:negative|positive|pending|not_done", "hospital", ["FORM-X4"], ["DOC-1", "DOC-5"], False, "PPD / TB test or chest X-ray screen for TB"),
    F("infection_control.c_diff", "enum:negative|positive|not_tested", "hospital", ["GI-X3", "FORM-X4"], ["DOC-5", "DOC-9"], False, "C. difficile status"),
    F("infection_control.active_infection_cleared_by_physician", "bool", "hospital", ["FORM-X4"], ["DOC-5"], False, "A doctor has documented the infection as cleared or not contagious"),

    # ---- medications ----
    F("medications.list_present", "bool", "hospital", [], ["DOC-7"], True, "A medication list is in the packet"),
    F("medications.medication_count", "int", "hospital", [], ["DOC-7"], False, "Number of active medications"),
    F("medications.high_risk_categories", "list", "hospital", ["COG-1"], ["DOC-7"], False, "Present categories: insulin, anticoagulant, opioid, iv_antibiotic, chemotherapy, controlled_substance, oxygen"),
    F("medications.insulin_regimen", "bool", "hospital", ["DM-Q1", "DM-3", "DM-6"], ["DOC-7"], False, "On insulin"),
    F("medications.insulin_pump", "bool", "hospital", ["DM-Q1"], ["DOC-7"], False, "Insulin by pump"),
    F("medications.insulin_started_less_than_24h_before_admission", "bool", "hospital", ["DM-3"], ["DOC-5", "DOC-7"], False, "Insulin regimen started under 24 hours before recuperative care admission. Usually cant_tell."),
    F("medications.oral_diabetes_meds", "bool", "hospital", ["DM-Q2", "DM-4"], ["DOC-7"], False, "On oral diabetes medication"),
    F("medications.anticoagulant", "bool", "hospital", ["CARD-4"], ["DOC-7"], False, "On a blood thinner"),
    F("medications.iv_medications_ongoing_at_discharge", "bool", "hospital", ["OTH-3", "ID-3"], ["DOC-7"], False, "Will still need IV medication after discharge"),
    F("medications.iv_antihypertensive_last_24h", "bool", "hospital", ["CARD-6", "CIR-X2"], ["DOC-7", "DOC-5"], False, "IV blood pressure medication in the last 24 hours"),
    F("medications.self_administers_all_meds", "bool", "hospital", ["FORM-M5", "COG-1"], ["DOC-1"], False, "Referral form: self-administers all medications"),
    F("medications.thirty_day_supply_arranged", "bool", "hospital", [], ["DOC-10"], True, "30-day supply will come with the patient"),

    # ---- lines, devices, oxygen ----
    F("lines_devices_oxygen.iv_access", "enum:none|peripheral|midline|picc|port", "hospital", ["OTH-3", "ID-3", "INT-E3"], ["DOC-5", "DOC-8"], False, "IV access the patient will leave with"),
    F("lines_devices_oxygen.oxygen_required", "bool", "hospital", ["RESP-3", "RSP-E3"], ["DOC-1", "DOC-5"], True, "Needs supplemental oxygen"),
    F("lines_devices_oxygen.oxygen_flow_lpm", "number", "hospital", ["RESP-3"], ["DOC-5"], False, "Liters per minute"),
    F("lines_devices_oxygen.oxygen_continuous", "bool", "hospital", ["RSP-E3"], ["DOC-5"], False, "Continuous (not eligible for COPD on continuous O2) vs as needed"),
    F("lines_devices_oxygen.baseline_spo2_room_air", "number", "hospital", ["RSP-E5", "RESP-2"], ["DOC-5", "DOC-6"], False, "Usual oxygen saturation on room air"),
    F("lines_devices_oxygen.cpap", "bool", "hospital", ["RSP-E7"], ["DOC-7"], False, "Uses CPAP (eligible)"),
    F("lines_devices_oxygen.bipap", "bool", "hospital", ["RSP-X5"], ["DOC-5"], False, "Requires BiPAP (not eligible)"),
    F("lines_devices_oxygen.tracheostomy", "enum:none|mature_self_care|ventilator|other", "hospital", ["RSP-E6", "RSP-X3"], ["DOC-4", "DOC-5"], False, "Trach status"),
    F("lines_devices_oxygen.dialysis", "bool", "hospital", ["GU-E7"], ["DOC-4"], False, "On dialysis"),
    F("lines_devices_oxygen.external_fixation_device", "bool", "hospital", ["MSK-X3"], ["DOC-4", "DOC-8"], False, "External fixator in place (not eligible)"),
    F("lines_devices_oxygen.breathing_treatments", "list", "hospital", ["RESP-Q1"], ["DOC-7"], False, "Nebulizers, inhalers, as written"),
    F("lines_devices_oxygen.respiratory_therapy_notes_present", "bool", "hospital", ["RESP-4"], ["DOC-5"], False, "RT notes in the packet"),

    # ---- wounds ----
    F("wounds.any_wounds", "bool", "hospital", ["INT-E1"], ["DOC-1", "DOC-5"], True, "Any wound documented"),
    F("wounds.items", "rows:WoundRow", "hospital", ["INT-E1", "INT-E2", "INT-E3", "INT-E4", "INT-E5", "INT-E6", "INT-E7", "INT-X1", "INT-X2", "INT-X3"], ["DOC-1", "DOC-5", "DOC-8"], False, "One row per wound"),
    F("wounds.patient_independent_with_wound_care", "bool", "hospital", ["INT-E2"], ["DOC-1", "DOC-8"], False, "Referral form: independent with wound care"),

    # ---- vitals ----
    F("vitals.sets", "rows:VitalsRow", "hospital", ["ID-1", "RESP-2", "CARD-1"], ["DOC-6"], False, "Most recent sets of vital signs, newest first"),
    F("vitals.set_count", "int", "hospital", ["ID-1", "RESP-2", "CARD-1"], ["DOC-6"], False, "How many sets are in the packet (rules ask for 5)"),
    F("vitals.sepsis_signs", "bool", "hospital", ["ID-1"], ["DOC-6"], False, "Continued fever, fast heart rate or low blood pressure"),
    F("vitals.bp_stable_on_oral_meds_24h", "bool", "hospital", ["CARD-1", "CIR-E6"], ["DOC-6", "DOC-7"], False, "Blood pressure stable on oral medication for at least 24 hours"),
    F("vitals.spo2_stable_at_baseline", "bool", "hospital", ["RESP-2", "RSP-E5"], ["DOC-6"], False, "Oxygen readings stable and at the patient's normal"),

    # ---- labs ----
    F("labs.glucose_readings", "rows:LabRow", "hospital", ["DM-1", "END-E1"], ["DOC-5", "DOC-6"], False, "Finger-stick glucose readings, newest first"),
    F("labs.glucose_reading_count", "int", "hospital", ["DM-1"], ["DOC-5"], False, "How many consecutive readings are in the packet (rule asks for 5)"),
    F("labs.glucose_trend", "enum:improving|stable|worsening|insufficient_data", "hospital", ["DM-1", "END-E1"], ["DOC-5"], False, "Improvement and stabilization"),
    F("labs.hypoglycemia_last_week", "bool", "hospital", ["DM-4"], ["DOC-5"], False, "Any hypoglycemia episode in the last week"),
    F("labs.a1c", "number", "hospital", ["DM-Q3"], ["DOC-5"], False, "Last hemoglobin A1C (informational, not limiting)"),
    F("labs.dka_status", "enum:current|resolved|none", "hospital", ["DM-2", "END-X3"], ["DOC-4", "DOC-5"], False, "Diabetic ketoacidosis. Rules do not say whether resolved DKA still excludes: show the gap."),
    F("labs.troponin_results", "rows:LabRow", "hospital", ["CARD-2"], ["DOC-5"], False, "Troponin series"),
    F("labs.troponin_count", "int", "hospital", ["CARD-2"], ["DOC-5"], False, "Number of troponin results (rule asks for 3, with 2 negative)"),
    F("labs.troponin_negative_count", "int", "hospital", ["CARD-2"], ["DOC-5"], False, "Number of negative troponins"),
    F("labs.cultures", "rows:CultureRow", "hospital", ["ID-Q2"], ["DOC-9"], False, "Blood, wound, urine cultures and results"),
    F("labs.wbc_latest", "number", "hospital", ["ID-1"], ["DOC-5"], False, "Latest white blood cell count"),
    F("labs.inr_latest", "number", "hospital", ["CARD-4"], ["DOC-5"], False, "Latest INR if on warfarin"),
    F("labs.hemoglobin_hematocrit_normal", "bool", "hospital", ["CIR-E5"], ["DOC-5"], False, "Post cardiac surgery: normal hemoglobin and hematocrit"),

    # ---- imaging / cardiac ----
    F("imaging_cardiac.chest_imaging_present", "bool", "hospital", ["RESP-1"], ["DOC-5"], False, "Chest X-ray, CT or MRI in the packet"),
    F("imaging_cardiac.chest_imaging_impression", "text", "hospital", ["RESP-1", "RSP-X1", "RSP-X2"], ["DOC-5"], False, "Radiology impression, as written"),
    F("imaging_cardiac.ekg_present", "bool", "hospital", ["CARD-3"], ["DOC-5"], False, "EKG in the packet"),
    F("imaging_cardiac.ekg_impression", "text", "hospital", ["CARD-3", "CIR-X1"], ["DOC-5"], False, "EKG reading, as written"),
    F("imaging_cardiac.ejection_fraction_percent", "number", "hospital", ["CARD-5", "CIR-E7", "CIR-E5"], ["DOC-5"], False, "EF from echo or cath report"),
    F("imaging_cardiac.ef_is_patient_baseline", "bool", "hospital", ["CARD-5"], ["DOC-5"], False, "Low EF established as the patient's baseline"),
    F("imaging_cardiac.unstable_arrhythmia", "bool", "hospital", ["CIR-X1"], ["DOC-5"], False, "Unstable arrhythmia documented"),
    F("imaging_cardiac.atrial_fibrillation", "bool", "hospital", ["CARD-4"], ["DOC-4", "DOC-5"], False, "History or new diagnosis of atrial fibrillation"),

    # ---- conditions ----
    F("conditions.primary_diagnosis", "text", "hospital", [], ["DOC-4"], True, "Primary diagnosis for this stay"),
    F("conditions.problem_list", "rows:ProblemRow", "hospital", [], ["DOC-4", "DOC-5"], False, "Active problems, each tagged to a body system and the rule IDs it touches"),
    F("conditions.flags.stroke_with_residual_deficit", "bool", "hospital", ["NEU-X1"], ["DOC-4", "DOC-8"], False, "Stroke with ongoing deficit (not eligible)"),
    F("conditions.flags.new_onset_seizure_with_deficit", "bool", "hospital", ["NEU-X2"], ["DOC-4"], False, "New seizure with deficit (not eligible)"),
    F("conditions.flags.tbi_severity", "enum:none|mild|moderate|severe", "hospital", ["NEU-E3", "NEU-X3"], ["DOC-4"], False, "Traumatic brain injury severity"),
    F("conditions.flags.spinal_fusion", "bool", "hospital", ["NEU-X5"], ["DOC-4"], False, "Cervical or lumbar fusion (not eligible)"),
    F("conditions.flags.immediately_post_neurosurgery", "bool", "hospital", ["NEU-X6"], ["DOC-4"], False, "Just had neurosurgery (escalate for clinical review)"),
    F("conditions.flags.pulmonary_embolism", "bool", "hospital", ["RSP-X4"], ["DOC-4", "DOC-5"], False, "PE (not eligible)"),
    F("conditions.flags.pneumothorax_or_hemothorax", "bool", "hospital", ["RSP-X1", "RSP-X2"], ["DOC-4", "DOC-5"], False, "Not eligible"),
    F("conditions.flags.open_fracture", "bool", "hospital", ["MSK-X1", "MSK-E1"], ["DOC-4"], False, "Open fracture (not eligible); closed fractures are eligible"),
    F("conditions.flags.osteomyelitis", "bool", "hospital", ["MSK-X2"], ["DOC-4", "DOC-5"], False, "Bone infection (not eligible)"),
    F("conditions.flags.recent_amputation_with_wounds", "bool", "hospital", ["MSK-X4"], ["DOC-4"], False, "Not eligible immediately after"),
    F("conditions.flags.jaw_wired", "bool", "hospital", ["MSK-X5"], ["DOC-4"], False, "Facial fracture with jaw wiring (not eligible)"),
    F("conditions.flags.decompensated_cirrhosis", "bool", "hospital", ["GI-X1"], ["DOC-4", "DOC-5"], False, "Ascites, varices, encephalopathy or jaundice (not eligible)"),
    F("conditions.flags.recent_gi_bleed_or_varices", "bool", "hospital", ["GI-X2"], ["DOC-4"], False, "Not eligible immediately after"),
    F("conditions.flags.immediately_post_parathyroidectomy", "bool", "hospital", ["END-X1"], ["DOC-4"], False, "Not eligible"),
    F("conditions.flags.alcoholic_ketoacidosis", "bool", "hospital", ["END-X2"], ["DOC-4"], False, "Not eligible"),
    F("conditions.flags.recent_heart_attack", "bool", "hospital", ["CIR-E7", "CARD-5"], ["DOC-4"], False, "Being discharged after a heart attack (needs normal EF)"),
    F("conditions.flags.on_chemotherapy", "bool", "hospital", ["CARD-5"], ["DOC-4", "DOC-7"], False, "On chemotherapy"),
    F("conditions.flags.post_cabg_or_valve_surgery", "bool", "hospital", ["CIR-E5"], ["DOC-4"], False, "Post bypass or valve surgery"),
    F("conditions.flags.hypertension_uncontrolled_or_symptomatic", "bool", "hospital", ["CIR-X2", "CIR-E4", "CIR-E6"], ["DOC-5", "DOC-6"], False, "Hypertension needing IV meds, symptomatic, or not responding to oral meds"),
    F("conditions.flags.diabetes", "bool", "hospital", ["DM-1", "DM-2", "DM-3", "DM-4", "DM-5", "DM-6", "END-E1"], ["DOC-1", "DOC-4"], False, "Has diabetes"),
    F("conditions.flags.active_infection", "bool", "hospital", ["ID-1", "ID-2", "ID-3", "FORM-X4"], ["DOC-1", "DOC-4"], False, "Active infection being treated"),
    F("conditions.flags.respiratory_illness", "bool", "hospital", ["RESP-1", "RESP-2", "RESP-3", "RESP-4"], ["DOC-4"], False, "Pneumonia, COPD, asthma, bronchitis or similar"),
    F("conditions.flags.cardiac_condition", "bool", "hospital", ["CARD-1", "CARD-2", "CARD-3"], ["DOC-4"], False, "A cardiac reason for the stay"),

    # ---- disease specific ----
    F("disease_specific.diabetes.pcp_followup_within_7_days", "bool", "hospital", ["DM-4"], ["DOC-5"], False, "Primary care follow-up scheduled within 5 to 7 days (ED referrals)"),
    F("disease_specific.diabetes.written_glucose_treatment_plan", "bool", "hospital", ["DM-4"], ["DOC-5", "DOC-7"], False, "Sliding scale or hold parameters written down"),
    F("disease_specific.infection.location", "text", "hospital", ["ID-Q1"], ["DOC-4"], False, "Where the infection is"),
    F("disease_specific.infection.on_oral_antibiotics", "bool", "hospital", ["ID-2", "INT-E1"], ["DOC-7"], False, "Oral antibiotics at discharge"),
    F("disease_specific.infection.on_iv_antibiotics", "bool", "hospital", ["ID-3", "INT-E3"], ["DOC-7"], False, "IV antibiotics at discharge (needs home health)"),
    F("disease_specific.infection.can_self_administer_oral_antibiotics", "bool", "hospital", ["ID-2"], ["DOC-8"], False, "Can take oral antibiotics alone"),
    F("disease_specific.respiratory.on_antibiotics_for_respiratory", "bool", "hospital", ["RESP-Q2"], ["DOC-7"], False, "Antibiotics for the respiratory condition"),
    F("disease_specific.cardiac.anticoag_independent_and_understands_inr", "bool", "hospital", ["CARD-4"], ["DOC-8"], False, "Takes anticoagulant alone and understands INR checks"),

    # ---- arrangements (conditional rules) ----
    F("arrangements.home_health_status", "enum:none|ordered|established|coordinated_and_scheduled", "hospital", ["OTH-3", "ID-3", "INT-E2", "INT-E3", "INT-E6", "INT-E7", "CIR-E5"], ["DOC-1", "DOC-5"], True, "Home health. The rules use four different thresholds; record the strongest one documented."),
    F("arrangements.home_health_purpose", "list", "hospital", ["OTH-3", "ID-3", "INT-E2"], ["DOC-5"], True, "What home health is for: IV, wound care, therapy"),
    F("arrangements.wound_care_scheduled", "bool", "hospital", ["INT-E2", "INT-E6", "INT-E7"], ["DOC-5"], True, "Wound care visits scheduled, matching dressing frequency"),
    F("arrangements.pt_scheduled", "bool", "hospital", ["MSK-E2"], ["DOC-5", "DOC-8"], True, "Outpatient or home PT scheduled"),
    F("arrangements.hospice_arranged", "bool", "hospital", ["EOL-1"], ["DOC-5"], True, "Hospice agency arranged and everyone told staff cannot give medications"),
    F("arrangements.pcp_followup_scheduled", "bool", "hospital", ["DM-4", "CIR-E4"], ["DOC-5"], True, "Primary care follow-up booked"),
    F("arrangements.specialty_followups", "list", "hospital", ["INT-E5", "GU-E6", "CIR-E5"], ["DOC-5"], True, "Booked follow-ups: suture removal, catheter removal, cardiac surgery clinic"),
    F("arrangements.oxygen_ordered_tank_ready_day_before", "bool", "hospital", ["RESP-3"], ["DOC-5"], True, "Hospital ordered O2 and the tank will be at the site the day before"),
    F("arrangements.dialysis_schedule_confirmed", "bool", "hospital", ["GU-E7"], ["DOC-5"], True, "Dialysis days and chair confirmed"),

    # ---- forms: the referral form and the authorization form AS WRITTEN, so the SST is the electronic form ----
    # Patient identifiers (name, MRN, DOB, SSN) are never stored; only whether the form has them filled in.
    F("forms.ihh_referral_form.present", "bool", "hospital", [], ["DOC-1"], True, "IHH referral form is in the packet"),
    F("forms.ihh_referral_form.form_code", "text", "hospital", [], ["DOC-1"], False, "Form code printed on it, for example RC0725-SH"),
    F("forms.ihh_referral_form.referring_department", "text", "hospital", [], ["DOC-1"], False, "Department or floor, as written"),
    F("forms.ihh_referral_form.referrer_role", "enum:social_worker|case_manager|nurse|physician|other", "hospital", [], ["DOC-1"], False, "Who filled it in"),
    F("forms.ihh_referral_form.referrer_contact_filled", "bool", "hospital", [], ["DOC-1"], False, "Phone or email filled in (the values are never stored)"),
    F("forms.ihh_referral_form.authorized_by_filled", "bool", "hospital", [], ["DOC-1"], False, "Authorized-by line filled in"),
    F("forms.ihh_referral_form.patient_identifiers_complete", "bool", "hospital", [], ["DOC-1"], False, "Name, MRN, DOB and insurance all filled in (values never stored)"),
    F("forms.ihh_referral_form.gender", "text", "hospital", [], ["DOC-1"], False, "As written on the form"),
    F("forms.ihh_referral_form.ethnicity", "text", "hospital", [], ["DOC-1"], False, "As written on the form"),
    F("forms.ihh_referral_form.insurance", "text", "hospital", [], ["DOC-1"], False, "As written on the form"),
    F("forms.ihh_referral_form.preferred_language", "text", "hospital", [], ["DOC-1"], False, "As written on the form"),
    F("forms.ihh_referral_form.currently_unhoused", "bool", "hospital", ["FORM-M1"], ["DOC-1"], False, "Form: currently unhoused"),
    F("forms.ihh_referral_form.four_plus_episodes_3y", "bool", "hospital", [], ["DOC-1"], False, "Form: 4+ episodes of homelessness in 3 years"),
    F("forms.ihh_referral_form.chief_complaint", "text", "hospital", [], ["DOC-1"], False, "Chief complaint or admitting diagnosis, as written on the form"),
    F("forms.ihh_referral_form.admit_date_filled", "bool", "hospital", [], ["DOC-1"], False, "Admit date filled in (redacted in event packets)"),
    F("forms.ihh_referral_form.wounds_reported", "bool", "hospital", [], ["DOC-1"], False, "Form: any wounds"),
    F("forms.ihh_referral_form.wounds_description", "text", "hospital", [], ["DOC-1"], False, "Number, location, size and stage, as written"),
    F("forms.ihh_referral_form.independent_with_wound_care", "bool", "hospital", ["INT-E2"], ["DOC-1"], False, "Form: independent with wound care"),
    F("forms.ihh_referral_form.home_health_will_be_ordered", "bool", "hospital", ["OTH-3", "ID-3"], ["DOC-1"], False, "Form: home health will be ordered"),
    F("forms.ihh_referral_form.tb_test_type", "enum:ppd|chest_xray|none", "hospital", ["FORM-X4"], ["DOC-1"], False, "Form: PPD/TB test or chest X-ray"),
    F("forms.ihh_referral_form.tb_result", "text", "hospital", ["FORM-X4"], ["DOC-1"], False, "Form: TB result as written (date never stored)"),
    F("forms.ihh_referral_form.o2", "bool", "hospital", ["RESP-3"], ["DOC-1"], False, "Form yes/no: oxygen"),
    F("forms.ihh_referral_form.self_administers_all_meds", "bool", "hospital", ["FORM-M5"], ["DOC-1"], False, "Form yes/no: self-administers all medications"),
    F("forms.ihh_referral_form.continent", "bool", "hospital", [], ["DOC-1"], False, "Form yes/no: continent of bowel and bladder"),
    F("forms.ihh_referral_form.ostomy", "bool", "hospital", [], ["DOC-1"], False, "Form yes/no: colostomy or ileostomy"),
    F("forms.ihh_referral_form.foley", "bool", "hospital", ["GU-E6"], ["DOC-1"], False, "Form yes/no: Foley catheter"),
    F("forms.ihh_referral_form.diabetes", "bool", "hospital", ["DM-Q2"], ["DOC-1"], False, "Form yes/no: diabetes"),
    F("forms.ihh_referral_form.insulin", "bool", "hospital", ["DM-Q1"], ["DOC-1"], False, "Form yes/no: insulin"),
    F("forms.ihh_referral_form.diabetes_oral_meds", "bool", "hospital", ["DM-Q2"], ["DOC-1"], False, "Form yes/no: oral diabetes medication"),
    F("forms.ihh_referral_form.active_infection", "bool", "hospital", ["FORM-X4"], ["DOC-1"], False, "Form yes/no: active infection"),
    F("forms.ihh_referral_form.ambulatory", "bool", "hospital", ["FORM-M6"], ["DOC-1"], False, "Form yes/no: ambulatory"),
    F("forms.ihh_referral_form.assistive_device", "bool", "hospital", ["FORM-M6"], ["DOC-1"], False, "Form yes/no: assistive device"),
    F("forms.ihh_referral_form.hospice", "bool", "hospital", ["EOL-1"], ["DOC-1"], False, "Form yes/no: hospice"),
    F("forms.ihh_referral_form.car", "bool", "hospital", [], ["DOC-1"], False, "Form yes/no: car"),
    F("forms.ihh_referral_form.service_animal", "bool", "hospital", [], ["DOC-1"], False, "Form yes/no: service animal"),
    F("forms.ihh_referral_form.limitations_or_challenges", "text", "hospital", [], ["DOC-1"], False, "Behavioral section: limitations or challenges, as written"),
    F("forms.ihh_referral_form.mental_health_dx", "bool", "hospital", ["MH-1"], ["DOC-1"], False, "Form: mental health diagnosis"),
    F("forms.ihh_referral_form.mental_health_dx_type", "text", "hospital", ["MH-1"], ["DOC-1"], False, "Form: type, as written"),
    F("forms.ihh_referral_form.non_compliant", "bool", "hospital", ["FORM-X5"], ["DOC-1"], False, "Form: non-compliant"),
    F("forms.ihh_referral_form.forgetful", "bool", "hospital", ["COG-1"], ["DOC-1"], False, "Form: forgetful"),
    F("forms.ihh_referral_form.cognitive_impairment", "bool", "hospital", ["FORM-X1"], ["DOC-1"], False, "Form: cognitive impairment"),
    F("forms.ihh_referral_form.registered_sex_offender", "bool", "hospital", ["FORM-X6", "OTH-2"], ["DOC-1"], False, "Form: registered sex offender"),
    F("forms.ihh_referral_form.behavioral_other", "text", "hospital", [], ["DOC-1"], False, "Form: other, as written"),
    F("forms.ihh_referral_form.length_of_stay_authorized_days", "int", "hospital", [], ["DOC-1"], False, "Form: length of stay authorized, in days"),
    F("forms.ihh_referral_form.staff_section_completed", "bool", "provider", [], ["DOC-1"], False, "IHH staff-only section filled in (blank in all event packets)"),
    F("forms.calaim_authorization.present", "bool", "hospital", [], ["DOC-3"], True, "CalAIM / CalOptima authorization form, LOA or auth is in the packet"),
    F("forms.calaim_authorization.health_plan", "text", "hospital", [], ["DOC-3"], True, "Plan named on the form"),
    F("forms.calaim_authorization.community_support_requested", "text", "hospital", [], ["DOC-3"], False, "Which Community Support is requested, as written (recuperative care expected)"),
    F("forms.calaim_authorization.requested_days", "int", "hospital", [], ["DOC-3"], True, "Days requested"),
    F("forms.calaim_authorization.requested_start_date_filled", "bool", "hospital", [], ["DOC-3"], False, "Start date filled in (redacted in event packets)"),
    F("forms.calaim_authorization.icd10_codes", "list", "hospital", [], ["DOC-3"], False, "Diagnosis codes listed"),
    F("forms.calaim_authorization.requesting_provider_type", "text", "hospital", [], ["DOC-3"], False, "Hospital, clinic, other, as written"),
    F("forms.calaim_authorization.status", "enum:not_requested|submitted|approved|denied", "hospital", [], ["DOC-3"], True, "Authorization status as far as the packet shows"),
    F("forms.calaim_authorization.authorization_number_filled", "bool", "hospital", [], ["DOC-3"], False, "Authorization number present (value never stored)"),
    F("forms.calaim_authorization.member_id_filled", "bool", "hospital", [], ["DOC-3"], False, "Member ID present (value never stored)"),
]

# ---------- schema building ----------

def value_schema(kind):
    if kind == "bool":
        return {"type": ["boolean", "null"]}
    if kind == "text":
        return {"type": ["string", "null"]}
    if kind == "number":
        return {"type": ["number", "null"]}
    if kind == "int":
        return {"type": ["integer", "null"]}
    if kind == "list":
        return {"type": "array", "items": {"type": "string"}}
    if kind.startswith("enum:"):
        return {"type": ["string", "null"], "enum": kind[5:].split("|") + [None]}
    if kind.startswith("rows:"):
        return {"type": "array", "items": {"$ref": "#/$defs/" + kind[5:]}}
    raise ValueError(kind)


def blank_value(kind):
    return [] if (kind == "list" or kind.startswith("rows:")) else None


FIELD_DEFS = {}


def _wrapper(kind):
    """One shared $def per value type so the schema stays small."""
    key = "Field_" + re.sub(r"[^A-Za-z0-9]+", "_", kind).strip("_")
    if key not in FIELD_DEFS:
        FIELD_DEFS[key] = {
            "type": "object",
            "properties": {
                "value": value_schema(kind),
                "status": {"type": "string", "enum": STATUS},
                "evidence": {"type": "array", "items": {"$ref": "#/$defs/Evidence"}},
                "conflicts": {"type": "array", "items": {"$ref": "#/$defs/Conflict"}},
                "confidence": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
                "updated_by": {"type": ["string", "null"], "enum": ACTORS + [None]},
                "updated_at": {"type": ["string", "null"], "description": "ISO 8601"},
                "note": {"type": ["string", "null"]},
            },
            "required": ["value", "status", "evidence", "conflicts", "confidence", "updated_by", "updated_at", "note"],
            "additionalProperties": False,
        }
    return key


def field_schema(f):
    return {
        "$ref": "#/$defs/" + _wrapper(f["kind"]),
        "description": f["desc"],
        "x-owner": f["owner"],
        "x-rules": f["rules"],
        "x-docs": f["docs"],
        "x-patient_visible": f["pv"],
    }


def blank_field(f):
    return {"value": blank_value(f["kind"]), "status": "pending", "evidence": [], "conflicts": [],
            "confidence": None, "updated_by": None, "updated_at": None, "note": None}


def obj(props, desc=None, **extra):
    o = {"type": "object", "properties": props, "required": list(props.keys()), "additionalProperties": False}
    if desc:
        o["description"] = desc
    o.update(extra)
    return o


def S(t, desc=None, nullable=True, **kw):
    d = {"type": [t, "null"] if nullable else t}
    if desc:
        d["description"] = desc
    d.update(kw)
    return d


EVIDENCE = obj({
    "source": {"type": "string", "enum": ["packet", "transcript", "entered", "external"], "description": "packet = a page of the referral packet; transcript = a recorded coordination conversation; entered = typed by a party; external = another uploaded document"},
    "page": S("integer", "1-based page number in the packet PDF. Required when source is packet; null otherwise"),
    "doc_type": S("string", "What kind of page: referral_form, face_sheet, h_and_p, progress_note, pt_ot_eval, nursing_note, mar, labs, imaging, ekg, auth_form, fax_cover, social_work_note, consult, other"),
    "ref": S("string", "For transcript or external sources: the conversation id or document id"),
    "timestamp": S("string", "For transcript sources: position in the conversation, for example 00:04:12"),
    "quote": S("string", "Short verbatim text from the page or transcript that supports the value. Never paraphrase into a quote.", nullable=False),
}, "Where a value came from. Every 'found' value needs at least one.")

CONFLICT = obj({
    "page": S("integer", nullable=False),
    "doc_type": S("string"),
    "quote": S("string", nullable=False),
    "value": S("string", "The value this page supports, as text"),
}, "A second source that disagrees with the recorded value.")

WOUND_ROW = obj({
    "location": S("string"),
    "wound_type": {"type": ["string", "null"], "enum": ["pressure_injury", "surgical_incision", "abscess", "cellulitis", "laceration", "burn", "diabetic_ulcer", "other", None]},
    "stage": {"type": ["string", "null"], "enum": ["1", "2", "3", "4", "unstageable", "deep_tissue_injury", "not_applicable", None]},
    "size": S("string", "As written, for example 3 x 2 x 0.5 cm"),
    "infected": S("boolean"),
    "dressing_change_frequency": S("string"),
    "hard_to_reach_area": S("boolean", "Coccyx, sacrum, back, back of thighs, perineum: needs at least one wound-care visit (INT-E2)"),
    "staples_or_sutures": S("boolean"),
    "removal_followup_scheduled": S("boolean"),
    "burn_tbsa_percent": S("number"),
    "inhalation_injury": S("boolean"),
    "requires_skin_graft": S("boolean"),
    "complicated_large_abscess": S("boolean", "Undefined in the rules; flag for the coordinator"),
    "evidence": {"type": "array", "items": {"$ref": "#/$defs/Evidence"}},
}, "One wound.")

VITALS_ROW = obj({
    "label": S("string", "Time or sequence label as written; dates are redacted in the event packets"),
    "temp_f": S("number"), "heart_rate": S("integer"), "bp_systolic": S("integer"), "bp_diastolic": S("integer"),
    "resp_rate": S("integer"), "spo2": S("integer"), "on_oxygen": S("boolean"),
    "page": S("integer", nullable=False),
}, "One set of vital signs.")

LAB_ROW = obj({
    "label": S("string", "Time or sequence label as written"),
    "value": S("string", "Result as written, including units"),
    "flag": {"type": ["string", "null"], "enum": ["normal", "high", "low", "critical", "negative", "positive", None]},
    "page": S("integer", nullable=False),
}, "One lab result.")

CULTURE_ROW = obj({
    "specimen": {"type": ["string", "null"], "enum": ["blood", "wound", "urine", "sputum", "nasal", "stool", "other", None]},
    "result": S("string", "Organism or 'no growth', as written"),
    "final": S("boolean", "Final vs preliminary"),
    "page": S("integer", nullable=False),
}, "One culture.")

PROBLEM_ROW = obj({
    "text": S("string", nullable=False),
    "body_system": {"type": ["string", "null"], "enum": ["integumentary", "ophthalmology", "respiratory", "musculoskeletal", "gi", "endocrine", "gu", "circulatory", "neurology", "psychiatric", "infectious", "other", None]},
    "matched_rule_ids": {"type": "array", "items": {"type": "string"}, "description": "IDs from the provider's criteria this problem touches, for example INT-E1 or GI-X1"},
    "evidence": {"type": "array", "items": {"$ref": "#/$defs/Evidence"}},
}, "One active problem.")

PAGE_ROW = obj({
    "page": S("integer", nullable=False),
    "doc_type": S("string"),
    "orientation": {"type": ["string", "null"], "enum": ["upright", "rotated_90", "rotated_180", "rotated_270", "mixed", None]},
    "legibility": {"type": ["string", "null"], "enum": ["good", "partial", "poor", "blank", None]},
    "from_earlier_stay": S("boolean"),
    "duplicate_of_page": S("integer"),
}, "What is on one page of the packet. Built first so the extractor and the people can navigate.")

RULE_RESULT = obj({
    "rule_id": S("string", nullable=False),
    "status": {"type": "string", "enum": ["met", "not_met", "cant_tell", "not_applicable", "needs_confirmation", "pending"]},
    "basis": S("string", "One or two sentences on why, in plain language"),
    "sst_fields": {"type": "array", "items": {"type": "string"}, "description": "SST field paths used"},
    "evidence": {"type": "array", "items": {"$ref": "#/$defs/Evidence"}},
    "confirmed_by": S("string", "Role of the person who confirmed, if any"),
    "confirmed_at": S("string"),
}, "The result of checking one rule against the SST.")

ELIGIBILITY = obj({
    "provider_id": S("string", "Which provider's requirements were applied, for example ihh"),
    "profile_version": S("string"),
    "evaluated_at": S("string"),
    "rules": {"type": "array", "items": {"$ref": "#/$defs/RuleResult"}},
    "hard_stops": {"type": "array", "items": {"type": "string"}, "description": "Rule IDs that are not_met and exclude on their own"},
    "open_questions": {"type": "array", "items": obj({"rule_id": S("string"), "question": S("string", nullable=False)})},
    "recommendation": {"type": ["string", "null"], "enum": ["likely_eligible", "likely_ineligible", "request_information", "cant_tell", None]},
    "recommendation_rationale": S("string"),
    "coordinator_decision": obj({
        "decision": {"type": ["string", "null"], "enum": ["approve", "deny", "request_information", None]},
        "reason": S("string"),
        "decided_by": S("string"),
        "decided_at": S("string"),
    }, "The human decision. The tool recommends; the coordinator decides."),
    "scores": {"$ref": "#/$defs/Scores"},
}, "Computed by the provider's requirements profile. Never extracted from the packet directly.")

SCORES = obj({
    "computed_at": S("string"),
    "readiness": S("integer", "0 to 100. How ready this packet is to be decided by this provider: completeness, rule coverage, rules met, arrangements confirmed. Not a probability of approval. Hard stops are shown separately and are never averaged in."),
    "band": {"type": ["string", "null"], "enum": ["ready_to_decide", "nearly_ready", "needs_documents", "blocked_by_hard_stop", None]},
    "components": obj({
        "completeness": obj({"score": S("integer", "0 to 100"), "present": S("integer"), "required": S("integer"), "missing_doc_ids": {"type": "array", "items": {"type": "string"}}}, "Checklist documents present out of those this packet needs"),
        "rule_coverage": obj({"score": S("integer", "0 to 100"), "answerable": S("integer"), "applicable": S("integer"), "cant_tell_rule_ids": {"type": "array", "items": {"type": "string"}}}, "Applicable rules that can be answered from the record"),
        "rules_met": obj({"score": S("integer", "0 to 100"), "met": S("integer"), "not_met": S("integer"), "not_met_rule_ids": {"type": "array", "items": {"type": "string"}}}, "Of the answerable rules, how many are met"),
        "arrangements": obj({"score": S("integer", "0 to 100"), "confirmed": S("integer"), "needed": S("integer"), "pending_rule_ids": {"type": "array", "items": {"type": "string"}}}, "Conditional rules whose arrangement is confirmed"),
        "hard_stop_count": S("integer", "Number of exclude rules that are met. Any value above 0 sets the band to blocked_by_hard_stop"),
    }),
    "weights": obj({"completeness": S("number"), "rule_coverage": S("number"), "rules_met": S("number"), "arrangements": S("number")}, "Weights used, from the provider profile. Sum to 1"),
    "explanation": S("string", "Two or three plain sentences the coordinator can read out, naming the biggest reason the score is not 100"),
    "top_actions": {"type": "array", "items": {"type": "string"}, "description": "Up to three actions that would raise readiness the most, each tied to a gap id"},
}, "A readiness score, computed deterministically from the rule results. Explains itself. Never shown to the patient.")

CONVERSATION_ROW = obj({
    "id": S("string", nullable=False),
    "kind": {"type": "string", "enum": ["hospital_call", "patient_conversation", "provider_internal", "other"]},
    "participants": {"type": "array", "items": {"type": "string", "enum": ["provider", "hospital", "patient", "interpreter", "family", "other"]}},
    "started_at": S("string"),
    "duration_seconds": S("integer"),
    "consent_confirmed": S("boolean", "Every participant agreed to the recording or dictation before it started. Required true before any transcript is stored. California requires all-party consent for recorded calls.", nullable=False),
    "consent_note": S("string"),
    "transcript_source": {"type": ["string", "null"], "enum": ["browser_dictation", "uploaded_audio", "pasted_text", "typed_notes", None]},
    "language": S("string"),
    "transcript_text": S("string", "The transcript, with speaker labels by role. Identifier scrub runs before storage"),
    "identifier_scrub": {"type": ["string", "null"], "enum": ["passed", "flagged", "not_run", None], "description": "Result of the check for names, dates of birth, phone numbers and record numbers"},
    "summary": S("string", "Three to five plain sentences: what was asked, what was promised, by when"),
    "extracted_items": {"type": "array", "items": obj({
        "sst_path": S("string", nullable=False),
        "proposed_value": S("string", "As text; the field's own type applies once accepted"),
        "quote": S("string", nullable=False),
        "timestamp": S("string"),
        "status": {"type": "string", "enum": ["proposed", "accepted", "rejected"]},
        "decided_by": S("string"),
    }, "A fact said in the conversation that could update the SST. Nothing changes until a person accepts it."), "description": "Proposed SST updates from this conversation"},
    "follow_ups": {"type": "array", "items": obj({"what": S("string", nullable=False), "owner_role": S("string"), "due": S("string"), "gap_id": S("string")})},
    "status": {"type": "string", "enum": ["draft", "reviewed", "archived"]},
}, "One coordination conversation: a call with the hospital case manager, or a conversation with the patient. Transcribed, summarized, and mined for proposed SST updates that a person accepts or rejects.")

ANALYSIS_ROW = obj({
    "id": S("string", nullable=False),
    "ts": S("string", nullable=False),
    "actor_role": {"type": "string", "enum": ACTORS},
    "mode": {"type": "string", "enum": ["briefing", "question", "compare_sources", "what_would_change"]},
    "question": S("string", "Empty for a briefing"),
    "answer": S("string", nullable=False),
    "cited_sst_paths": {"type": "array", "items": {"type": "string"}},
    "cited_pages": {"type": "array", "items": {"type": "integer"}},
    "unknowns": {"type": "array", "items": {"type": "string"}, "description": "Things the question needed that the record does not contain"},
    "model": S("string"),
}, "One read-only analysis. Analyses never change the SST; they read it and cite it.")

GAP = obj({
    "id": S("string", nullable=False),
    "what": S("string", "What is missing or unclear, in plain language", nullable=False),
    "why_rule_ids": {"type": "array", "items": {"type": "string"}},
    "doc_ids": {"type": "array", "items": {"type": "string"}},
    "sst_fields": {"type": "array", "items": {"type": "string"}},
    "who_can_supply": {"type": ["string", "null"], "enum": ["hospital_case_manager", "hospital_nurse", "physician", "pt_ot", "pharmacy", "patient", "provider", None]},
    "status": {"type": "string", "enum": ["open", "requested", "received", "resolved", "not_available"]},
    "requested_at": S("string"), "received_at": S("string"),
    "draft_request_text": S("string", "Draft wording for the hospital. Drafted only; nothing is sent by the tool."),
    "note": S("string"),
}, "One thing to ask the hospital (or the patient) for while the patient is still in the hospital.")

PATIENT_SUMMARY = obj({
    "language": S("string", "Language the summary is written in"),
    "requires_staff_review": S("boolean", "True until a staff member has read it", nullable=False),
    "reviewed_by": S("string"), "reviewed_at": S("string"),
    "where_things_stand": S("string", "Two or three plain sentences. No medical advice."),
    "what_is_still_needed": {"type": "array", "items": obj({"what": S("string", nullable=False), "who_is_getting_it": S("string"), "status": S("string")})},
    "your_options": {"type": "array", "items": obj({"provider_name": S("string", nullable=False), "what_it_is": S("string"), "status": S("string")})},
    "questions_to_ask_your_team": {"type": "array", "items": {"type": "string"}},
}, "What the patient sees. Status and logistics only; clinical interpretation stays with staff.")

HISTORY_ROW = obj({
    "ts": S("string", nullable=False),
    "actor_role": {"type": "string", "enum": ACTORS},
    "actor_label": S("string", "Display label, never a real name in the event build"),
    "action": {"type": "string", "enum": ["extracted", "edited", "confirmed", "requested", "received", "evaluated", "scored", "decided", "viewed", "analyzed", "transcribed", "accepted", "rejected", "commented"]},
    "path": S("string", "Dotted SST path affected"),
    "old_value": S("string"), "new_value": S("string"), "note": S("string"),
}, "One change. The SST is append-only in history.")

META = obj({
    "sst_version": S("string", nullable=False),
    "referral_id": S("string", nullable=False),
    "created_at": S("string"), "updated_at": S("string"),
    "fictional": S("boolean", "True for made-up test data", nullable=False),
    "packet": obj({
        "source_file": S("string", "Path on disk, never committed"),
        "page_count": S("integer"),
        "pages": {"type": "array", "items": {"$ref": "#/$defs/PageRow"}},
    }),
    "parties": obj({
        "hospital": obj({"label": S("string"), "contact_role": S("string")}),
        "patient": obj({"label": S("string", "Display label only, for example 'Patient'")}),
        "providers": {"type": "array", "items": obj({
            "provider_id": S("string", nullable=False), "name": S("string"),
            "status": {"type": "string", "enum": ["not_sent", "sent", "reviewing", "information_requested", "approved", "denied", "withdrawn"]},
            "updated_at": S("string"),
        })},
    }),
})


def BLANK_SCORES():
    comp = lambda **k: {"score": None, **k}
    return {"computed_at": None, "readiness": None, "band": None,
            "components": {"completeness": comp(present=None, required=None, missing_doc_ids=[]),
                           "rule_coverage": comp(answerable=None, applicable=None, cant_tell_rule_ids=[]),
                           "rules_met": comp(met=None, not_met=None, not_met_rule_ids=[]),
                           "arrangements": comp(confirmed=None, needed=None, pending_rule_ids=[]),
                           "hard_stop_count": None},
            "weights": {"completeness": None, "rule_coverage": None, "rules_met": None, "arrangements": None},
            "explanation": None, "top_actions": []}


def build_schema():
    root_props = {"meta": {"$ref": "#/$defs/Meta"}}
    blank = {"meta": {
        "sst_version": "0.1", "referral_id": "", "created_at": None, "updated_at": None, "fictional": False,
        "packet": {"source_file": None, "page_count": None, "pages": []},
        "parties": {"hospital": {"label": None, "contact_role": None}, "patient": {"label": "Patient"}, "providers": []},
    }}
    # nested field sections
    sections = {}
    for f in FIELDS:
        parts = f["path"].split(".")
        node = sections
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = f

    def to_schema(node, path):
        props, blk = {}, {}
        for k, v in node.items():
            if isinstance(v, dict) and "path" in v and "kind" in v:
                props[k] = field_schema(v)
                blk[k] = blank_field(v)
            else:
                sub_s, sub_b = to_schema(v, path + [k])
                props[k] = sub_s
                blk[k] = sub_b
        o = obj(props)
        if len(path) == 1:
            o["description"] = SECTIONS.get(path[0], "")
        return o, blk

    for sec, node in sections.items():
        s, b = to_schema(node, [sec])
        root_props[sec] = s
        blank[sec] = b

    root_props["eligibility"] = {"$ref": "#/$defs/Eligibility"}
    root_props["gaps"] = {"type": "array", "items": {"$ref": "#/$defs/Gap"}}
    root_props["patient_summary"] = {"$ref": "#/$defs/PatientSummary"}
    root_props["conversations"] = {"type": "array", "items": {"$ref": "#/$defs/ConversationRow"}}
    root_props["analyses"] = {"type": "array", "items": {"$ref": "#/$defs/AnalysisRow"}}
    root_props["history"] = {"type": "array", "items": {"$ref": "#/$defs/HistoryRow"}}
    blank["eligibility"] = {"provider_id": None, "profile_version": None, "evaluated_at": None, "rules": [], "hard_stops": [],
                            "open_questions": [], "recommendation": None, "recommendation_rationale": None,
                            "coordinator_decision": {"decision": None, "reason": None, "decided_by": None, "decided_at": None},
                            "scores": BLANK_SCORES()}
    blank["gaps"] = []
    blank["conversations"] = []
    blank["analyses"] = []
    blank["patient_summary"] = {"language": None, "requires_staff_review": True, "reviewed_by": None, "reviewed_at": None,
                                "where_things_stand": None, "what_is_still_needed": [], "your_options": [], "questions_to_ask_your_team": []}
    blank["history"] = []

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/notrestai/claudeimpactlab/sst/sst.schema.json",
        "title": "Single Source of Truth (SST) for a recuperative care referral",
        "description": ("One shared, structured record built from a hospital referral packet. Every clinical field is wrapped: "
                        "value + status + evidence + who last changed it. Unknown is unknown; nothing defaults to normal. "
                        "Contains no names, dates of birth, record numbers or addresses."),
        "x-status_meaning": STATUS_MEANING,
        "type": "object",
        "properties": root_props,
        "required": list(root_props.keys()),
        "additionalProperties": False,
        "$defs": {
            "Evidence": EVIDENCE, "Conflict": CONFLICT, "WoundRow": WOUND_ROW, "VitalsRow": VITALS_ROW, "LabRow": LAB_ROW,
            "CultureRow": CULTURE_ROW, "ProblemRow": PROBLEM_ROW, "PageRow": PAGE_ROW, "RuleResult": RULE_RESULT,
            "Eligibility": ELIGIBILITY, "Scores": SCORES, "Gap": GAP, "PatientSummary": PATIENT_SUMMARY, "HistoryRow": HISTORY_ROW, "Meta": META,
            "ConversationRow": CONVERSATION_ROW, "AnalysisRow": ANALYSIS_ROW,
            **FIELD_DEFS,
        },
    }
    return schema, blank


# ---------- provider profile (IHH) ----------

def build_profile():
    crit = json.loads((ROOT / "providers/ihh/admission-criteria.json").read_text())
    rules = {}
    for r in crit["general"]["any_of"]:
        rules[r["id"]] = {"text": r["text"], "kind": "any_of_general", "section": "General"}
    for r in crit["rules"]:
        rules[r["id"]] = {"text": r["text"], "kind": r["kind"], "section": r["section"]}
    for s in crit["by_system"]:
        for r in s.get("eligible", []):
            rules[r["id"]] = {"text": r["text"], "kind": "eligible_condition", "section": s["system"]}
        for r in s.get("ineligible", []):
            rules[r["id"]] = {"text": r["text"], "kind": "ineligible_condition", "section": s["system"]}
    for r in crit["referral_form_screen"]["must_meet_all"]:
        rules[r["id"]] = {"text": r["text"], "kind": "form_must", "section": "Referral form screen"}
    for r in crit["referral_form_screen"]["not_eligible_if"]:
        rules[r["id"]] = {"text": r["text"], "kind": "form_exclude", "section": "Referral form screen"}
    docs = {d["id"]: d["text"] for d in crit["referral_checklist"]["items"]}

    for rid in rules:
        rules[rid]["sst_fields"] = []
    for did in docs:
        pass
    doc_fields = {d: [] for d in docs}
    unknown_refs = set()
    for f in FIELDS:
        for rid in f["rules"]:
            if rid in rules:
                rules[rid]["sst_fields"].append(f["path"])
            else:
                unknown_refs.add(rid)
        for did in f["docs"]:
            if did in doc_fields:
                doc_fields[did].append(f["path"])
            else:
                unknown_refs.add(did)
    unmapped = [rid for rid, r in rules.items() if not r["sst_fields"]]
    profile = {
        "provider_id": "ihh",
        "name": "Illumination Health + Home, adult recuperative care",
        "profile_version": "2026-10-17",
        "source": "providers/ihh/admission-criteria.json (IHH's criteria as transcribed for the event; the documents remain IHH's)",
        "precedence": crit["precedence"],
        "how_to_use": ("For each rule, read the listed SST fields. A rule is 'met' or 'not_met' only when the fields' statuses are "
                       "found, entered or confirmed. If any needed field is not_in_packet, redacted, cant_tell or conflict, the rule is "
                       "'cant_tell' and becomes a gap. Rules of kind 'ask' are questions, not pass/fail. Rules of kind 'conditional' "
                       "need the arrangement confirmed before admission. 'exclude' rules that are met are hard stops."),
        "rules": rules,
        "documents": {d: {"text": docs[d], "sst_fields": doc_fields[d]} for d in docs},
        "unmapped_rule_ids": unmapped,
        "unknown_references_in_field_table": sorted(unknown_refs),
    }
    return profile


# ---------- fictional example ----------

def set_field(doc, path, value, status="found", evidence=None, by="extractor", conf="high", note=None):
    node = doc
    parts = path.split(".")
    for p in parts[:-1]:
        node = node[p]
    fld = node[parts[-1]]
    fld.update({"value": value, "status": status, "evidence": evidence or [], "updated_by": by,
                "updated_at": "2026-10-17T10:30:00-07:00", "confidence": conf, "note": note})


def build_example(blank):
    ex = copy.deepcopy(blank)
    ex["meta"].update({"referral_id": "EXAMPLE-FICTIONAL-001", "created_at": "2026-10-17T10:00:00-07:00",
                       "updated_at": "2026-10-17T10:45:00-07:00", "fictional": True})
    ex["meta"]["packet"] = {"source_file": None, "page_count": 6, "pages": [
        {"page": 1, "doc_type": "fax_cover", "orientation": "upright", "legibility": "good", "from_earlier_stay": False, "duplicate_of_page": None},
        {"page": 2, "doc_type": "referral_form", "orientation": "upright", "legibility": "good", "from_earlier_stay": False, "duplicate_of_page": None},
        {"page": 3, "doc_type": "h_and_p", "orientation": "upright", "legibility": "partial", "from_earlier_stay": False, "duplicate_of_page": None},
        {"page": 4, "doc_type": "pt_ot_eval", "orientation": "rotated_90", "legibility": "good", "from_earlier_stay": False, "duplicate_of_page": None},
        {"page": 5, "doc_type": "labs", "orientation": "upright", "legibility": "good", "from_earlier_stay": False, "duplicate_of_page": None},
        {"page": 6, "doc_type": "mar", "orientation": "upright", "legibility": "poor", "from_earlier_stay": False, "duplicate_of_page": None},
    ]}
    ex["meta"]["parties"] = {"hospital": {"label": "Referring hospital (fictional)", "contact_role": "case_manager"},
                             "patient": {"label": "Patient"},
                             "providers": [{"provider_id": "ihh", "name": "Illumination Health + Home", "status": "reviewing", "updated_at": "2026-10-17T10:45:00-07:00"}]}
    E = lambda p, d, q: [{"source": "packet", "page": p, "doc_type": d, "ref": None, "timestamp": None, "quote": q}]
    T = lambda cid, ts, q: [{"source": "transcript", "page": None, "doc_type": None, "ref": cid, "timestamp": ts, "quote": q}]
    set_field(ex, "documents.doc_1_referral_form", True, evidence=E(2, "referral_form", "Recuperative Care Referral Form"))
    set_field(ex, "documents.doc_4_history_and_physical", True, evidence=E(3, "h_and_p", "HISTORY AND PHYSICAL"))
    set_field(ex, "documents.doc_8_pt_ot_or_nursing_notes", True, evidence=E(4, "pt_ot_eval", "Physical Therapy Evaluation"))
    set_field(ex, "documents.doc_7_medication_list", True, evidence=E(6, "mar", "Medication Administration Record"))
    set_field(ex, "documents.doc_6_vitals_last_24h", False, status="not_in_packet")
    set_field(ex, "documents.doc_9_cultures", False, status="not_in_packet", note="Infection case; cultures not sent")
    set_field(ex, "referral.source_setting", "inpatient", evidence=E(3, "h_and_p", "admitted to medicine floor"))
    set_field(ex, "referral.chief_complaint_or_admitting_dx", "Left lower leg cellulitis", evidence=E(3, "h_and_p", "CC: left lower extremity cellulitis"))
    set_field(ex, "referral.admit_date", None, status="redacted", note="Blacked out for privacy")
    set_field(ex, "patient.age_years", 58, status="conflict", evidence=E(2, "referral_form", "Age: 58"))
    ex["patient"]["age_years"]["conflicts"] = [{"page": 3, "doc_type": "h_and_p", "quote": "57 y/o", "value": "57"}]
    set_field(ex, "housing.housing_situation", "unhoused", evidence=E(2, "referral_form", "Currently unhoused: Yes"))
    set_field(ex, "function.mobility.ambulation_distance_ft", 150, evidence=E(4, "pt_ot_eval", "ambulated 150 ft with FWW, mod I"))
    set_field(ex, "function.mobility.ambulation_assist_level", "modified_independent", evidence=E(4, "pt_ot_eval", "mod I"))
    set_field(ex, "function.mobility.dme_type", ["front wheeled walker"], evidence=E(4, "pt_ot_eval", "FWW"))
    set_field(ex, "function.mobility.transfers_independent", True, evidence=E(4, "pt_ot_eval", "transfers: independent"))
    set_field(ex, "cognition.orientation", "AOx4", evidence=E(3, "h_and_p", "A&O x4"))
    set_field(ex, "conditions.flags.active_infection", True, evidence=E(3, "h_and_p", "cellulitis"))
    set_field(ex, "conditions.flags.diabetes", True, evidence=E(3, "h_and_p", "PMH: DM2"))
    set_field(ex, "disease_specific.infection.location", "left lower leg", evidence=E(3, "h_and_p", "left lower extremity"))
    set_field(ex, "disease_specific.infection.on_oral_antibiotics", True, evidence=E(6, "mar", "cephalexin 500 mg PO"), conf="medium", note="MAR page is poorly legible")
    set_field(ex, "medications.insulin_regimen", False, evidence=E(6, "mar", "metformin 1000 mg PO BID"), conf="medium")
    set_field(ex, "labs.glucose_reading_count", 2, evidence=E(5, "labs", "POC glucose 162, 148"))
    set_field(ex, "labs.glucose_trend", "insufficient_data", status="cant_tell", note="Only 2 readings; rule asks for 5")
    set_field(ex, "infection_control.mrsa_swab_result", None, status="not_in_packet")
    set_field(ex, "infection_control.covid_test_result", None, status="not_in_packet")
    set_field(ex, "vitals.set_count", 0, status="not_in_packet")
    set_field(ex, "arrangements.pcp_followup_scheduled", True, status="entered", by="provider", evidence=T("CONV-1", "00:03:10", "the PCP follow-up is booked for next week"), note="Accepted from the hospital call transcript")
    set_field(ex, "patient.preferred_language", "Spanish", status="entered", by="patient")
    ex["eligibility"] = {
        "provider_id": "ihh", "profile_version": "2026-10-17", "evaluated_at": "2026-10-17T10:40:00-07:00",
        "rules": [
            {"rule_id": "MOB-3", "status": "met", "basis": "PT documented 150 ft at modified independence with a walker.", "sst_fields": ["function.mobility.ambulation_distance_ft", "function.mobility.ambulation_assist_level"], "evidence": E(4, "pt_ot_eval", "ambulated 150 ft with FWW, mod I"), "confirmed_by": None, "confirmed_at": None},
            {"rule_id": "COG-2", "status": "met", "basis": "Alert and oriented x4 in the H&P.", "sst_fields": ["cognition.orientation"], "evidence": E(3, "h_and_p", "A&O x4"), "confirmed_by": None, "confirmed_at": None},
            {"rule_id": "INF-1", "status": "cant_tell", "basis": "No MRSA swab result in the packet. Needed because the referral is from an inpatient stay.", "sst_fields": ["infection_control.mrsa_swab_result"], "evidence": [], "confirmed_by": None, "confirmed_at": None},
            {"rule_id": "ID-1", "status": "cant_tell", "basis": "No vital signs in the packet; rule asks for the last 5 sets.", "sst_fields": ["vitals.set_count", "vitals.sepsis_signs"], "evidence": [], "confirmed_by": None, "confirmed_at": None},
            {"rule_id": "DM-1", "status": "cant_tell", "basis": "Only 2 glucose readings; rule asks for 5 consecutive readings showing improvement.", "sst_fields": ["labs.glucose_reading_count", "labs.glucose_trend"], "evidence": E(5, "labs", "POC glucose 162, 148"), "confirmed_by": None, "confirmed_at": None},
        ],
        "hard_stops": [],
        "open_questions": [{"rule_id": "ID-Q2", "question": "Were cultures performed? Please send results."}],
        "recommendation": "request_information",
        "recommendation_rationale": "Function and cognition rules are met. Infection-control and diabetes documentation is missing, so the packet cannot be approved yet.",
        "coordinator_decision": {"decision": None, "reason": None, "decided_by": None, "decided_at": None},
        "scores": {
            "computed_at": "2026-10-17T10:40:00-07:00", "readiness": 46, "band": "needs_documents",
            "components": {
                "completeness": {"score": 50, "present": 4, "required": 8, "missing_doc_ids": ["DOC-2", "DOC-3", "DOC-6", "DOC-9"]},
                "rule_coverage": {"score": 40, "answerable": 2, "applicable": 5, "cant_tell_rule_ids": ["INF-1", "ID-1", "DM-1"]},
                "rules_met": {"score": 100, "met": 2, "not_met": 0, "not_met_rule_ids": []},
                "arrangements": {"score": 0, "confirmed": 0, "needed": 1, "pending_rule_ids": ["DM-4"]},
                "hard_stop_count": 0,
            },
            "weights": {"completeness": 0.3, "rule_coverage": 0.3, "rules_met": 0.3, "arrangements": 0.1},
            "explanation": "Everything the packet does answer is in the patient's favour, but three of five applicable rules cannot be checked yet. The MRSA swab, five sets of vitals and three more glucose readings would move this to nearly ready.",
            "top_actions": ["G1: get the MRSA swab result", "G2: get the last five sets of vital signs", "G3: get five consecutive glucose readings"],
        },
    }
    ex["conversations"] = [{
        "id": "CONV-1", "kind": "hospital_call", "participants": ["provider", "hospital"],
        "started_at": "2026-10-17T10:41:00-07:00", "duration_seconds": 240, "consent_confirmed": True,
        "consent_note": "Both parties agreed to dictation at the start of the call (fictional)",
        "transcript_source": "browser_dictation", "language": "en",
        "transcript_text": "PROVIDER: Calling about the cellulitis referral. We are missing the MRSA swab. HOSPITAL: It was done, I will fax the result within the hour. PROVIDER: And the last five sets of vitals? HOSPITAL: Yes, sending those too. Also the PCP follow-up is booked for next week.",
        "identifier_scrub": "passed",
        "summary": "Hospital confirmed the MRSA swab was done and will fax the result within the hour, together with the last five sets of vitals. Primary care follow-up is booked.",
        "extracted_items": [
            {"sst_path": "arrangements.pcp_followup_scheduled", "proposed_value": "true", "quote": "the PCP follow-up is booked for next week", "timestamp": "00:03:10", "status": "accepted", "decided_by": "Referral coordinator"},
            {"sst_path": "infection_control.mrsa_swab_result", "proposed_value": "pending", "quote": "It was done, I will fax the result within the hour", "timestamp": "00:00:45", "status": "proposed", "decided_by": None},
        ],
        "follow_ups": [{"what": "Receive MRSA result by fax", "owner_role": "hospital", "due": "within the hour", "gap_id": "G1"},
                       {"what": "Receive five sets of vitals", "owner_role": "hospital", "due": "within the hour", "gap_id": "G2"}],
        "status": "reviewed",
    }]
    ex["analyses"] = [{
        "id": "AN-1", "ts": "2026-10-17T10:43:00-07:00", "actor_role": "provider", "mode": "briefing", "question": None,
        "answer": "Fifty-eight-year-old with left lower leg cellulitis on oral antibiotics, walking 150 ft at modified independence with a walker and alert and oriented x4. Nothing in the record excludes admission. Three rules cannot be checked: no MRSA swab, no vital signs and only two glucose readings. The age differs between the referral form and the H&P.",
        "cited_sst_paths": ["referral.chief_complaint_or_admitting_dx", "function.mobility.ambulation_distance_ft", "cognition.orientation", "infection_control.mrsa_swab_result", "vitals.set_count", "labs.glucose_reading_count", "patient.age_years"],
        "cited_pages": [2, 3, 4, 5], "unknowns": ["MRSA swab result", "Last five sets of vital signs", "Three more glucose readings"],
        "model": "claude-opus-5-5",
    }]
    ex["gaps"] = [
        {"id": "G1", "what": "MRSA swab result", "why_rule_ids": ["INF-1"], "doc_ids": ["DOC-9"], "sst_fields": ["infection_control.mrsa_swab_result"], "who_can_supply": "hospital_nurse", "status": "requested", "requested_at": "2026-10-17T10:42:00-07:00", "received_at": None, "draft_request_text": "Please send the documented MRSA nasal swab result for this patient.", "note": None},
        {"id": "G2", "what": "Last 5 sets of vital signs", "why_rule_ids": ["ID-1"], "doc_ids": ["DOC-6"], "sst_fields": ["vitals.sets"], "who_can_supply": "hospital_nurse", "status": "open", "requested_at": None, "received_at": None, "draft_request_text": "Please send the last five sets of vital signs.", "note": None},
        {"id": "G3", "what": "Three more consecutive glucose readings", "why_rule_ids": ["DM-1"], "doc_ids": ["DOC-5"], "sst_fields": ["labs.glucose_readings"], "who_can_supply": "hospital_nurse", "status": "open", "requested_at": None, "received_at": None, "draft_request_text": "Please send five consecutive point-of-care glucose readings.", "note": None},
    ]
    ex["patient_summary"] = {
        "language": "es", "requires_staff_review": True, "reviewed_by": None, "reviewed_at": None,
        "where_things_stand": "Su hospital envió una solicitud a un programa de cuidado de recuperación. El programa está revisando. Faltan tres documentos del hospital.",
        "what_is_still_needed": [
            {"what": "Resultado de la prueba de MRSA", "who_is_getting_it": "Enfermería del hospital", "status": "solicitado"},
            {"what": "Signos vitales recientes", "who_is_getting_it": "Enfermería del hospital", "status": "pendiente"},
            {"what": "Lecturas de glucosa", "who_is_getting_it": "Enfermería del hospital", "status": "pendiente"},
        ],
        "your_options": [{"provider_name": "Illumination Health + Home", "what_it_is": "Cuidado de recuperación: cama, comidas y seguimiento de salud por un tiempo corto", "status": "en revisión"}],
        "questions_to_ask_your_team": ["¿Ya enviaron el resultado de la prueba de MRSA?", "¿Cuándo esperan una respuesta del programa?"],
    }
    ex["history"] = [
        {"ts": "2026-10-17T10:30:00-07:00", "actor_role": "extractor", "actor_label": "SST extractor", "action": "extracted", "path": "*", "old_value": None, "new_value": None, "note": "Initial extraction from 6-page fictional packet"},
        {"ts": "2026-10-17T10:40:00-07:00", "actor_role": "system", "actor_label": "rules", "action": "evaluated", "path": "eligibility", "old_value": None, "new_value": "request_information", "note": "IHH profile 2026-10-17"},
        {"ts": "2026-10-17T10:42:00-07:00", "actor_role": "provider", "actor_label": "Referral coordinator", "action": "requested", "path": "gaps.G1", "old_value": "open", "new_value": "requested", "note": "Draft sent to hospital case manager by phone (outside the tool)"},
        {"ts": "2026-10-17T10:45:00-07:00", "actor_role": "hospital", "actor_label": "Hospital case manager", "action": "edited", "path": "arrangements.pcp_followup_scheduled", "old_value": None, "new_value": "true", "note": None},
    ]
    # Let the real rules engine compute eligibility, gaps and the score when the backend is importable,
    # so the example never disagrees with the code.
    try:
        sys.path.insert(0, str(ROOT))
        from backend.pipeline import gaps as _gaps
        _gaps.run_all(ex, "ihh")
    except Exception as e:  # backend not built yet
        print("example: kept hand-written eligibility (", type(e).__name__, ")")
    return ex


# ---------- field reference markdown ----------

def build_reference(profile):
    lines = ["# SST field reference", "",
             "Generated by `sst/build.py` from the field table. Do not edit by hand; edit the table and re-run.", "",
             "Every field below is wrapped the same way: `value`, `status`, `evidence[]`, `conflicts[]`, `confidence`, `updated_by`, `updated_at`, `note`.",
             "", "## Statuses", "", "| Status | Meaning |", "|---|---|"]
    for k, v in STATUS_MEANING.items():
        lines.append(f"| `{k}` | {v} |")
    lines += ["", "## Fields by section", ""]
    by_sec = {}
    for f in FIELDS:
        by_sec.setdefault(f["path"].split(".")[0], []).append(f)
    for sec, fs in by_sec.items():
        lines += [f"### {sec}", "", SECTIONS.get(sec, ""), "",
                  "| Field | Type | Supplied by | Feeds rules | Checklist docs | Patient view | Meaning |", "|---|---|---|---|---|---|---|"]
        for f in fs:
            kind = f["kind"]
            if kind.startswith("enum:"):
                kind = "one of: " + ", ".join(kind[5:].split("|"))
            elif kind.startswith("rows:"):
                kind = "list of " + kind[5:]
            lines.append("| `{}` | {} | {} | {} | {} | {} | {} |".format(
                f["path"].split(".", 1)[1], kind, f["owner"], ", ".join(f["rules"]) or "", ", ".join(f["docs"]) or "",
                "yes" if f["pv"] else "", f["desc"].replace("|", "/")))
        lines.append("")
    lines += ["## IHH rules with no SST field yet", "",
              "These rule IDs from `providers/ihh/admission-criteria.json` are not fed by any field. Most are condition names that belong in `conditions.problem_list` via `matched_rule_ids`.", ""]
    for rid in profile["unmapped_rule_ids"]:
        r = profile["rules"][rid]
        lines.append(f"- `{rid}` ({r['section']}, {r['kind']}): {r['text'][:120]}")
    lines.append("")
    return "\n".join(lines)


def main():
    schema, blank = build_schema()
    profile = build_profile()
    example = build_example(blank)
    try:
        import jsonschema
        jsonschema.Draft202012Validator.check_schema(schema)
        v = jsonschema.Draft202012Validator(schema)
        for name, inst in (("blank", blank), ("example", example)):
            errs = sorted(v.iter_errors(inst), key=lambda e: list(e.path))
            if errs:
                for e in errs[:10]:
                    print(f"{name}: {'/'.join(map(str, e.path))}: {e.message}", file=sys.stderr)
                sys.exit(1)
        print("schema ok; blank and example validate")
    except ImportError:
        print("jsonschema not installed; skipped validation (pip install jsonschema)")
    (ROOT / "sst/sst.schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    (ROOT / "sst/sst.blank.json").write_text(json.dumps(blank, indent=2, ensure_ascii=False) + "\n")
    (ROOT / "sst/example.fictional.json").write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n")
    (ROOT / "providers/ihh/profile.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n")
    (ROOT / "docs/02b-sst-field-reference.md").write_text(build_reference(profile))
    n_rules = len(profile["rules"])
    print(f"fields: {len(FIELDS)}  rules in IHH profile: {n_rules}  unmapped: {len(profile['unmapped_rule_ids'])}  "
          f"unknown refs: {profile['unknown_references_in_field_table']}")


if __name__ == "__main__":
    main()
