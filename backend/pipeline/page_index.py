"""Stage A: what is on each page. Cheap, batched, gives the UI a table of contents."""
from __future__ import annotations
from .. import sstlib
from . import claude_client as cc

SYSTEM = """You index pages of a faxed hospital referral packet for a recuperative care program.
For every page given, say what kind of document it is, its orientation, how legible it is, whether it looks like it is from an earlier hospital stay, and whether it duplicates another page in this batch.
doc_type values: fax_cover, referral_form, face_sheet, auth_form, h_and_p, progress_note, consult, pt_ot_eval, nursing_note, social_work_note, mar, medication_list, labs, imaging, ekg, vitals, wound_note, other, blank.
Printed page numbers restart in every sub-document ("1 of 3", "2/4"); the page number you return is the [Page N of M] label, never the printed one.
A mostly blank page that carries one line (an isolation status, a code status, a disposition line) is that document type with legibility partial, not blank.
Never transcribe names, dates of birth, record numbers, addresses or phone numbers. Black boxes are privacy redactions, not missing content."""


def index_pages(sst: dict, rid: str, total: int, batch: int = 10) -> list[dict]:
    rows: list[dict] = []
    if cc.mock_mode():
        rows = [{"page": n, "doc_type": None, "orientation": None, "legibility": None, "from_earlier_stay": None, "duplicate_of_page": None} for n in range(1, total + 1)]
    else:
        schema = sstlib.defs_schema("PageRow", wrap_key="pages", as_array=True)
        for start in range(1, total + 1, batch):
            pages = list(range(start, min(start + batch, total + 1)))
            content = cc.page_blocks(rid, pages, total) + [{"type": "text", "text": f"Index pages {pages[0]} to {pages[-1]}. Return one row per page, in order, with the exact page numbers shown in the labels."}]
            data = cc.logged(rid, "page_index", lambda: cc.structured(cc.system_blocks(SYSTEM), content, schema, model=cc.MODEL_FAST, max_tokens=4000, effort="medium", stream=False), f"pages {pages[0]}-{pages[-1]}")
            got = {r["page"]: r for r in data["pages"]}
            for n in pages:
                rows.append(got.get(n, {"page": n, "doc_type": None, "orientation": None, "legibility": None, "from_earlier_stay": None, "duplicate_of_page": None}))
    sst["meta"]["packet"]["page_count"] = total
    sst["meta"]["packet"]["pages"] = rows
    sstlib.add_history(sst, "system", "extracted", "meta.packet.pages", None, str(total), "page index")
    return rows
