#!/usr/bin/env python3
"""Download the 11 de-identified event packets (R01.pdf to R11.pdf) into data/packets/.

The packets are never in git. The event's Google Drive folder is public; this uses gdown to pull it.
Downloads land in a fresh temporary folder first, then only R??.pdf files are moved into place.

Usage: python3 scripts/fetch_packets.py [--force]
"""
from __future__ import annotations
import argparse, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEST = ROOT / "data" / "packets"
FOLDER = "https://drive.google.com/drive/folders/1ISmUtoNCTW3NJNqCQJHJzb6C319RgrXM"
EXPECTED = [f"R{i:02d}.pdf" for i in range(1, 12)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download even if all 11 are present")
    args = ap.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    have = [n for n in EXPECTED if (DEST / n).exists()]
    if len(have) == len(EXPECTED) and not args.force:
        print(f"all {len(EXPECTED)} packets already in {DEST}")
        return 0
    try:
        import gdown  # noqa: F401
    except ImportError:
        print("gdown is not installed. Run: pip install gdown   (or pip install -r backend/requirements.txt)")
        return 2
    with tempfile.TemporaryDirectory(prefix="packets-") as tmp:
        cmd = [sys.executable, "-m", "gdown", "--folder", FOLDER, "-O", tmp]
        print("downloading from the event's Drive folder ...")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-2000:])
            print("download failed. Open the folder in a browser, choose Download all, unzip, and move R01.pdf to R11.pdf into data/packets/")
            return 1
        moved = 0
        for f in sorted(pathlib.Path(tmp).rglob("R*.pdf")):
            if f.name in EXPECTED:
                shutil.move(str(f), DEST / f.name)
                moved += 1
    have = [n for n in EXPECTED if (DEST / n).exists()]
    missing = [n for n in EXPECTED if n not in have]
    print(f"moved {moved}; {len(have)} of {len(EXPECTED)} packets now in {DEST}" + (f"; missing: {', '.join(missing)}" if missing else ""))
    try:
        import pypdfium2 as pdfium
        for n in have:
            print(f"  {n}: {len(pdfium.PdfDocument(str(DEST / n)))} pages")
    except Exception as e:  # pragma: no cover
        print(f"(page counts skipped: {e})")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
