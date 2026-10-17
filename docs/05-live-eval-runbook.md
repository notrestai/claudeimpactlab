# Live eval runbook

For whoever runs the first real model pass (a new cloud session or a laptop). Written at the 3:00 PM freeze
of October 17, 2026, when the pipeline had been exercised end to end only with a stand-in for Claude.

## Before you start

- The key must be in the environment as `SST_API_KEY` (or `ANTHROPIC_API_KEY` on a laptop). Hosted Claude
  Code sessions remove a variable named `ANTHROPIC_API_KEY`, so in the cloud use the second name.
- `python3 -c "import os; print(bool(os.environ.get('SST_API_KEY') or os.environ.get('ANTHROPIC_API_KEY')))"` must print `True`.
- `pip install -r backend/requirements.txt`, then `python3 scripts/fetch_packets.py` (11 PDFs into `data/packets/`).

## Steps

1. `python3 -m pytest -q backend/tests` — 9 tests pass without a key.
2. `python3 backend/eval/run_eval.py --packets R01,R05,R10` — about 20 model calls per packet (one page-index
   call per 10 pages on `claude-sonnet-5-5`, one call per SST section on `claude-opus-5-5`, cached packet prefix).
   Check `Cached input tokens` is non-zero in the quality table; if it is zero, caching is broken.
3. Open `data/runs/eval/R01.sst.json` and compare with `eval/hand-review/R01.md`. Same for R05 and R10. Look for:
   - a `found` value whose quote is not on the cited page (quality-control failure; tighten rule 1 or 15 in `RULES_TEXT`),
   - a value taken from the referral form when the chart says otherwise (rule 13),
   - a value taken from an earlier-stay page (rule 12),
   - `not_in_packet` where the hand review found it (the section field guide may be unclear; fix the
     description in `sst/build.py`, then `python3 sst/build.py`),
   - a rule result that disagrees with the hand review's expected status (that is `backend/pipeline/evaluate.py`, not the prompt).
4. Fix, re-run the same three packets, repeat once. Do not tune to a packet after seeing IHH's decision.
5. `python3 backend/eval/run_eval.py --readme` — all 11 packets, tables written into `README.md`.
6. Write the misses you saw into README "Where it gets things wrong", in plain words, and commit.

## If something fails

- `stopped: refusal` or `max_tokens` on a section: the retry already asks for `cant_tell`; if it persists, the
  section schema is too large for one call. Split `forms` (55 fields) first.
- A section fails validation: the error names the path; check `_postprocess` in `extract.py`.
- Rate limits: lower `--workers` to 1.
