import os, sys, pathlib
os.environ["SST_MOCK"] = "1"
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import sstlib, scrub, roles, store
from backend.pipeline import gaps, score, evaluate

store.DB_PATH = ROOT / "data" / "test.sqlite"
if store.DB_PATH.exists():
    store.DB_PATH.unlink()


def test_example_rules():
    ex = sstlib.load_example()
    el = gaps.run_all(ex, "ihh")
    by = {r["rule_id"]: r for r in el["rules"]}
    assert by["MOB-3"]["status"] == "met"
    assert by["COG-2"]["status"] == "met"
    assert by["INF-1"]["status"] == "cant_tell"
    assert by["DM-1"]["status"] == "cant_tell"
    assert by["FORM-M6"]["status"] == "not_applicable"
    assert el["hard_stops"] == []
    assert el["recommendation"] == "request_information"
    assert ex["eligibility"]["scores"]["band"] == "needs_documents"
    assert sstlib.validate(ex) == []


def test_hard_stop_blocks():
    ex = sstlib.load_example()
    sstlib.set_field(ex, "mental_health.suicidal_ideation", True, "found", "extractor", evidence=[sstlib.packet_evidence(3, "h_and_p", "endorses SI")])
    el = gaps.run_all(ex, "ihh")
    assert "MH-2" in el["hard_stops"]
    assert el["recommendation"] == "likely_ineligible"
    assert ex["eligibility"]["scores"]["band"] == "blocked_by_hard_stop"


def test_flag_x_rule():
    ex = sstlib.load_example()
    sstlib.set_field(ex, "conditions.flags.pulmonary_embolism", True, "found", "extractor", evidence=[sstlib.packet_evidence(3, "h_and_p", "acute PE")])
    el = gaps.run_all(ex, "ihh")
    assert "RSP-X4" in el["hard_stops"]


def test_blank_scores_zero_ish():
    b = sstlib.new_sst("T1")
    el = gaps.run_all(b, "ihh")
    assert el["recommendation"] == "request_information"
    assert b["eligibility"]["scores"]["components"]["completeness"]["score"] == 0
    assert sstlib.validate(b) == []


def test_scrub():
    t, flags = scrub.scrub("Mr. Jones DOB 03/04/1968 phone 714-555-0199 at 10:30 MRN 00123456")
    kinds = {f["kind"] for f in flags}
    assert {"date", "phone", "mrn", "name"} <= kinds or {"dob", "phone", "mrn", "name"} <= kinds
    assert "10:30" in t and "714" not in t


def test_roles():
    ex = sstlib.load_example()
    p = roles.filter_for_role(ex, "patient")
    assert "eligibility" not in p and "labs" not in p
    assert roles.can_write("patient", "patient.has_phone")
    assert not roles.can_write("patient", "vitals.set_count")
    assert not roles.can_write("hospital", "forms.ihh_referral_form.staff_section_completed")


def test_api_flow():
    from fastapi.testclient import TestClient
    from backend.app import app
    c = TestClient(app)
    r = c.post("/referrals?fixture=true", headers={"X-Role": "provider"}); assert r.status_code == 200, r.text
    rid = r.json()["referral_id"]
    r = c.post(f"/referrals/{rid}/evaluate", headers={"X-Role": "provider"}); assert r.status_code == 200
    assert r.json()["eligibility"]["recommendation"] == "request_information"
    r = c.get(f"/referrals/{rid}/sst", headers={"X-Role": "patient"}); assert r.status_code == 200
    assert "eligibility" not in r.json()
    r = c.patch(f"/referrals/{rid}/sst", json=[{"path": "patient.has_phone", "value": True}], headers={"X-Role": "patient"}); assert r.status_code == 200
    r = c.patch(f"/referrals/{rid}/sst", json=[{"path": "vitals.set_count", "value": 5}], headers={"X-Role": "patient"}); assert r.status_code == 403
    r = c.post(f"/referrals/{rid}/analyze", json={"mode": "briefing"}, headers={"X-Role": "patient"}); assert r.status_code == 403
    r = c.post(f"/referrals/{rid}/analyze", json={"mode": "briefing"}, headers={"X-Role": "provider"}); assert r.status_code == 200
    assert r.json()["model"] == "mock"
    r = c.post(f"/referrals/{rid}/conversations", json={"kind": "hospital_call", "consent_confirmed": False, "transcript_text": "x"}, headers={"X-Role": "provider"}); assert r.status_code == 400
    r = c.post(f"/referrals/{rid}/conversations", json={"kind": "hospital_call", "consent_confirmed": True, "transcript_source": "pasted_text",
               "transcript_text": "HOSPITAL: The PCP follow-up is booked. We will fax the MRSA result, DOB 01/02/1970, call 714-555-0100."}, headers={"X-Role": "provider"})
    assert r.status_code == 200, r.text
    conv = r.json(); assert conv["identifier_scrub"] == "flagged" and "714" not in conv["transcript_text"]
    assert conv["extracted_items"], "mock should propose at least one item"
    before = c.post(f"/referrals/{rid}/score", headers={"X-Role": "provider"}).json()["readiness"]
    r = c.post(f"/referrals/{rid}/conversations/{conv['id']}/items/0", json={"status": "accepted"}, headers={"X-Role": "provider"}); assert r.status_code == 200, r.text
    sst = c.get(f"/referrals/{rid}/sst", headers={"X-Role": "provider"}).json()
    f = sstlib.field(sst, conv["extracted_items"][0]["sst_path"]); assert f["status"] == "entered" and f["evidence"][0]["source"] == "transcript"
    r = c.post(f"/referrals/{rid}/decision", json={"decision": "approve"}, headers={"X-Role": "provider"}); assert r.status_code == 409
    r = c.post(f"/referrals/{rid}/summary?language=es", headers={"X-Role": "provider"}); assert r.status_code == 200 and r.json()["requires_staff_review"] is True
    r = c.get(f"/referrals/{rid}/sst", headers={"X-Role": "patient"}).json(); assert r["patient_summary"]["where_things_stand"] is None
    c.post(f"/referrals/{rid}/summary/review", headers={"X-Role": "provider"})
    r = c.get(f"/referrals/{rid}/sst", headers={"X-Role": "patient"}).json(); assert r["patient_summary"]["where_things_stand"]
    r = c.get(f"/referrals/{rid}/forms/ihh_referral_form", headers={"X-Role": "hospital"}); assert r.status_code == 200 and r.json()["sections"]
    r = c.get(f"/referrals/{rid}/reply", headers={"X-Role": "provider"}); assert "Draft prepared" in r.text
    r = c.get(f"/referrals/{rid}/scores", headers={"X-Role": "hospital"}); assert r.status_code == 200 and r.json()[0]["provider_id"] == "ihh"
