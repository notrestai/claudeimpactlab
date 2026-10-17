"""FastAPI service. Every response is JSON except page images, the form rendering and the frontend."""
from __future__ import annotations
import pathlib, shutil, uuid
from typing import Any
from fastapi import FastAPI, File, Header, HTTPException, UploadFile, Query, Body
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from . import sstlib, store, roles, forms
from .pipeline import ingest, page_index, extract, gaps as gaps_mod, score as score_mod, analyze as analyze_mod, summary as summary_mod, conversations as conv_mod, claude_client as cc

app = FastAPI(title="Relay Health", version="0.2")
import threading
JOBS: dict[str, dict] = {}


def start_job(rid: str, name: str, fn):
    """Run fn(progress) in a background thread. Cloudflare cuts requests at 100 s; extraction takes minutes."""
    if JOBS.get(rid, {}).get("status") == "running":
        raise HTTPException(409, f"a job is already running for this referral: {JOBS[rid]['job']}")
    JOBS[rid] = {"job": name, "status": "running", "started_at": sstlib.now_iso(), "ended_at": None, "progress": "starting", "detail": None, "error": None}

    def progress(msg):
        JOBS[rid]["progress"] = msg

    def target():
        try:
            detail = fn(progress)
            JOBS[rid].update(status="done", detail=detail, ended_at=sstlib.now_iso(), progress="done")
        except Exception as e:  # noqa: BLE001
            JOBS[rid].update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}", ended_at=sstlib.now_iso())

    threading.Thread(target=target, daemon=True).start()
    return JOBS[rid]
from fastapi.middleware.cors import CORSMiddleware
import os as _os
app.add_middleware(CORSMiddleware,
                   allow_origins=[o.strip() for o in _os.environ.get("SST_CORS_ORIGINS", "https://relayhealth.dev,https://www.relayhealth.dev").split(",") if o.strip()],
                   allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?|https://[a-z0-9-]+\.pages\.dev|https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev|https://([a-z0-9-]+\.)?relayhealth\.dev",
                   allow_methods=["*"], allow_headers=["*"])
store.init_db()
FRONTEND = sstlib.ROOT / "frontend"
UPLOADS = sstlib.ROOT / "data" / "uploads"


def role_of(x_role: str | None) -> str:
    try:
        return roles.check_role(x_role)
    except PermissionError as e:
        raise HTTPException(400, str(e))


def need(role: str, *allowed: str):
    if role not in allowed:
        raise HTTPException(403, f"role {role} may not do this")


def load(rid: str) -> dict:
    sst = store.load_sst(rid)
    if sst is None:
        raise HTTPException(404, "referral not found")
    return sst


def save(rid: str, sst: dict, role: str, note: str | None = None):
    try:
        return store.save_sst(rid, sst, role, note)
    except ValueError as e:
        raise HTTPException(500, str(e))


@app.get("/health")
def health():
    return {"ok": True, "mock": cc.mock_mode(), "model": cc.MODEL_MAIN, "sections": sstlib.SECTIONS}


@app.get("/providers")
def providers():
    return sstlib.list_providers()


@app.get("/sst/schema")
def schema():
    return sstlib.load_schema()


@app.get("/referrals")
def list_referrals(x_role: str | None = Header(None)):
    role_of(x_role)
    return store.list_referrals()


@app.post("/referrals")
async def create_referral(x_role: str | None = Header(None), packet: UploadFile | None = File(None), label: str | None = Query(None),
                          fixture: bool = Query(False, description="Create from the fictional example instead of a packet")):
    role = role_of(x_role); need(role, "provider", "hospital")
    if fixture:
        sst = sstlib.load_example()
        sst["meta"]["referral_id"] = f"FIX-{uuid.uuid4().hex[:6].upper()}"
        sst["meta"]["created_at"] = sst["meta"]["updated_at"] = sstlib.now_iso()
        rid = store.create_referral(sst, None, label or "fictional example")
        return {"referral_id": rid, "fictional": True}
    sst = sstlib.new_sst()
    rid = sst["meta"]["referral_id"]
    path = None
    if packet is not None:
        UPLOADS.mkdir(parents=True, exist_ok=True)
        path = UPLOADS / f"{rid}.pdf"
        with path.open("wb") as f:
            shutil.copyfileobj(packet.file, f)
        sst["meta"]["packet"]["source_file"] = str(path)
    sst["meta"]["parties"]["providers"] = [{"provider_id": "ihh", "name": "Illumination Health + Home", "status": "sent", "updated_at": sstlib.now_iso()}]
    sstlib.add_history(sst, role, "edited", "meta", None, None, "referral created" + (" with packet" if path else ""))
    store.create_referral(sst, str(path) if path else None, label)
    return {"referral_id": rid, "packet": bool(path)}


@app.post("/referrals/{rid}/ingest", status_code=202)
def do_ingest(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid); ref = store.get_referral(rid)
    if not ref or not ref["packet_path"]:
        raise HTTPException(400, "no packet on this referral")

    def work(progress):
        progress("rendering pages")
        total = ingest.render(rid, ref["packet_path"])
        progress(f"indexing {total} pages")
        page_index.index_pages(sst, rid, total)
        store.update_referral(rid, page_count=total, status="indexed")
        store.save_sst(rid, sst, role, "ingest")
        return {"page_count": total}
    return start_job(rid, "ingest", work)


@app.post("/referrals/{rid}/extract", status_code=202)
def do_extract(rid: str, x_role: str | None = Header(None), pages: str | None = Query(None, description="comma-separated page numbers to (re)extract"),
               sections: str | None = Query(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid)
    if not sst["meta"]["packet"]["page_count"] and not cc.mock_mode():
        raise HTTPException(400, "run ingest first")
    pg = [int(p) for p in pages.split(",")] if pages else None
    sc = [s for s in sections.split(",")] if sections else None

    def work(progress):
        progress("extracting sections")
        report = extract.extract(sst, rid, sections=sc, pages=pg)
        store.update_referral(rid, status="extracted")
        store.save_sst(rid, sst, role, "extract")
        return {"report": report, "mock": cc.mock_mode()}
    return start_job(rid, "extract", work)


@app.get("/referrals/{rid}/jobs")
def job_status(rid: str, x_role: str | None = Header(None)):
    role_of(x_role)
    return JOBS.get(rid) or {"status": "idle"}


@app.post("/referrals/{rid}/evaluate")
def do_evaluate(rid: str, x_role: str | None = Header(None), provider: str = Query("ihh")):
    role = role_of(x_role); need(role, "provider")
    sst = load(rid)
    el = gaps_mod.run_all(sst, provider)
    store.update_referral(rid, status="evaluated")
    save(rid, sst, role, f"evaluate {provider}")
    return {"eligibility": el, "gaps": sst["gaps"], "reply": gaps_mod.draft_reply(sst)}


@app.post("/referrals/{rid}/score")
def do_score(rid: str, x_role: str | None = Header(None), provider: str = Query("ihh")):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid)
    if not sst["eligibility"]["rules"]:
        gaps_mod.run_all(sst, provider)
    s = score_mod.score(sst, provider)
    save(rid, sst, role, "score")
    return s


@app.get("/referrals/{rid}/scores")
def all_scores(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid)
    import copy
    out = []
    for p in sstlib.list_providers():
        tmp = copy.deepcopy(sst)
        gaps_mod.run_all(tmp, p["provider_id"])
        s = tmp["eligibility"]["scores"]
        out.append({"provider_id": p["provider_id"], "name": p["name"], "readiness": s["readiness"], "band": s["band"],
                    "hard_stops": tmp["eligibility"]["hard_stops"], "missing": s["components"]["completeness"]["missing_doc_ids"],
                    "cant_tell": s["components"]["rule_coverage"]["cant_tell_rule_ids"]})
    return out


@app.post("/referrals/{rid}/summary")
def do_summary(rid: str, x_role: str | None = Header(None), language: str | None = Query(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid)
    ps = summary_mod.patient_summary(sst, rid, language)
    save(rid, sst, role, "summary")
    return ps


@app.post("/referrals/{rid}/summary/review")
def review_summary(rid: str, x_role: str | None = Header(None), actor_label: str | None = Query(None)):
    role = role_of(x_role); need(role, "provider")
    sst = load(rid)
    sst["patient_summary"]["requires_staff_review"] = False
    sst["patient_summary"]["reviewed_by"] = actor_label or "Referral coordinator"
    sst["patient_summary"]["reviewed_at"] = sstlib.now_iso()
    sstlib.add_history(sst, role, "confirmed", "patient_summary", "needs review", "reviewed", None, actor_label)
    save(rid, sst, role, "summary reviewed")
    return sst["patient_summary"]


@app.post("/referrals/{rid}/run_all", status_code=202)
def run_all(rid: str, x_role: str | None = Header(None), provider: str = Query("ihh")):
    """Demo convenience, as a background job: ingest (if a packet), extract, evaluate, gaps, score, summary."""
    role = role_of(x_role); need(role, "provider")
    sst = load(rid); ref = store.get_referral(rid)

    def work(progress):
        steps = {}
        if ref and ref["packet_path"]:
            progress("rendering pages")
            total = ingest.render(rid, ref["packet_path"])
            progress(f"indexing {total} pages")
            page_index.index_pages(sst, rid, total); steps["ingest"] = total
            store.update_referral(rid, page_count=total)
            store.save_sst(rid, sst, role, "run_all: ingest")
        progress("extracting the record from the pages (minutes)")
        steps["extract"] = extract.extract(sst, rid)
        store.save_sst(rid, sst, role, "run_all: extract")
        progress("evaluating rules, gaps and score")
        gaps_mod.run_all(sst, provider); steps["evaluate"] = sst["eligibility"]["recommendation"]
        progress("writing the patient summary")
        summary_mod.patient_summary(sst, rid); steps["summary"] = sst["patient_summary"]["language"]
        store.update_referral(rid, status="evaluated")
        store.save_sst(rid, sst, role, "run_all")
        return {"steps": steps, "readiness": sst["eligibility"]["scores"]["readiness"], "band": sst["eligibility"]["scores"]["band"], "mock": cc.mock_mode()}
    return start_job(rid, "run_all", work)


@app.get("/referrals/{rid}/sst")
def get_sst(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role)
    return roles.filter_for_role(load(rid), role)


@app.patch("/referrals/{rid}/sst")
def patch_sst(rid: str, changes: list[dict[str, Any]] = Body(...), x_role: str | None = Header(None), actor_label: str | None = Query(None)):
    role = role_of(x_role)
    sst = load(rid)
    applied, refused = [], []
    for ch in changes:
        path = ch.get("path")
        if not roles.can_write(role, path):
            refused.append({"path": path, "reason": "not this role's field"}); continue
        if "value" in ch:
            sstlib.set_field(sst, path, ch["value"], "entered", role, evidence=[{"source": "entered", "page": None, "doc_type": None, "ref": None, "timestamp": None, "quote": str(ch["value"])}],
                             note=ch.get("note"), actor_label=actor_label)
        elif ch.get("note") is not None:
            f = sstlib.field(sst, path)
            f["note"] = ch["note"]; sstlib.add_history(sst, role, "commented", path, None, ch["note"], None, actor_label)
        applied.append(path)
    if applied and sst["eligibility"]["rules"]:
        gaps_mod.run_all(sst, sst["eligibility"]["provider_id"] or "ihh")
    if refused and not applied:
        raise HTTPException(403, {"refused": refused})
    save(rid, sst, role, f"patch {len(applied)} field(s)")
    return {"applied": applied, "refused": refused, "readiness": sst["eligibility"]["scores"]["readiness"]}


@app.post("/referrals/{rid}/confirm")
def confirm(rid: str, paths: list[str] = Body(...), x_role: str | None = Header(None), actor_label: str | None = Query(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid)
    done = []
    for p in paths:
        f = sstlib.field(sst, p)
        if f is None or f["status"] not in sstlib.KNOWN:
            continue
        f["status"] = "confirmed"; f["updated_by"] = role; f["updated_at"] = sstlib.now_iso()
        sstlib.add_history(sst, role, "confirmed", p, None, str(f["value"]), None, actor_label); done.append(p)
    if done and sst["eligibility"]["rules"]:
        gaps_mod.run_all(sst, sst["eligibility"]["provider_id"] or "ihh")
    save(rid, sst, role, f"confirm {len(done)}")
    return {"confirmed": done}


@app.post("/referrals/{rid}/gaps/{gap_id}")
def update_gap(rid: str, gap_id: str, body: dict = Body(...), x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid)
    g = next((g for g in sst["gaps"] if g["id"] == gap_id), None)
    if g is None:
        raise HTTPException(404, "gap not found")
    status = body.get("status")
    if status not in ("open", "requested", "received", "resolved", "not_available"):
        raise HTTPException(400, "bad status")
    old = g["status"]; g["status"] = status
    if status == "requested": g["requested_at"] = sstlib.now_iso()
    if status == "received": g["received_at"] = sstlib.now_iso()
    if body.get("note") is not None: g["note"] = body["note"]
    sstlib.add_history(sst, role, "requested" if status == "requested" else "received" if status == "received" else "edited", f"gaps.{gap_id}", old, status, body.get("note"))
    save(rid, sst, role, f"gap {gap_id} {status}")
    return g


@app.post("/referrals/{rid}/decision")
def decision(rid: str, body: dict = Body(...), x_role: str | None = Header(None), actor_label: str | None = Query(None)):
    role = role_of(x_role); need(role, "provider")
    sst = load(rid); el = sst["eligibility"]
    d = body.get("decision")
    if d not in ("approve", "deny", "request_information"):
        raise HTTPException(400, "decision must be approve, deny or request_information")
    if d == "approve":
        unconfirmed = []
        for r in el["rules"]:
            if r["status"] in ("met", "not_met") and (r["rule_id"] in el["hard_stops"] or sstlib.load_profile(el["provider_id"] or "ihh")["rules"][r["rule_id"]]["kind"] in ("must", "exclude")):
                for p in r["sst_fields"]:
                    f = sstlib.field(sst, p)
                    if f and f["status"] in ("found", "entered"):
                        unconfirmed.append(p)
        if unconfirmed:
            raise HTTPException(409, {"message": "confirm the fields behind the decision first", "unconfirmed": sorted(set(unconfirmed))[:20]})
    el["coordinator_decision"] = {"decision": d, "reason": body.get("reason"), "decided_by": actor_label or "Referral coordinator", "decided_at": sstlib.now_iso()}
    for p in sst["meta"]["parties"]["providers"]:
        if p["provider_id"] == (el["provider_id"] or "ihh"):
            p["status"] = {"approve": "approved", "deny": "denied", "request_information": "information_requested"}[d]; p["updated_at"] = sstlib.now_iso()
    sstlib.add_history(sst, role, "decided", "eligibility.coordinator_decision", None, d, body.get("reason"), actor_label)
    save(rid, sst, role, f"decision {d}")
    return el["coordinator_decision"]


@app.get("/referrals/{rid}/reply", response_class=PlainTextResponse)
def reply(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider")
    return gaps_mod.draft_reply(load(rid))


@app.post("/referrals/{rid}/analyze")
def do_analyze(rid: str, body: dict = Body(...), x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider")
    sst = load(rid)
    try:
        row = analyze_mod.analyze(sst, rid, body.get("mode", "briefing"), body.get("question"), role, with_pages=body.get("with_pages", False))
    except ValueError as e:
        raise HTTPException(400, str(e))
    save(rid, sst, role, f"analyze {row['mode']}")
    return row


@app.get("/referrals/{rid}/analyses")
def analyses(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider")
    return load(rid)["analyses"]


@app.post("/referrals/{rid}/conversations")
def add_conversation(rid: str, body: dict = Body(...), x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid)
    try:
        row = conv_mod.add_conversation(sst, rid, body, role)
    except PermissionError as e:
        raise HTTPException(400, str(e))
    save(rid, sst, role, f"conversation {row['id']}")
    return row


@app.get("/referrals/{rid}/conversations")
def list_conversations(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    return load(rid)["conversations"]


@app.post("/referrals/{rid}/conversations/{cid}/items/{n}")
def decide_item(rid: str, cid: str, n: int, body: dict = Body(...), x_role: str | None = Header(None), actor_label: str | None = Query(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    sst = load(rid)
    try:
        item = conv_mod.decide_item(sst, rid, cid, n, body.get("status"), role, actor_label)
    except (KeyError, IndexError):
        raise HTTPException(404, "conversation or item not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if item["status"] == "accepted" and sst["eligibility"]["rules"]:
        gaps_mod.run_all(sst, sst["eligibility"]["provider_id"] or "ihh")
    save(rid, sst, role, f"{cid} item {n} {item['status']}")
    return {"item": item, "readiness": sst["eligibility"]["scores"]["readiness"]}


@app.get("/referrals/{rid}/pages/{n}")
def page_image(rid: str, n: int, x_role: str | None = Header(None), role: str | None = Query(None)):
    r = role_of(x_role or role); need(r, "provider", "hospital")
    p = ingest.page_path(rid, n)
    if not p.exists():
        raise HTTPException(404, "page not rendered")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/referrals/{rid}/history")
def history(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    return load(rid)["history"]


@app.get("/referrals/{rid}/runs")
def runs(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider")
    return store.list_runs(rid)


@app.get("/referrals/{rid}/forms/ihh_referral_form")
def form_json(rid: str, x_role: str | None = Header(None)):
    role = role_of(x_role); need(role, "provider", "hospital")
    return forms.render(load(rid))


@app.get("/referrals/{rid}/forms/ihh_referral_form.html", response_class=HTMLResponse)
def form_html(rid: str, x_role: str | None = Header(None), role: str | None = Query(None)):
    r = role_of(x_role or role); need(r, "provider", "hospital")
    return forms.render_html(load(rid))


if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
