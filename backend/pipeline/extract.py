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
10. Lists and rows (vitals, labs, wounds, problems): include every item you can read, newest first, each with its page.
11. Faxed pages are often sideways or upside down, and some are faint. Read them anyway; decide what a page is from its content, not its position in the packet.
12. Packets can contain pages from an earlier hospital stay or an older referral. Prefer the current stay. If the only evidence is from an earlier stay, say so in note and set confidence low.
13. A ticked box or a one-word answer on the referral form is one person's claim. When a chart page (H&P, progress note, PT/OT, nursing, MAR, labs) says otherwise, the chart wins: put the chart reading in value and the form's reading under conflicts.
14. Evidence doc_type uses this vocabulary: fax_cover, referral_form, face_sheet, auth_form, h_and_p, progress_note, consult, pt_ot_eval, nursing_note, social_work_note, mar, medication_list, labs, imaging, ekg, vitals, wound_note, other.
15. Quotes are copied as printed, under about 20 words, and skip over any identifier (write [redacted] in its place). A quote must come from the page you cite.

Known traps in faxed referral packets (seen in hand review):
16. "Alert", "awake and alert" or "AAO" with the count blacked out is NOT an orientation level. AOx values come only from an explicit count on the page; a blacked-out count is status redacted.
17. A face sheet "arrival mode: wheelchair" or "ambulance" says nothing about mobility. Ambulation distance, assist level and device come from PT/OT or nursing notes. The PT goal distance is not the achieved distance; record the achieved distance and keep phrases like "with cues for safety" in note.
18. Progress notes copy earlier text forward word for word. Two pages with the same sentence are one source, not two; prefer the page that adds new text.
19. Hypoglycemia-protocol PRN orders (dextrose, glucagon, glucose gel), a discontinued sliding scale, or inpatient DVT-prophylaxis injections do not make a patient diabetic or a self-injector. Condition flags need a diagnosis statement; "no pertinent PMH" argues against the flag.
20. On a MAR, repeated due times are schedule slots, not administrations; one drug under several order lines with different doses is one drug with a changing regimen, never summed. Brand and generic names are the same drug. "D/C medications" can mean discontinued orders, not discharge medications.
21. On forms a blank box is blank, not "No". A ticked "No" to "any wounds" together with a ticked "No" to "independent with wound care" is a form slip, not a wound-care need; note it under conflicts.
22. Ordered or referenced is not present: "EKG ordered", "recheck BMP", "see nursing note" with no such page means not_in_packet. Culture numbers contradicted by a later "no growth" or "likely contaminated" line are a conflict, not a positive.
23. Vitals printed as 24-hour min/max blocks are not discrete sets; count sets by distinct time stamps. In lab tables "--" is not drawn and a black cell is redacted; a one-digit heart rate next to a two-digit max is a scan artifact.
24. Printed page numbers restart in every sub-document ("1 of 3", "2/4"). Cite only the [Page N of M] label.
25. "5150", "DTO", "grave disability" and an APS referral are psychiatric hold, danger-to-others, consult-reason and protective-services terms. They are not a conservatorship. Record them as printed under mental_health and safety_legal so a person judges; do not map them to any eligibility conclusion.
26. Diagnoses printed on a face sheet or a MAR conditions list (dementia, psychosis, homelessness) are chart diagnoses even when the referral form leaves the matching box unticked: record them with their page and put the form's blank under conflicts.
27. Facility MAR grids: day-of-month columns run across the page, order text sits at the edge and is often cut off; an "X" cell means not scheduled, "NA" is not a value, a blacked-out dose is redacted, and a page that says "continued from previous page" belongs to the previous page's shift. Behavior-monitoring sheets look like MARs but hold codes and counts; meal percentages and hours of sleep are not vitals or doses. Orthostatic lying/sitting/standing pressures are not routine vitals sets.
28. When only the day of the month is visible, every "within 24 hours" or "72 hours" question is cant_tell; never infer timing from a column's position.
29. Chart wording such as "resident", "facility" or "absent from facility" means the sending setting may not be a hospital; record referral.setting as the pages state it, not as the form assumes.
30. In PT notes "(I)" means independent, "DNT" means did not test, five-digit codes like 97116 are billing codes, "300' x 2" is a distance, and "requires max encouragement" is not max assist. A POLST line about artificial nutrition is a care preference, not a feeding tube.
31. A near-empty page can be a truncated note or an empty report, and tick marks that cross a box edge belong to the box they are mostly inside. When you cannot tell which box is ticked, cant_tell."""


def _page_map(sst: dict) -> str:
    """The Stage A page index as one line, so the section call knows where the MAR, the PT eval and the old pages are."""
    parts = []
    for r in sst["meta"]["packet"].get("pages") or []:
        if not r.get("doc_type"):
            continue
        bits = [f"p{r['page']} {r['doc_type']}"]
        if r.get("orientation") and r["orientation"] != "upright":
            bits.append(r["orientation"])
        if r.get("legibility") in ("partial", "poor"):
            bits.append(f"{r['legibility']} legibility")
        if r.get("from_earlier_stay"):
            bits.append("earlier stay")
        if r.get("duplicate_of_page"):
            bits.append(f"duplicate of p{r['duplicate_of_page']}")
        parts.append(" ".join(bits))
    return ("Page index from a first pass (verify against the page itself): " + "; ".join(parts) + "\n\n") if parts else ""


def _instruction(section: str, sst: dict | None = None) -> str:
    return ((_page_map(sst) if sst else "") +
            f"Fill the `{section}` section of the record from the pages above. Fields and meanings:\n"
            f"{sstlib.section_field_guide(section)}\n\nReturn the whole `{section}` object with every field present.")


def _postprocess(section_obj: dict, section: str):
    """Evidence gate and identifier scrub on everything the model returned."""
    ann = sstlib.annotations()

    def walk(node, path):
        if isinstance(node, dict) and "status" in node and "value" in node and "evidence" in node:
            node.setdefault("conflicts", [])
            node.setdefault("confidence", None)
            node.setdefault("note", None)
            for ev in node["evidence"]:
                ev.setdefault("source", "packet")
                ev.setdefault("ref", None)
                ev.setdefault("timestamp", None)
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
        """Returns (section, section_object or None, error or None). Never raises: one failed section must not sink the others."""
        schema = sstlib.section_schema(section)
        content = images + [{"type": "text", "text": _instruction(section, sst)}]
        def call():
            try:
                return cc.structured(system, content, schema, max_tokens=16000, effort="high")
            except cc.ClaudeError as e:
                retry = content + [{"type": "text", "text": f"The previous attempt failed ({e}). Return valid JSON for the schema; set unclear fields to status cant_tell."}]
                return cc.structured(system, retry, schema, max_tokens=16000, effort="high")
        try:
            data = cc.logged(rid, f"extract:{section}", call, f"{len(pages)} pages")
            return section, data[section], None
        except Exception as e:
            return section, None, f"{type(e).__name__}: {str(e)[:160]}"

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(one, s) for s in sections]
        for f in cf.as_completed(futures):
            section, obj, err = f.result()
            if err:
                report[section] = f"failed: {err}"
                continue
            try:
                _postprocess(obj, section)
                _merge(sst, section, obj, only_unknown=len(pages) < total)
                report[section] = "ok"
            except Exception as e:
                report[section] = f"failed in merge: {type(e).__name__}: {str(e)[:160]}"
    sstlib.add_history(sst, "extractor", "extracted", "*", None, None, f"sections: {', '.join(k for k, v in report.items() if v == 'ok')}; pages {pages[0]}-{pages[-1]}")
    errors = sstlib.validate(sst)
    if errors:
        report["validation"] = "; ".join(errors[:3])
    return report
