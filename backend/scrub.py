"""Identifier scrub for transcripts and model output. Conservative: it flags, a person decides."""
from __future__ import annotations
import re

PATTERNS = [
    ("date", re.compile(r"\b(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:19|20)?\d{2}\b")),
    ("date", re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")),
    ("date", re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}\b", re.I)),
    ("phone", re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("mrn", re.compile(r"\b(?:MRN|MR#|medical record (?:number|no\.?)|acct(?:ount)?\s*#?|account number)\s*[:#]?\s*[A-Z]?\d{5,}\b", re.I)),
    ("dob", re.compile(r"\b(?:DOB|date of birth|born on)\s*[:#]?\s*[^\n,.;]{4,20}", re.I)),
    ("name", re.compile(r"\b(?:Mr|Mrs|Ms|Mx|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")),
    ("name", re.compile(r"\b(?:patient(?:'s)? name|name)\s*(?:is|:)\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+")),
]
ALLOW_TIMES = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")


def scrub(text: str) -> tuple[str, list[dict]]:
    """Returns (clean_text, flags). Each flag: kind, original, position."""
    flags: list[dict] = []
    clean = text or ""
    for kind, rx in PATTERNS:
        def repl(m, kind=kind):
            s = m.group(0)
            if ALLOW_TIMES.match(s):
                return s
            flags.append({"kind": kind, "original": s, "start": m.start()})
            return "[redacted " + kind + "]"
        clean = rx.sub(repl, clean)
    return clean, flags


def result_label(flags: list[dict]) -> str:
    return "flagged" if flags else "passed"
