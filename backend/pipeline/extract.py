"""Stage B: fill the SST section by section from the page images, with evidence. Runs sections in parallel;
the packet images are a shared cached prefix. Never overwrites a confirmed or entered value."""
from __future__ import annotations
import concurrent.futures as cf
from .. import sstlib, scrub
from . import claude_client as cc

RULES_TEXT = """You read faxed hospital referral packets for a recuperative care program and fill one section of a structured record.

Rules that do not bend:
1. Only record what a page shows. For every value with status "found", give at least one evidence item with source "packet", the page number from the [Page N of M] label, the doc_type, and a short verbatim quote. No quote, no value.
2. If the packet does not contain it, set status "not_in_packet" and value null. Never assume normal.
3. A black box is a privacy redaction: status "redacted", value null. Do not report it as missing and do not guess what was under it.
4. If two pages disagree, set status "conflict", put the value you judge most recent in value, and list the other reading under conflicts with its page and quote.
5. Rules that need a date or a count of hours cannot be checked when dates are blacked out: status "cant_tell".
6. Never output a name, date of birth, medical record number, address, phone number, or any date with a month and day. Ages in years are fine.
7. Read the chart, not only the referral form. The form is one person's summary and can be wrong; a note deeper in the chart wins.
8. Leave updated_by as "extractor", updated_at null, confidence high, medium or low, note null unless something needs explaining.
9. Condition flags: set true only when the condition is positively documented. If the chart does not mention it, status "not_in_packet", value null.
10. Lists and rows (vitals, labs, wounds, problems): include every item you can read, newest first, each with its page."""


def _instruction(section: str) -> str:
    return (f"Fill the `{section}` section of the record from the pages above. Fields and meanings:\n"
            f"{sstlib.section_field_guide(section)}\n\nReturn the whole `{section}` object with every field present.")


def _postprocess(section_obj: dict, section: str):
    """Fill bookkeeping keys, then evidence gate and identifier scrub on everything the model returned."""
    sstlib.fill_field_defaults(section_obj, "extractor")
    ann = sstlib.annotations()

    def walk(node, path):
        if isinstance(node, dict) and "status" in node and "value" in node and "evidence" in node:
            for ev in node["evidence"]:
                ev.setdefault("source", "packet")
                ev["quote"], flags = scrub.scrub(ev.get("quote") or "")
                if ev.get("page") is None and ev["source"] == "packet":
                    ev["page"] = 0
            if node["status"] == "found" and not node["evidence"]:
                node["status"] = "cant_tell"
                node["note"] = (node.get("note") or "") + " Extractor gave no evidence; downgraded."
            if isinstance(node["value"], str):
                node["value"], flags = scrub.scrub(node["value"])
            if node.get("updated_by") is None:
                node["updated_by"] = "extractor"
            node["updated_at"] = sstlib.now_iso()
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, list):
            for v in node:
                walk(v, path)

    walk(section_obj, [section])


def _merge(sst: dict, section: str, new_obj: dict, only_unknown: bool):
    """Replace the section, but keep confirmed/entered values (adding a conflict if the packet now disagrees)."""
    ann = sstlib.annotations()
    old = sst[section]
    for path, a in ann.items():
        if a["section"] != section:
            continue
        rel = path.split(".")[1:]
        o = old
        n = new_obj
        for p in rel:
            o = o.get(p) if isinstance(o, dict) else None
            n = n.get(p) if isinstance(n, dict) else None
        if o is None or n is None:
            continue
        if o["status"] in ("confirmed", "entered"):
            if n["status"] == "found" and n["value"] != o["value"]:
                o["conflicts"] = (o.get("conflicts") or []) + [{"page": (n["evidence"][0].get("page") if n["evidence"] else None), "doc_type": (n["evidence"][0].get("doc_type") if n["evidence"] else None), "quote": (n["evidence"][0].get("quote") if n["evidence"] else ""), "value": str(n["value"])}]
            _copy_into(n, o)
        elif only_unknown and o["status"] in sstlib.KNOWN:
            _copy_into(n, o)
    sst[section] = new_obj


def _copy_into(dst: dict, src: dict):
    for k in list(dst.keys()):
        dst[k] = src.get(k)


def extract(sst: dict, rid: str, sections: list[str] | None = None, pages: list[int] | None = None, workers: int = 3) -> dict:
    sections = sections or sstlib.SECTIONS
    total = sst["meta"]["packet"]["page_count"] or 0
    pages = pages or list(range(1, total + 1))
    report: dict[str, str] = {}
    if cc.mock_mode():
        ex = sstlib.load_example()
        for s in sections:
            _merge(sst, s, ex[s], only_unknown=bool(pages and len(pages) < total))
            report[s] = "mock (fictional example)"
        sst["meta"]["fictional"] = True
        sstlib.add_history(sst, "extractor", "extracted", "*", None, None, "MOCK extraction: fictional example data, no API key")
        return report

    system = cc.system_blocks(RULES_TEXT, "IHH admission criteria, for context on what matters:\n\n" + sstlib.load_criteria_text())
    images = cc.page_blocks(rid, pages, total)

    def one(section):
        schema = sstlib.section_schema(section)
        content = images + [{"type": "text", "text": _instruction(section)}]
        def call():
            try:
                return cc.structured(system, content, schema, max_tokens=16000, effort="high")
            except cc.ClaudeError as e:
                retry = content + [{"type": "text", "text": f"The previous attempt failed ({e}). Return valid JSON for the schema; set unclear fields to status cant_tell."}]
                return cc.structured(system, retry, schema, max_tokens=16000, effort="high")
        data = cc.logged(rid, f"extract:{section}", call, f"{len(pages)} pages")
        return section, data[section]

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(one, s): s for s in sections}
        for f in cf.as_completed(futures):
            section = futures[f]
            try:
                _, obj = f.result()
                _postprocess(obj, section)
                _merge(sst, section, obj, only_unknown=len(pages) < total)
                report[section] = "ok"
            except Exception as e:  # noqa: BLE001
                report[section] = f"failed: {type(e).__name__}: {str(e)[:160]}"
    sstlib.add_history(sst, "extractor", "extracted", "*", None, None, f"sections: {', '.join(k for k, v in report.items() if v == 'ok')}; pages {pages[0]}-{pages[-1]}")
    errors = sstlib.validate(sst)
    if errors:
        report["validation"] = "; ".join(errors[:3])
    return report
