"""Render a packet PDF to page images. Pages live under data/pages/<referral_id>/, never in git."""
from __future__ import annotations
import pathlib
from .. import sstlib

PAGES_DIR = sstlib.ROOT / "data" / "pages"


def page_path(rid: str, n: int) -> pathlib.Path:
    return PAGES_DIR / rid / f"p{n:03d}.jpg"


def render(rid: str, pdf_path: str, scale: float = 1.9, quality: int = 80) -> int:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_path)
    (PAGES_DIR / rid).mkdir(parents=True, exist_ok=True)
    for i in range(len(pdf)):
        out = page_path(rid, i + 1)
        if out.exists():
            continue
        img = pdf[i].render(scale=scale).to_pil().convert("RGB")
        w, h = img.size
        if max(w, h) > 1568:  # Claude downsizes beyond this anyway
            r = 1568 / max(w, h)
            img = img.resize((int(w * r), int(h * r)))
        img.save(out, "JPEG", quality=quality, optimize=True)
    return len(pdf)


def page_count(rid: str) -> int:
    d = PAGES_DIR / rid
    return len(list(d.glob("p*.jpg"))) if d.exists() else 0


def load_page_bytes(rid: str, n: int) -> bytes:
    return page_path(rid, n).read_bytes()
