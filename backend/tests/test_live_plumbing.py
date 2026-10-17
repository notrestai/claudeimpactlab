"""Exercise the LIVE code path (page images, section schemas, post-processing, merge, analyze, summary,
conversations) with a stand-in for the Claude call. Catches plumbing bugs before the first real run."""
import os, sys, pathlib, json
import pytest
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import sstlib, store
from backend.pipeline import claude_client as cc, ingest, page_index, extract, analyze, summary, conversations, gaps
import jsonschema

EX = sstlib.load_example()
CALLS = []


@pytest.fixture(autouse=True)
def live_path(monkeypatch):
    """Pretend a key exists and swap the Claude call for the stand-in, for this module only.
    Module-level side effects would leak into test_core (which runs in mock mode)."""
    monkeypatch.setenv("SST_MOCK", "0")
    monkeypatch.setenv("SST_API_KEY", "test-stand-in")
    monkeypatch.setattr(cc, "structured", fake_structured)
    monkeypatch.setattr(store, "DB_PATH", ROOT / "data" / "test-live.sqlite")
    if store.DB_PATH.exists():
        store.DB_PATH.unlink()
    store.init_db()
    yield


def strip_bookkeeping(node):
    """What the model is asked for omits the bookkeeping the pipeline fills in afterwards (sstlib.OUTPUT_DROP)."""
    if isinstance(node, list):
        return [strip_bookkeeping(n) for n in node]
    if isinstance(node, dict):
        if "status" in node and "value" in node and "evidence" in node:
            node = {k: v for k, v in node.items() if k not in sstlib.OUTPUT_DROP["Field_"]}
            node["evidence"] = [{k: v for k, v in ev.items() if k not in sstlib.OUTPUT_DROP["Evidence"]} for ev in node["evidence"]]
            return node
        return {k: strip_bookkeeping(v) for k, v in node.items()}
    return node


def fake_structured(system, content, schema, **kw):
    """Validate the request shape like the API would, then answer from the fictional example."""
    assert isinstance(system, list) and system[-1].get("cache_control"), "system must be cached blocks"
    assert isinstance(content, list) and content, "content must be a list of blocks"
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema.get("additionalProperties") is False and schema.get("required"), "strict object schema"
    assert json.dumps(schema).find('"x-') == -1, "x- annotations must be stripped"
    key = schema["required"][0]
    CALLS.append(key)
    if key in EX:
        out = {key: strip_bookkeeping(json.loads(json.dumps(EX[key])))}
    elif key == "pages":
        pages = [int(b["text"].split()[1]) for b in content if b.get("type") == "text" and b["text"].startswith("[Page ")]
        out = {"pages": [{"page": n, "doc_type": "other", "orientation": "upright", "legibility": "good", "from_earlier_stay": False, "duplicate_of_page": None} for n in pages]}
    elif key == "analysis":
        out = {"analysis": {"id": "x", "ts": "x", "actor_role": "provider", "mode": "briefing", "question": None, "answer": "Walks 150 ft per PT.",
                            "cited_sst_paths": ["function.mobility.ambulation_distance_ft", "bogus.path"], "cited_pages": [4, 99], "unknowns": [], "model": None}}
    elif key == "patient_summary":
        out = {"patient_summary": EX["patient_summary"]}
    elif key == "summary":
        out = {"summary": "Hospital will fax the swab.", "follow_ups": [{"what": "fax swab", "owner_role": "hospital", "due": None, "gap_id": None}],
               "extracted_items": [{"sst_path": "infection_control.mrsa_swab_result", "proposed_value": "pending", "quote": "will fax the swab", "timestamp": "00:01", "status": "proposed", "decided_by": None},
                                   {"sst_path": "not.a.field", "proposed_value": "x", "quote": "x", "timestamp": None, "status": "proposed", "decided_by": None}]}
    else:
        raise AssertionError(f"unexpected schema key {key}")
    jsonschema.validate(out, schema)
    return out, {"input_tokens": 10, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 0, "output_tokens": 3}, "fake-model"


def test_live_path_end_to_end():
    assert not cc.mock_mode()
    pdf = ROOT / "data" / "packets" / "R01.pdf"
    if not pdf.exists():
        import pytest; pytest.skip("packets not downloaded")
    sst = sstlib.new_sst("LIVE-TEST")
    store.create_referral(sst, str(pdf), "live plumbing")
    total = ingest.render("LIVE-TEST", str(pdf))
    assert total == 34
    rows = page_index.index_pages(sst, "LIVE-TEST", total)
    assert len(rows) == 34 and rows[33]["page"] == 34
    report = extract.extract(sst, "LIVE-TEST", workers=2)
    assert all(v == "ok" for k, v in report.items() if k != "validation"), report
    assert "validation" not in report, report.get("validation")
    assert sstlib.val(sst, "function.mobility.ambulation_distance_ft") == (150, "found")
    ev = sstlib.field(sst, "function.mobility.ambulation_distance_ft")["evidence"][0]
    assert ev["source"] == "packet" and ev["page"] == 4
    assert sstlib.validate(sst) == []
    gaps.run_all(sst, "ihh")
    row = analyze.analyze(sst, "LIVE-TEST", "briefing", None, "provider", with_pages=True)
    assert row["cited_sst_paths"] == ["function.mobility.ambulation_distance_ft"] and row["cited_pages"] == [4]
    ps = summary.patient_summary(sst, "LIVE-TEST", "es")
    assert ps["requires_staff_review"] is True
    conv = conversations.add_conversation(sst, "LIVE-TEST", {"kind": "hospital_call", "consent_confirmed": True, "transcript_text": "HOSPITAL: we will fax the swab"}, "provider")
    assert [i["sst_path"] for i in conv["extracted_items"]] == ["infection_control.mrsa_swab_result"], "disallowed paths must be dropped"
    conversations.decide_item(sst, "LIVE-TEST", conv["id"], 0, "accepted", "provider")
    assert sstlib.val(sst, "infection_control.mrsa_swab_result") == ("pending", "entered")
    assert sstlib.validate(sst) == []
    store.save_sst("LIVE-TEST", sst, "system")
    runs = store.list_runs("LIVE-TEST")
    assert any(r["stage"].startswith("extract:") and r["ok"] for r in runs)
    assert len(set(CALLS)) >= len(sstlib.SECTIONS)


def test_every_section_schema_is_strict():
    for s in sstlib.SECTIONS:
        sch = sstlib.section_schema(s)
        jsonschema.Draft202012Validator.check_schema(sch)
        blob = json.dumps(sch)
        assert '"x-' not in blob
        # every object must be strict for structured outputs
        def walk(n):
            if isinstance(n, dict):
                if n.get("type") == "object" or ("properties" in n):
                    assert n.get("additionalProperties") is False, f"{s}: object without additionalProperties false"
                    assert set(n.get("required", [])) == set(n.get("properties", {}).keys()), f"{s}: required must list every property"
                for v in n.values(): walk(v)
            elif isinstance(n, list):
                for v in n: walk(v)
        walk(sch)
