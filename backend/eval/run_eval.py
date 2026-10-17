#!/usr/bin/env python3
"""Blind eval: run packets in data/packets through the pipeline, then compare the recommendation with
IHH's real decisions. eval/decisions.json is read ONLY here, after all extraction is done, never in a prompt.

Usage:
  python3 backend/eval/run_eval.py                       # every packet, live (needs the API key)
  python3 backend/eval/run_eval.py --packets R01,R05,R10 # a subset
  python3 backend/eval/run_eval.py --resume              # keep finished packets from data/runs/eval/results.json
  python3 backend/eval/run_eval.py --readme              # also write the tables into README.md between the eval markers
  python3 backend/eval/run_eval.py --mock                # plumbing check on fictional data; clearly labelled, never written to the README

Writes data/runs/eval/<packet>.sst.json (the full record per packet), data/runs/eval/results.json and
data/runs/eval.md, and prints the tables."""
from __future__ import annotations
import argparse, json, os, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "runs" / "eval"
README = ROOT / "README.md"
MARK_START, MARK_END = "<!-- eval:start -->", "<!-- eval:end -->"
REC_TO_DECISION = {"likely_eligible": "approve", "likely_ineligible": "deny"}


def quality(sst: dict) -> dict:
    """How much the extractor filled in, and whether every found value carries a page and a quote."""
    from backend import sstlib
    counts = {"found": 0, "found_with_evidence": 0, "not_in_packet": 0, "cant_tell": 0, "redacted": 0, "conflict": 0, "pending": 0, "other": 0}
    for path in sstlib.field_paths():
        f = sstlib.field(sst, path)
        if f is None:
            continue
        st = f["status"]
        if st == "found":  # entered and confirmed values come from people, not pages, and are counted under other
            counts["found"] += 1
            if any(ev.get("source") == "packet" and (ev.get("page") or 0) >= 1 and (ev.get("quote") or "").strip() for ev in f.get("evidence") or []):
                counts["found_with_evidence"] += 1
        elif st in counts:
            counts[st] += 1
        else:
            counts["other"] += 1
    return counts


def run_packet(pdf: pathlib.Path, provider: str, workers: int) -> dict:
    from backend import sstlib, store
    from backend.pipeline import ingest, page_index, extract, gaps
    t = time.time()
    sst = sstlib.new_sst(f"EVAL-{pdf.stem}")
    rid = sst["meta"]["referral_id"]
    sst["meta"]["packet"]["source_file"] = str(pdf)
    sst["meta"]["parties"]["providers"] = [{"provider_id": provider, "name": "Illumination Health + Home", "status": "reviewing", "updated_at": sstlib.now_iso()}]
    if store.get_referral(rid) is None:
        store.create_referral(sst, str(pdf), f"eval {pdf.stem}")
    row = {"packet": pdf.stem, "pages": None, "rec": None, "hard_stops": [], "cant_tell": None, "readiness": None, "secs": None,
           "error": None, "quality": None, "cache_read_tokens": None, "input_tokens": None, "output_tokens": None}
    try:
        total = ingest.render(rid, str(pdf))
        row["pages"] = total
        page_index.index_pages(sst, rid, total)
        report = extract.extract(sst, rid, workers=workers)
        el = gaps.run_all(sst, provider)
        store.save_sst(rid, sst, "system", "eval run")
        (OUT / f"{pdf.stem}.sst.json").write_text(json.dumps(sst, indent=1, ensure_ascii=False))
        bad = {k: v for k, v in report.items() if v != "ok" and not str(v).startswith("mock")}
        row.update({"rec": el["recommendation"], "hard_stops": el["hard_stops"],
                    "cant_tell": len(el["scores"]["components"]["rule_coverage"]["cant_tell_rule_ids"]),
                    "readiness": el["scores"]["readiness"], "quality": quality(sst),
                    "error": "; ".join(f"{k}: {v}" for k, v in bad.items()) or None})
        runs = store.list_runs(rid)
        for key in ("cache_read_tokens", "input_tokens", "output_tokens"):
            row[key] = sum((r.get(key) or 0) for r in runs)
    except Exception as e:  # keep going with the other packets; the table reports the error
        row["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    row["secs"] = round(time.time() - t)
    return row


def tables(results: list[dict], mock: bool) -> str:
    decisions = {d["packet_id"]: d["decision"] for d in json.loads((ROOT / "eval" / "decisions.json").read_text())["packets"]}
    agree = partial = 0
    rows = []
    for r in results:
        ihh = decisions.get(r["packet"], "?")
        ours = REC_TO_DECISION.get(r["rec"], "request_information")
        if r["rec"] is None:
            match = "error"
        elif ours == ihh:
            match = "yes"; agree += 1
        elif ours == "request_information":
            match = "partial"; partial += 1
        else:
            match = "NO"
        rows.append(f"| {r['packet']} | {r['pages'] or ''} | {r['rec'] or 'error'} | {', '.join(r['hard_stops'])} | "
                    f"{'' if r['cant_tell'] is None else r['cant_tell']} | {'' if r['readiness'] is None else r['readiness']} | {ihh} | {match} | {r['error'] or ''} |")
    t1 = ["| Packet | Pages | Our recommendation | Hard stops | Rules can't tell | Readiness | IHH decided | Agree | Errors |",
          "|---|---|---|---|---|---|---|---|---|"] + rows
    q_rows = []
    for r in results:
        q = r.get("quality")
        if not q:
            q_rows.append(f"| {r['packet']} | | | | | | | {r['secs'] or ''} | |"); continue
        q_rows.append(f"| {r['packet']} | {q['found']} | {q['found_with_evidence']} | {q['not_in_packet']} | {q['cant_tell']} | {q['redacted']} | {q['conflict']} | {r['secs']} | {r['cache_read_tokens'] or 0:,} |")
    t2 = ["| Packet | Fields found | ...with page and quote | Not in packet | Can't tell | Redacted | Conflict | Seconds | Cached input tokens |",
          "|---|---|---|---|---|---|---|---|---|"] + q_rows
    n = len(results)
    summary = (f"{agree} of {n} recommendations match IHH's decision outright; {partial} are 'partial' (we asked for more information "
               f"where IHH decided); {n - agree - partial} disagree or errored. The score is reported as is; nothing was tuned to a packet after seeing its decision.")
    head = "**MOCK RUN on fictional data. Nothing here is a real result.**\n\n" if mock else ""
    return head + "\n".join(t1) + "\n\n" + summary + "\n\n**Extraction quality** (234 schema fields; a found value without a page and quote is a quality-control failure)\n\n" + "\n".join(t2) + "\n"


def write_readme(body: str) -> bool:
    text = README.read_text()
    if MARK_START not in text or MARK_END not in text:
        print(f"README.md has no {MARK_START} ... {MARK_END} markers; not written")
        return False
    a, rest = text.split(MARK_START, 1)
    _, b = rest.split(MARK_END, 1)
    README.write_text(a + MARK_START + "\n" + body.rstrip() + "\n" + MARK_END + b)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packets", default=None, help="comma-separated, e.g. R01,R05,R10")
    ap.add_argument("--mock", action="store_true", help="fictional data, no API calls; labelled MOCK and never written to the README")
    ap.add_argument("--provider", default="ihh")
    ap.add_argument("--workers", type=int, default=3, help="parallel section calls per packet")
    ap.add_argument("--resume", action="store_true", help="keep finished packets from a previous results.json")
    ap.add_argument("--readme", action="store_true", help="write the tables into README.md between the eval markers")
    args = ap.parse_args()
    if args.mock:
        os.environ["SST_MOCK"] = "1"
    from backend import store
    from backend.pipeline import claude_client as cc
    if not args.mock and cc.mock_mode():
        print("No API key: set one of " + " or ".join(cc.API_KEY_VARS) + " (or put it in .env) and run again.\n"
              "Refusing to run the blind eval on fictional data. Use --mock if you only want to check the plumbing.")
        sys.exit(2)
    store.init_db()
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = args.packets.split(",") if args.packets else None
    pdfs = sorted(p for p in (ROOT / "data" / "packets").glob("R*.pdf") if not wanted or p.stem in wanted)
    if not pdfs:
        print("no packets in data/packets/ (run python3 scripts/fetch_packets.py)"); sys.exit(1)
    done: dict[str, dict] = {}
    results_path = OUT / "results.json"
    if args.resume and results_path.exists():
        prev = json.loads(results_path.read_text())
        if prev.get("mock") == args.mock:
            done = {r["packet"]: r for r in prev["results"] if r.get("rec")}
    results = []
    for pdf in pdfs:
        if pdf.stem in done:
            results.append(done[pdf.stem]); print(f"{pdf.stem}: kept from previous run ({done[pdf.stem]['rec']})", flush=True); continue
        print(f"{pdf.stem}: running ...", flush=True)
        r = run_packet(pdf, args.provider, args.workers)
        results.append(r)
        print(f"{pdf.stem}: {r['rec'] or 'error'} in {r['secs']}s" + (f"  [{r['error']}]" if r['error'] else ""), flush=True)
        results_path.write_text(json.dumps({"mock": args.mock, "model": cc.MODEL_MAIN, "results": results}, indent=1))

    # Only now: IHH's decisions, for scoring.
    body = tables(results, args.mock)
    out = "# Blind eval\n\nGenerated by `backend/eval/run_eval.py` with " + ("MOCK data" if args.mock else f"`{cc.MODEL_MAIN}`") + ". Decisions were loaded only after every extraction finished.\n\n" + body
    (ROOT / "data" / "runs" / "eval.md").write_text(out)
    print("\n" + out)
    if args.readme:
        if args.mock:
            print("--readme ignored for a mock run")
        elif write_readme(body):
            print("README.md updated between the eval markers")


if __name__ == "__main__":
    main()
