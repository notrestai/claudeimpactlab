"""Stage C: deterministic rule evaluation against a provider profile. Code decides the mechanical rules;
judgment rules are marked needs_confirmation with the question spelled out."""
from __future__ import annotations
from .. import sstlib
from ..sstlib import KNOWN

HARD_STOP_KINDS = {"exclude", "ineligible_condition", "form_exclude"}
SUPERSEDED = {"FORM-M6": "MOB-3/MOB-4", "FORM-X3": "MOB-3/MOB-4", "FORM-X6": "OTH-2"}


class Ctx:
    def __init__(self, sst):
        self.sst = sst
        self.used: list[str] = []

    def v(self, path):
        """Known value or None. Records the path. A conflict carries the reading the extractor judged most recent
        (the other reading is listed under conflicts), so rules see it; the conflict itself stays visible in the record."""
        if path not in self.used:
            self.used.append(path)
        value, status = sstlib.val(self.sst, path)
        return value if status in KNOWN or status == "conflict" else None

    def st(self, path):
        return sstlib.val(self.sst, path)[1]

    def confirmed(self, path):
        return self.st(path) == "confirmed"

    def flag(self, path):
        """True only when the flag is known true. Diagnoses are positively documented or absent."""
        return self.v(path) is True

    def rows(self, path):
        value, status = sstlib.val(self.sst, path)
        return value or []

    def problem_ids(self):
        ids = set()
        for row in self.rows("conditions.problem_list"):
            ids.update(row.get("matched_rule_ids") or [])
        return ids


# ---------- small handler factories ----------

def must_true(path, yes, no):
    def h(c):
        v = c.v(path)
        if v is True: return "met", yes
        if v is False: return "not_met", no
        return "cant_tell", f"Not in the record: {desc(path)}."
    return h


def exclude_true(path, excl, ok):
    def h(c):
        v = c.v(path)
        if v is True: return "not_met", excl
        if v is False: return "met", ok
        return "cant_tell", f"Not in the record: {desc(path)}."
    return h


def flag_exclude(path, excl):
    def h(c):
        v = c.v(path)
        if v is True: return "not_met", excl
        return "not_applicable", "Not documented."
    return h


def doc_present(path, name):
    def h(c):
        if c.v(path) is True: return "met", f"{name} is in the packet."
        return "cant_tell", f"{name} is not in the packet."
    return h


def arranged(path, ok_values, what):
    """Conditional rules: the arrangement must be documented and confirmed before admission."""
    def h(c):
        v = c.v(path)
        if v is None: return "cant_tell", f"Not in the record: {what}."
        ok = (v in ok_values) if isinstance(ok_values, (set, list, tuple)) else (v == ok_values)
        if not ok: return "not_met", f"{what} is not in place ({v})."
        if c.confirmed(path): return "met", f"{what} confirmed."
        return "needs_confirmation", f"{what} is documented ({v}); confirm it before admission."
    return h


def desc(path):
    a = sstlib.annotations().get(path)
    return a["description"] if a else path


def always(c): return True


# ---------- the rules ----------

def _adl_all(c):
    bathing = c.v("function.adls.bathing"); dressing = c.v("function.adls.dressing")
    toileting = c.v("function.adls.toileting"); eating = c.v("function.adls.eating")
    hyg = c.v("function.adls.hygiene_independent")
    vals = [bathing, dressing, toileting, eating]
    if "needs_assist" in vals or hyg is False:
        return "not_met", "Needs assistance with at least one daily living activity."
    if all(v is not None for v in vals) or hyg is True:
        return "met", "Daily living activities documented as independent."
    return "cant_tell", "Daily living activities not fully documented (PT/OT or nursing notes)."


def _mob3(c):
    d = c.v("function.mobility.ambulation_distance_ft"); lvl = c.v("function.mobility.ambulation_assist_level")
    if lvl == "non_ambulatory": return "not_met", "Documented as non-ambulatory."
    if lvl in ("min_assist", "mod_assist", "max_assist", "contact_guard"): return "not_met", f"Walking needs {lvl.replace('_', ' ')}."
    if d is not None and d < 100: return "not_met", f"Farthest documented walk is {d:g} ft; 100 ft needed."
    if d is not None and d >= 100 and lvl in ("independent", "modified_independent", "supervision", None):
        if lvl == "supervision": return "needs_confirmation", f"{d:g} ft with supervision; rule asks for modified independence."
        return "met", f"Walks {d:g} ft at {lvl or 'documented'} level."
    return "cant_tell", "No walking distance in the PT/OT notes."


def _mh4(c):
    hrs = c.v("substance_use.hours_since_intoxication_admission"); ciwa = c.v("substance_use.ciwa_latest_score"); wd = c.v("substance_use.withdrawal_signs")
    if wd is True or (ciwa is not None and ciwa >= 8): return "not_met", "Signs of alcohol withdrawal (CIWA 8 or more)."
    if hrs is None: return "cant_tell", "Hours since admission cannot be checked (dates redacted or not stated)."
    if hrs < 72: return "not_met", f"Held {hrs:g} hours; 72 needed."
    if ciwa is None and wd is None: return "cant_tell", "No CIWA score or withdrawal assessment in the packet."
    return "met", f"Held {hrs:g} hours, CIWA {ciwa if ciwa is not None else 'n/a'}, no withdrawal signs."


def _cog1(c):
    meds = c.v("cognition.can_self_administer_meds"); ins = c.v("medications.insulin_regimen"); insulin_ok = c.v("cognition.can_self_administer_insulin"); edu = c.v("cognition.can_receive_education")
    if meds is False or edu is False: return "not_met", "Cannot self-administer medications or take in teaching."
    if ins is True and insulin_ok is False: return "not_met", "On insulin but cannot self-administer it."
    if meds is None: return "cant_tell", "Self-administration of medications not documented."
    if ins is True and insulin_ok is None: return "cant_tell", "On insulin; self-administration of insulin not confirmed."
    return "met", "Self-administers medications" + (" including insulin." if ins else ".")


def _oth2(c):
    rso = c.v("safety_legal.registered_sex_offender"); viol = c.v("safety_legal.offense_involved_violence")
    if rso is not True: return "not_applicable", "Not a registered sex offender per the record." if rso is False else "Not documented."
    if viol is True: return "needs_confirmation", "Registered offender, offense involved violence: IHH says likely denied. Coordinator decides; never auto-deny."
    return "needs_confirmation", "Registered offender: case by case. Coordinator decides."


def _inf2(c):
    r = c.v("infection_control.covid_test_result"); w = c.v("infection_control.covid_test_within_24h_of_admission")
    if r == "positive": return "not_met", "COVID test positive."
    if r == "negative":
        if w is True: return "met", "Negative COVID test within 24 hours of admission."
        if w is False: return "not_met", "Negative COVID test but older than 24 hours before admission; repeat needed."
        return "cant_tell", "Negative COVID test; timing against admission cannot be checked."
    return "cant_tell", "No COVID test result in the packet."


def _dm1(c):
    n = c.v("labs.glucose_reading_count"); t = c.v("labs.glucose_trend")
    if t == "worsening": return "not_met", "Glucose readings worsening."
    if n is not None and n >= 5 and t in ("improving", "stable"): return "met", f"{n} consecutive readings, {t}."
    if n is not None and n < 5: return "cant_tell", f"Only {n} glucose readings; 5 consecutive needed."
    return "cant_tell", "Glucose readings not in the packet."


def _dm2(c):
    d = c.v("labs.dka_status")
    if d == "current": return "not_met", "In DKA."
    if d == "none": return "met", "No DKA documented."
    if d == "resolved": return "needs_confirmation", "DKA resolved. IHH's rules do not say whether resolved DKA still excludes; ask."
    return "cant_tell", "DKA status not documented."


def _dm4(c):
    pcp = c.v("disease_specific.diabetes.pcp_followup_within_7_days"); hypo = c.v("labs.hypoglycemia_last_week"); plan = c.v("disease_specific.diabetes.written_glucose_treatment_plan")
    if hypo is True: return "not_met", "Hypoglycemia in the last week."
    if pcp is False or plan is False: return "not_met", "No primary care follow-up within 5 to 7 days or no written glucose plan."
    if None in (pcp, hypo, plan): return "cant_tell", "Need: PCP follow-up within 5 to 7 days, no hypoglycemia in the last week, written glucose plan."
    if c.confirmed("disease_specific.diabetes.pcp_followup_within_7_days"): return "met", "Follow-up, no hypoglycemia and written plan confirmed."
    return "needs_confirmation", "Documented; confirm the follow-up appointment before admission."


def _id1(c):
    n = c.v("vitals.set_count"); s = c.v("vitals.sepsis_signs")
    if s is True: return "not_met", "Vital signs show signs of sepsis."
    if n is not None and n >= 5 and s is False: return "met", f"{n} sets of vitals, no sepsis signs."
    if n is not None and n < 5: return "cant_tell", f"Only {n} sets of vital signs; 5 needed."
    return "cant_tell", "Vital signs not in the packet."


def _vitals5(extra_path, extra_name):
    def h(c):
        n = c.v("vitals.set_count"); e = c.v(extra_path)
        if e is False: return "not_met", f"{extra_name} not met."
        if n is not None and n >= 5 and e is True: return "met", f"{n} sets of vitals; {extra_name.lower()}."
        if n is not None and n < 5: return "cant_tell", f"Only {n} sets of vital signs; 5 needed."
        return "cant_tell", f"Vital signs or {extra_name.lower()} not documented."
    return h


def _card2(c):
    n = c.v("labs.troponin_count"); neg = c.v("labs.troponin_negative_count")
    if n is None: return "cant_tell", "No troponin results in the packet."
    if n >= 3 and (neg or 0) >= 2: return "met", f"{n} troponins, {neg} negative."
    if n >= 3: return "not_met", f"{n} troponins but only {neg or 0} negative."
    return "cant_tell", f"Only {n} troponin results; 3 needed with 2 negative."


def _card5(c):
    ef = c.v("imaging_cardiac.ejection_fraction_percent"); base = c.v("imaging_cardiac.ef_is_patient_baseline")
    mi = c.flag("conditions.flags.recent_heart_attack"); chemo = c.flag("conditions.flags.on_chemotherapy")
    if ef is None: return "cant_tell", "Ejection fraction not in the packet."
    if ef < 25: return "not_met", f"EF {ef:g}% is below 25%."
    if ef < 50 and (mi or chemo): return "not_met", f"EF {ef:g}% with recent heart attack or chemotherapy."
    if ef < 50 and base is not True: return "needs_confirmation", f"EF {ef:g}%; confirm this is the patient's baseline."
    return "met", f"EF {ef:g}%."


def _wound_rule(check, applies_any=True):
    def h(c):
        rows = c.rows("wounds.items")
        if not rows: return "not_applicable", "No wounds documented."
        results = [check(c, w) for w in rows]
        results = [r for r in results if r]
        if not results: return "not_applicable", "Does not apply to the documented wounds."
        for status in ("not_met", "cant_tell", "needs_confirmation"):
            for r in results:
                if r[0] == status: return r
        return results[0]
    return h


def _int_e2(c, w):
    if w.get("hard_to_reach_area") is True or w.get("dressing_change_frequency"):
        wc = c.v("arrangements.wound_care_scheduled")
        if wc is False: return "not_met", f"Wound at {w.get('location')} needs wound care arranged before arrival."
        if wc is None: return "cant_tell", f"Wound at {w.get('location')}: wound care arrangement not documented."
        if c.confirmed("arrangements.wound_care_scheduled"): return "met", "Wound care scheduled and confirmed."
        return "needs_confirmation", "Wound care documented as scheduled; confirm frequency matches dressing changes."
    return None


def _int_e5(c, w):
    if w.get("staples_or_sutures") is True:
        f = w.get("removal_followup_scheduled")
        if f is True: return "met", "Removal follow-up scheduled."
        if f is False: return "not_met", "Staples or sutures without a removal appointment."
        return "cant_tell", "Staples or sutures; removal follow-up not documented."
    return None


def _int_e7(c, w):
    if w.get("wound_type") == "burn":
        t = w.get("burn_tbsa_percent")
        if t is None: return "cant_tell", "Burn without a body-surface percentage."
        if t >= 15: return "not_met", f"Burn covers {t:g}% of the body; under 15% required."
        return _int_e2(c, w) or ("met", f"Burn {t:g}% of body surface.")
    return None


def _int_x(key, text):
    def chk(c, w):
        if w.get(key) is True: return "not_met", text
        return None
    return chk


def _rsp_e3(c):
    if "RSP-E3" not in c.problem_ids(): return "not_applicable", "No COPD documented."
    if c.v("lines_devices_oxygen.oxygen_continuous") is True: return "not_met", "COPD on continuous oxygen."
    if c.v("lines_devices_oxygen.oxygen_required") is False: return "met", "COPD without oxygen."
    return "cant_tell", "COPD: oxygen requirement not documented."


def _gu_e6(c):
    if c.v("function.continence.foley_catheter") is not True: return "not_applicable", "No indwelling catheter."
    empt = c.v("function.continence.can_empty_catheter_bag"); fu = c.v("arrangements.specialty_followups") or []
    if empt is False: return "not_met", "Cannot empty the catheter bag alone."
    if empt is None: return "cant_tell", "Indwelling catheter; ability to empty the bag not documented."
    if any("cath" in s.lower() for s in fu): return "met", "Empties catheter bag; removal follow-up scheduled."
    return "needs_confirmation", "Empties catheter bag; confirm a removal follow-up is scheduled."


def _gu_e7(c):
    if c.v("lines_devices_oxygen.dialysis") is not True: return "not_applicable", "Not on dialysis."
    return arranged("arrangements.dialysis_schedule_confirmed", {True}, "Dialysis schedule")(c)


def _flag(p): return lambda c: c.flag(p)
def _src_inpatient(c): return c.v("referral.source_setting") in ("inpatient", None)
def _src_ed(c): return c.v("referral.source_setting") == "emergency_department"
def _diabetes(c): return c.flag("conditions.flags.diabetes")
def _infection(c): return c.flag("conditions.flags.active_infection")
def _resp(c): return c.flag("conditions.flags.respiratory_illness")
def _cardiac(c): return c.flag("conditions.flags.cardiac_condition")

RULES = {
    # general basis (any one of)
    "GEN-1": (always, lambda c: ("met", "Unhoused or at risk and too ill to recover outside.") if (c.v("housing.housing_situation") in ("unhoused", "at_risk", "inadequate_environment") and c.v("housing.too_ill_to_recover_outside") is True) else (("not_met", "Housed or not too ill per the record.") if c.v("housing.housing_situation") == "housed" else ("cant_tell", "Housing status or need for recuperative care not documented."))),
    "GEN-2": (always, lambda c: ("met", "Lives alone with no supports and too ill to recover.") if (c.v("housing.lives_alone_no_supports") is True and c.v("housing.too_ill_to_recover_outside") is True) else (("cant_tell", "No supports documented; whether too ill to recover outside is not stated.") if c.v("housing.lives_alone_no_supports") is True else ("not_applicable", "Not the stated basis."))),
    "GEN-3": (always, lambda c: ("met", "At risk of re-hospitalization.") if c.v("housing.readmission_risk") is True else ("not_applicable", "Not the stated basis.")),
    # ADLs
    "ADL-1": (always, _adl_all),
    "ADL-2": (always, lambda c: ("not_met", "Needs help bathing.") if c.v("function.adls.bathing") == "needs_assist" or c.v("function.adls.gathers_supplies_and_transfers_to_shower") is False else (("met", "Bathes independently (seated shower acceptable).") if c.v("function.adls.bathing") in ("independent", "seated_shower_independent") else ("cant_tell", "Bathing independence not documented."))),
    "ADL-3": (always, lambda c: ("not_met", "Needs help toileting.") if c.v("function.adls.toileting") == "needs_assist" else (("met", "Toilets or changes briefs independently.") if c.v("function.adls.toileting") in ("independent", "changes_own_briefs") else ("cant_tell", "Toileting independence not documented."))),
    # mobility
    "MOB-1": (lambda c: bool(c.v("function.mobility.dme_type")), must_true("function.mobility.dme_independent", "Uses equipment independently.", "Cannot use equipment independently.")),
    "MOB-2": (always, doc_present("documents.doc_8_pt_ot_or_nursing_notes", "PT/OT or nursing notes")),
    "MOB-3": (lambda c: c.v("function.mobility.wheelchair_dependent") is not True, _mob3),
    "MOB-4": (lambda c: c.v("function.mobility.wheelchair_dependent") is True, must_true("function.mobility.transfers_independent", "Wheelchair user who transfers independently (eligible under the outline).", "Wheelchair user who cannot transfer independently.")),
    # mental health
    "MH-1": (lambda c: bool(c.v("mental_health.psychiatric_diagnoses")), must_true("mental_health.stable_on_medications", "Stable on psychiatric medications.", "Not stable on medications.")),
    "MH-2": (always, exclude_true("mental_health.suicidal_ideation", "Suicidal ideation documented.", "No suicidal ideation.")),
    "MH-3": (always, exclude_true("mental_health.command_hallucinations", "Command hallucinations documented.", "No command hallucinations.")),
    "MH-4": (lambda c: c.v("substance_use.admitted_with_acute_alcohol_intoxication") is True, _mh4),
    "MH-5": (always, exclude_true("mental_health.conservatorship_or_fsp", "Conservatorship or FSP.", "No conservatorship or FSP.")),
    # cognition
    "COG-1": (always, _cog1),
    "COG-2": (always, lambda c: ("met", "Alert and oriented x4.") if c.v("cognition.orientation") == "AOx4" else (("not_met", f"Oriented {c.v('cognition.orientation')}; AOx4 required.") if c.v("cognition.orientation") else ("cant_tell", "Orientation not documented."))),
    # other
    "OTH-1": (always, exclude_true("mental_health.aggressive_toward_staff_or_patients", "Aggression toward staff or patients documented.", "No aggression documented.")),
    "OTH-2": (always, _oth2),
    "OTH-3": (lambda c: c.v("lines_devices_oxygen.iv_access") not in (None, "none") or c.v("medications.iv_medications_ongoing_at_discharge") is True, arranged("arrangements.home_health_status", {"established", "coordinated_and_scheduled"}, "Home health for the IV line")),
    "OTH-4": (always, lambda c: ("not_met", f"On {c.v('infection_control.isolation_status')} isolation; must be cleared before admission.") if c.v("infection_control.isolation_status") in ("contact", "droplet", "airborne") else (("met", "Not on isolation.") if c.v("infection_control.isolation_status") == "none" else ("cant_tell", "Isolation status not documented."))),
    "EOL-1": (lambda c: c.v("patient.hospice_enrolled") is True, arranged("arrangements.hospice_arranged", {True}, "Hospice services")),
    # infection control
    "INF-1": (_src_inpatient, lambda c: ("met", "MRSA swab negative.") if c.v("infection_control.mrsa_swab_result") == "negative" else (("not_met", "MRSA swab positive.") if c.v("infection_control.mrsa_swab_result") == "positive" else ("cant_tell", "No MRSA swab result in the packet."))),
    "INF-2": (_src_inpatient, _inf2),
    # diabetes
    "DM-1": (lambda c: _diabetes(c) and _src_inpatient(c), _dm1),
    "DM-2": (lambda c: _diabetes(c) and _src_ed(c), _dm2),
    "DM-3": (lambda c: _diabetes(c) and c.v("medications.insulin_regimen") is True, exclude_true("medications.insulin_started_less_than_24h_before_admission", "Insulin started less than 24 hours before admission.", "Insulin regimen established more than 24 hours.")),
    "DM-4": (lambda c: _diabetes(c) and _src_ed(c), _dm4),
    "DM-5": (_diabetes, must_true("cognition.understands_glucometer", "Understands the glucometer.", "Does not understand the glucometer.")),
    "DM-6": (lambda c: c.v("medications.insulin_regimen") is True, must_true("cognition.can_self_administer_insulin", "Understands insulin administration.", "Cannot administer own insulin.")),
    "DM-Q1": (_diabetes, lambda c: ("met", f"Insulin: {c.v('medications.insulin_regimen')}; pump: {c.v('medications.insulin_pump')}.") if c.v("medications.insulin_regimen") is not None else ("cant_tell", "Insulin use not documented.")),
    "DM-Q2": (_diabetes, lambda c: ("met", f"Oral diabetes medication: {c.v('medications.oral_diabetes_meds')}.") if c.v("medications.oral_diabetes_meds") is not None else ("cant_tell", "Oral diabetes medication not documented.")),
    "DM-Q3": (_diabetes, lambda c: ("met", f"Last A1C {c.v('labs.a1c')}.") if c.v("labs.a1c") is not None else ("cant_tell", "No A1C in the packet (informational).")),
    # infection
    "ID-1": (_infection, _id1),
    "ID-2": (lambda c: c.v("disease_specific.infection.on_oral_antibiotics") is True, lambda c: must_true("disease_specific.infection.can_self_administer_oral_antibiotics", "Takes oral antibiotics independently.", "Cannot take oral antibiotics independently.")(c) if c.v("disease_specific.infection.can_self_administer_oral_antibiotics") is not None else must_true("cognition.can_self_administer_meds", "Self-administers medications (covers oral antibiotics).", "Cannot self-administer medications.")(c)),
    "ID-3": (lambda c: c.v("disease_specific.infection.on_iv_antibiotics") is True, arranged("arrangements.home_health_status", {"ordered", "established", "coordinated_and_scheduled"}, "Home health for IV antibiotics")),
    "ID-Q1": (_infection, lambda c: ("met", f"Infection at {c.v('disease_specific.infection.location')}.") if c.v("disease_specific.infection.location") else ("cant_tell", "Infection location not documented.")),
    "ID-Q2": (_infection, lambda c: ("met", f"{len(c.rows('labs.cultures'))} culture result(s) in the packet.") if c.rows("labs.cultures") else ("cant_tell", "No culture results in the packet.")),
    "ID-Q3": (_infection, lambda c: ("met", f"Isolation: {c.v('infection_control.isolation_status')}.") if c.v("infection_control.isolation_status") else ("cant_tell", "Isolation status not documented.")),
    # respiratory
    "RESP-1": (_resp, doc_present("imaging_cardiac.chest_imaging_present", "Chest imaging")),
    "RESP-2": (_resp, _vitals5("vitals.spo2_stable_at_baseline", "Oxygen saturation stable at baseline")),
    "RESP-3": (lambda c: c.v("lines_devices_oxygen.oxygen_required") is True, arranged("arrangements.oxygen_ordered_tank_ready_day_before", {True}, "Oxygen ordered with the tank at the site the day before")),
    "RESP-4": (_resp, doc_present("lines_devices_oxygen.respiratory_therapy_notes_present", "Respiratory therapy notes")),
    "RESP-Q1": (_resp, lambda c: ("met", "Breathing treatments: " + ", ".join(c.v("lines_devices_oxygen.breathing_treatments") or ["none listed"]) + ".") if sstlib.known(c.sst, "lines_devices_oxygen.breathing_treatments") else ("cant_tell", "Breathing treatments not documented.")),
    "RESP-Q2": (_resp, lambda c: ("met", f"Antibiotics for the respiratory condition: {c.v('disease_specific.respiratory.on_antibiotics_for_respiratory')}.") if c.v("disease_specific.respiratory.on_antibiotics_for_respiratory") is not None else ("cant_tell", "Not documented.")),
    # cardiac
    "CARD-1": (_cardiac, _vitals5("vitals.bp_stable_on_oral_meds_24h", "Blood pressure stable on oral medication for 24 hours")),
    "CARD-2": (lambda c: _cardiac(c) or c.v("labs.troponin_count") is not None, _card2),
    "CARD-3": (_cardiac, doc_present("imaging_cardiac.ekg_present", "EKG")),
    "CARD-4": (lambda c: c.v("imaging_cardiac.atrial_fibrillation") is True, must_true("disease_specific.cardiac.anticoag_independent_and_understands_inr", "Manages anticoagulation and INR checks.", "Cannot manage anticoagulation independently.")),
    "CARD-5": (lambda c: c.v("imaging_cardiac.ejection_fraction_percent") is not None or c.flag("conditions.flags.recent_heart_attack") or c.flag("conditions.flags.on_chemotherapy"), _card5),
    "CARD-6": (lambda c: _cardiac(c) or c.flag("conditions.flags.hypertension_uncontrolled_or_symptomatic"), exclude_true("medications.iv_antihypertensive_last_24h", "IV blood pressure medication within 24 hours.", "No IV blood pressure medication in 24 hours.")),
    # referral form screen (the outline wins where they overlap)
    "FORM-M1": (always, lambda c: ("met", "Unhoused, at risk, or inadequate environment.") if c.v("housing.housing_situation") in ("unhoused", "at_risk", "inadequate_environment") else (("not_met", "Housed.") if c.v("housing.housing_situation") == "housed" else ("cant_tell", "Housing status not documented."))),
    "FORM-M2": (always, lambda c: ("not_met", "Not medically or psychiatrically stable.") if (c.v("vitals.sepsis_signs") is True or c.v("mental_health.stable_on_medications") is False) else (("met", "Stable per the record.") if (c.v("vitals.sepsis_signs") is False and c.v("mental_health.stable_on_medications") in (True, None)) else ("cant_tell", "Stability not fully documented."))),
    "FORM-M3": (always, lambda c: RULES["COG-2"][1](c)),
    "FORM-M4": (always, _adl_all),
    "FORM-M5": (always, must_true("cognition.can_self_administer_meds", "Self-administers medications.", "Cannot self-administer medications.")),
    "FORM-X1": (always, exclude_true("cognition.cognitive_impairment", "Cognitive impairment documented.", "No cognitive impairment.")),
    "FORM-X2": (always, lambda c: {"met": ("met", "Performs daily living activities."), "not_met": ("not_met", "Unable to perform daily living activities."), "cant_tell": ("cant_tell", "Daily living activities not documented.")}[_adl_all(c)[0]]),
    "FORM-X4": (always, lambda c: ("not_met", "Active MRSA, TB or C. diff not cleared.") if ((c.v("infection_control.mrsa_swab_result") == "positive" or c.v("infection_control.tb_screen_result") == "positive" or c.v("infection_control.c_diff") == "positive") and c.v("infection_control.active_infection_cleared_by_physician") is not True) else (("met", "No uncleared MRSA, TB or C. diff.") if (c.v("infection_control.mrsa_swab_result") in ("negative", "not_done") or c.v("infection_control.active_infection_cleared_by_physician") is True) else ("cant_tell", "MRSA, TB and C. diff status not documented."))),
    "FORM-X5": (always, lambda c: ("not_met", "Non-compliance with detox or refusal to abstain.") if c.v("substance_use.detox_noncompliance_or_refusal_to_abstain") is True else (("met", "No detox non-compliance documented.") if c.v("substance_use.detox_noncompliance_or_refusal_to_abstain") is False else ("not_applicable", "Not documented."))),
    "FORM-X7": (always, lambda c: RULES["OTH-1"][1](c)),
    # body systems with their own checks
    "INT-E2": (always, _wound_rule(_int_e2)),
    "INT-E5": (always, _wound_rule(_int_e5)),
    "INT-E7": (always, _wound_rule(_int_e7)),
    "INT-X1": (always, _wound_rule(_int_x("requires_skin_graft", "Wound requires skin grafting."))),
    "INT-X2": (always, _wound_rule(_int_x("complicated_large_abscess", "Complicated large abscess (undefined in the rules; coordinator judgment)."))),
    "INT-X3": (always, _wound_rule(_int_x("inhalation_injury", "Burn with inhalation injury."))),
    "RSP-E3": (always, _rsp_e3),
    "GU-E6": (always, _gu_e6),
    "GU-E7": (always, _gu_e7),
    "MSK-E2": (lambda c: "MSK-E1" in c.problem_ids() or "MSK-E4" in c.problem_ids(), arranged("arrangements.pt_scheduled", {True}, "Physical therapy after discharge")),
    "CIR-E5": (lambda c: c.flag("conditions.flags.post_cabg_or_valve_surgery"), lambda c: ("not_met", "Post cardiac surgery with abnormal blood counts.") if c.v("labs.hemoglobin_hematocrit_normal") is False else arranged("arrangements.home_health_status", {"coordinated_and_scheduled", "established"}, "Home health and cardiac surgery follow-up")(c)),
}

# ineligible conditions answered by a flag or field
FLAG_X = {
    "NEU-X1": ("conditions.flags.stroke_with_residual_deficit", "Stroke with residual deficit."),
    "NEU-X2": ("conditions.flags.new_onset_seizure_with_deficit", "New-onset seizure with deficit."),
    "NEU-X5": ("conditions.flags.spinal_fusion", "Cervical or lumbar fusion."),
    "RSP-X1": ("conditions.flags.pneumothorax_or_hemothorax", "Pneumothorax or hemothorax."),
    "RSP-X2": ("conditions.flags.pneumothorax_or_hemothorax", "Pneumothorax or hemothorax."),
    "RSP-X4": ("conditions.flags.pulmonary_embolism", "Pulmonary embolism."),
    "RSP-X5": ("lines_devices_oxygen.bipap", "Requires BiPAP."),
    "MSK-X1": ("conditions.flags.open_fracture", "Open fracture."),
    "MSK-X2": ("conditions.flags.osteomyelitis", "Osteomyelitis."),
    "MSK-X3": ("lines_devices_oxygen.external_fixation_device", "External fixation device."),
    "MSK-X4": ("conditions.flags.recent_amputation_with_wounds", "Immediately after amputation with wounds."),
    "MSK-X5": ("conditions.flags.jaw_wired", "Jaw wired after facial fracture."),
    "GI-X1": ("conditions.flags.decompensated_cirrhosis", "Decompensated cirrhosis."),
    "GI-X2": ("conditions.flags.recent_gi_bleed_or_varices", "Immediately after bleeding ulcer or varices."),
    "END-X1": ("conditions.flags.immediately_post_parathyroidectomy", "Immediately after parathyroidectomy."),
    "END-X2": ("conditions.flags.alcoholic_ketoacidosis", "Alcoholic ketoacidosis."),
    "CIR-X1": ("imaging_cardiac.unstable_arrhythmia", "Unstable cardiac arrhythmia."),
    "CIR-X2": ("conditions.flags.hypertension_uncontrolled_or_symptomatic", "Hypertension needing IV medication, symptomatic, or not responding to oral medication."),
    "NEU-X4": ("cognition.cognitive_impairment", "Dementia or cognitive impairment."),
}
VALUE_X = {
    "NEU-X3": ("conditions.flags.tbi_severity", {"moderate", "severe"}, "Moderate or severe traumatic brain injury."),
    "RSP-X3": ("lines_devices_oxygen.tracheostomy", {"ventilator"}, "Tracheostomy on a ventilator."),
    "GI-X3": ("infection_control.c_diff", {"positive"}, "C. difficile."),
    "END-X3": ("labs.dka_status", {"current"}, "Diabetic ketoacidosis."),
}
ESCALATE = {"NEU-X6": ("conditions.flags.immediately_post_neurosurgery", "Immediately after neurosurgery: escalate for clinical review.")}


def _system_rule(rid, kind, c):
    ids = c.problem_ids()
    if rid in FLAG_X:
        path, text = FLAG_X[rid]
        if c.v(path) is True or rid in ids: return "not_met", text
        return "not_applicable", "Not documented."
    if rid in VALUE_X:
        path, bad, text = VALUE_X[rid]
        if c.v(path) in bad or rid in ids: return "not_met", text
        return "not_applicable", "Not documented."
    if rid in ESCALATE:
        path, text = ESCALATE[rid]
        if c.v(path) is True or rid in ids: return "needs_confirmation", text
        return "not_applicable", "Not documented."
    if kind == "ineligible_condition":
        if rid in ids: return "not_met", "Ineligible condition documented in the problem list."
        return "not_applicable", "Not documented."
    if rid in ids: return "met", "Eligible condition documented in the problem list."
    return "not_applicable", "Not documented."


def evaluate(sst: dict, provider_id: str = "ihh") -> dict:
    profile = sstlib.load_profile(provider_id)
    results, hard_stops, open_questions = [], [], []
    for rid, rule in profile["rules"].items():
        c = Ctx(sst)
        kind = rule["kind"]
        if rid in SUPERSEDED:
            status, basis = "not_applicable", f"Superseded by {SUPERSEDED[rid]} (IHH's internal outline wins over the form)."
        elif rid in RULES:
            applies, handler = RULES[rid]
            if not applies(c):
                status, basis = "not_applicable", "Does not apply to this patient per the record."
            else:
                status, basis = handler(c)
        else:
            status, basis = _system_rule(rid, kind, c)
        evidence = []
        for p in c.used:
            f = sstlib.field(sst, p)
            if f and f["status"] in KNOWN:
                evidence.extend(f["evidence"][:1])
        results.append({"rule_id": rid, "status": status, "basis": basis, "sst_fields": c.used, "evidence": evidence[:3],
                        "confirmed_by": None, "confirmed_at": None})
        if status == "not_met" and kind in HARD_STOP_KINDS:
            hard_stops.append(rid)
        if kind == "ask" and status == "cant_tell":
            open_questions.append({"rule_id": rid, "question": rule["text"]})
    gen = [r for r in results if profile["rules"][r["rule_id"]]["kind"] == "any_of_general"]
    gen_ok = any(r["status"] == "met" for r in gen)
    blocking = [r for r in results if profile["rules"][r["rule_id"]]["kind"] in ("must", "exclude", "document", "conditional", "form_must", "form_exclude", "eligible_condition", "ineligible_condition")]
    cant = [r["rule_id"] for r in blocking if r["status"] == "cant_tell"]
    confirm = [r["rule_id"] for r in blocking if r["status"] == "needs_confirmation"]
    not_met_soft = [r["rule_id"] for r in blocking if r["status"] == "not_met" and r["rule_id"] not in hard_stops]
    if hard_stops:
        rec, why = "likely_ineligible", f"{len(hard_stops)} excluding rule(s) met: {', '.join(hard_stops)}."
    elif not_met_soft:
        rec, why = "request_information", f"Requirements not met yet: {', '.join(not_met_soft)}. These can change if the hospital arranges or documents them."
    elif cant or not gen_ok:
        rec, why = "request_information", f"{len(cant)} rule(s) cannot be checked from the record: {', '.join(cant[:8])}{'...' if len(cant) > 8 else ''}."
    elif confirm:
        rec, why = "request_information", f"Arrangements to confirm before admission: {', '.join(confirm)}."
    else:
        rec, why = "likely_eligible", "Every applicable rule is met. The coordinator decides."
    if not gen_ok and rec != "likely_ineligible":
        why += " The basis for recuperative care (homeless and too ill to recover outside) is not documented."
    el = sst["eligibility"]
    el.update({"provider_id": provider_id, "profile_version": profile["profile_version"], "evaluated_at": sstlib.now_iso(),
               "rules": results, "hard_stops": hard_stops, "open_questions": open_questions,
               "recommendation": rec, "recommendation_rationale": why})
    sstlib.add_history(sst, "system", "evaluated", "eligibility", None, rec, f"{provider_id} {profile['profile_version']}")
    return el
