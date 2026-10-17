# Referral SST: one shared record for the hospital, the patient and the provider

> For the referral coordinator, the hospital case manager and the patient leaving hospital with nowhere
> to recover: turns a faxed referral packet into one structured, cited record, checks it against the
> provider's rules, and lists what the hospital still needs to send while the patient is still there.

**Team:** [team name]
**Members:** [names]
**Challenge:** 1 Referral intake
**Built at:** Orange County | Claude Impact Lab, October 17, 2026, with Illumination Health + Home

## The problem

A hospital faxes 15 to 35 pages to each recuperative care provider, and a coordinator reads every page
by hand against six pages of rules, with a two-hour promise to answer. The fact that decides the case is
often deep in the chart and contradicts the form on page 2. A denial for a missing therapy note costs a
day the patient does not have, because the hospital may already have discharged them.

## What we built

A **Single Source of Truth (SST)** for each referral: one JSON record built from the packet by Claude,
with a page number and quote for every fact and "unknown" wherever the packet is silent. IHH's admission
criteria are loaded as a requirements profile; the SST is checked rule by rule, the gaps become a list
of what to ask the hospital for, and a draft reply is written in IHH's format. The hospital, the patient
and the provider see the same record through three views.

**Demo video:** [link]

Start here:

- [`docs/01-idea-evaluation.md`](docs/01-idea-evaluation.md): why this idea, the risks, the cut lines
- [`docs/02-sst-template.md`](docs/02-sst-template.md): what the SST is and the rules it follows
- [`docs/02b-sst-field-reference.md`](docs/02b-sst-field-reference.md): every field, who supplies it, which IHH rules it feeds
- [`docs/03-backend-prd.md`](docs/03-backend-prd.md): the service, pipeline, API, scoring, analyze mode and conversations
- [`docs/04-build-plan.md`](docs/04-build-plan.md): lanes, work packages, timeline, cut lines for the day
- [`sst/`](sst/): the schema, a blank record, a fictional example, and the generator
- [`providers/ihh/`](providers/ihh/): IHH's criteria and the rule-to-field map

## How Claude is used

- We built the project with Claude Code from plain descriptions, starting from the event template.
- Inside the tool, Claude indexes every page of the packet, fills the SST section by section with page
  citations using structured outputs, judges the rules that need judgment, and writes the patient's
  plain-language summary. Code decides the mechanical rules. A person confirms every value a decision
  relies on, and a coordinator records the decision.

## Try it

```bash
pip install -r backend/requirements.txt
python3 scripts/fetch_packets.py    # pulls R01.pdf to R11.pdf from the event's Drive folder into data/packets/ (never committed)
export ANTHROPIC_API_KEY=...        # or SST_API_KEY, or put either in .env, which git ignores. Without a key the app runs in MOCK mode on fictional data.
uvicorn backend.app:app --reload    # then open http://127.0.0.1:8000
```

`SST_API_KEY` exists because hosted Claude Code sessions drop a variable named `ANTHROPIC_API_KEY` from the
container; add the key to the environment under that second name and the code reads it.

In the page: pick a role, create a referral from the fictional example or upload a packet, press **Run all**.
Provider sees the score, readout, gaps, draft reply, Analyze and conversations. Hospital sees the gaps and the
electronic referral form. Patient sees the plain-language status once staff have reviewed it.

```bash
python3 -m pytest -q backend/tests                          # 9 tests; mock mode plus the live code path with a stand-in for Claude on the real R01
python3 backend/eval/run_eval.py --packets R01,R05,R10      # live run on the three hand-reviewed packets; compare with eval/hand-review/
python3 backend/eval/run_eval.py --readme                   # full blind eval, writes the tables below into this README (needs the key; refuses to run on mock data)
python3 backend/eval/run_eval.py --mock                     # plumbing check only, clearly labelled MOCK
python3 sst/build.py                                        # regenerates the schema, blank record, IHH profile and field reference
```

Each eval run saves the full record per packet under `data/runs/eval/` (ignored by git), so a wrong value can be
traced to its page and quote. `--resume` keeps finished packets if a run is interrupted.

Python 3.13 and an Anthropic API key. No keys in the repo. Launch notes for relayhealth.dev are in `deploy/README.md`.

## What is real and what is mocked

| Part | Status |
|---|---|
| SST schema, blank record, IHH rule map, field reference (234 fields, 145 rules) | working |
| Packet ingest and page index (Stage A) | ran live on all 11 packets (`claude-sonnet-5-5`, one call per 10 pages) |
| Section extraction with evidence (Stage B) | ran live on all 11 packets (`claude-opus-5-5`, one call per section, 20 per packet, shared cached page prefix); results in the tables below |
| Rule evaluation, gaps, draft reply (Stage C) | working, deterministic, unit-tested on the fixture |
| Readiness score (Stage E) | working, deterministic, unit-tested |
| Analyze mode (Stage F) | working code with citation validator; mock answer without a key |
| Patient summary (Stage D) | working code; mock English/Spanish text without a key |
| Conversations (Stage G): consent gate, scrub, summary, proposed updates, accept/reject | working; summary and proposals are mock without a key |
| Electronic referral form generated from the record | working |
| Coordinator, hospital and patient views (single-page frontend) | working against the API |
| Role-based access | header-based role switcher, not a login. Backend filters every read and write |
| Sending anything to anyone | not built, by design |
| Audio file transcription | not built; browser dictation (Chrome) or pasted text only |

## Where it gets things wrong

**Status at the 3:00 PM freeze: the live blind eval ran on all 11 packets.** 3 of 11 recommendations match
IHH's decision outright, 5 are partial (we asked for more information where IHH decided, four of those
approvals) and 3 disagree. The three disagreements are all packets IHH approved that the rules rejected on
a hard stop: R03 on an ejection fraction of 21.5% read from the echo report (p17); R06 and R07 on cognitive
impairment, a traumatic brain injury with encephalopathy (R06 p21, p28) and an acute metabolic
encephalopathy with fluctuating mental status (R07 p19, p22, p27), plus BiPAP for probable sleep apnea that
the patient refuses (R07 p22, p29). Those conditions are on the pages; the exclusion rules as written are
stricter than IHH's own judgment on those packets. Nothing was tuned after seeing a decision.

The quality table shows every found value carries a page and a quote (the evidence gate downgrades any that
do not), caching works (one to three million cached input tokens per packet; the page images are a shared
prefix for the 20 section calls), and most fields in most packets are "not in packet", which is honest:
the packets are thin. The first live attempt failed before reading a page because the per-section output
schemas were too large for the API's constrained-output grammar, and the constrained calls also missed the
cache (each schema becomes its own prefix). Section calls now ask for JSON in the prompt and validate it
locally against the same schema.

Misses found by comparing R01, R05 and R10 with the hand reviews, before the full run:

- R01 (p2, p7, p8) and R05 (p1): bathing, dressing, toileting, eating, continence and "self-administers
  medications" were filled from referral-form ticks with no chart support, which made ADL and medication
  rules "met". Fixed in the prompt: a form tick alone is "can't tell", cited, with the form named in the note.
- R05 (p9): a danger-to-others hold and threats toward a family member before admission were read as
  aggression toward staff, a hard stop. Fixed in the field description and prompt: that is for a person to
  judge; R05 moved from "likely ineligible" to "request information".
- R10 (p4, p10): orientation was documented ("oriented to self only" versus "person and place") but the
  rule checker ignored every field in conflict status, so it reported orientation as not documented. Fixed
  in the evaluator: a conflict carries the extractor's best reading.
- R01 (p4): "too ill to recover outside" was taken from the authorization form's own eligibility wording.
  Fixed in the prompt and field description. The housing basis rule now reads "can't tell" on R01 where the
  hand review expected it met from the homelessness diagnosis (p10), and on R10 the extractor became more
  cautious about "no supports" (p4, p6, p17) than the hand review. Both are open.
- Page index: R01's rotated MAR pages (p11-30) are grouped by rotation differently from the hand review, and
  the vitals grid (p24-25) and the code legends (p28-30) are labelled MAR. The section calls read the pages
  anyway.

Hand reviews of R01, R05 and R10, written before any model saw them, are in [`eval/hand-review/`](eval/hand-review/).
They list what the rule checker should find, where the form and the chart disagree, and the traps for an
extractor (sideways pages, earlier-stay documents, ticked boxes contradicted deeper in the chart). The
extraction prompt was updated from those traps. The first live run should be compared against them.

The tables below are written by `python3 backend/eval/run_eval.py --readme`.

<!-- eval:start -->
| Packet | Pages | Our recommendation | Hard stops | Rules can't tell | Readiness | IHH decided | Agree | Errors |
|---|---|---|---|---|---|---|---|---|
| R01 | 34 | likely_ineligible | NEU-X4, FORM-X1 | 17 | 60 | deny | yes |  |
| R02 | 22 | request_information |  | 20 | 72 | approve | partial |  |
| R03 | 23 | likely_ineligible | CARD-5 | 18 | 63 | approve | NO |  |
| R04 | 18 | request_information |  | 25 | 55 | approve | partial |  |
| R05 | 14 | request_information |  | 23 | 60 | deny | partial |  |
| R06 | 30 | likely_ineligible | NEU-X4, FORM-X1 | 23 | 58 | approve | NO |  |
| R07 | 30 | likely_ineligible | RSP-X5, NEU-X4, FORM-X1 | 19 | 57 | approve | NO |  |
| R08 | 29 | request_information |  | 18 | 57 | approve | partial |  |
| R09 | 30 | likely_ineligible | MSK-X1, NEU-X3, NEU-X4, FORM-X1, FORM-X2 | 13 | 47 | deny | yes |  |
| R10 | 21 | likely_ineligible | NEU-X4, FORM-X1 | 13 | 62 | deny | yes |  |
| R11 | 31 | request_information |  | 21 | 63 | approve | partial |  |

3 of 11 recommendations match IHH's decision outright; 5 are 'partial' (we asked for more information where IHH decided); 3 disagree or errored. The score is reported as is; nothing was tuned to a packet after seeing its decision.

**Extraction quality** (234 schema fields; a found value without a page and quote is a quality-control failure)

| Packet | Fields found | ...with page and quote | Not in packet | Can't tell | Redacted | Conflict | Seconds | Cached input tokens |
|---|---|---|---|---|---|---|---|---|
| R01 | 66 | 66 | 133 | 22 | 10 | 3 | 243 | 3,045,455 |
| R02 | 90 | 90 | 102 | 23 | 15 | 4 | 211 | 992,223 |
| R03 | 61 | 61 | 142 | 18 | 8 | 5 | 195 | 1,009,727 |
| R04 | 78 | 78 | 113 | 24 | 12 | 7 | 186 | 818,546 |
| R05 | 63 | 63 | 145 | 14 | 12 | 0 | 158 | 1,802,689 |
| R06 | 76 | 76 | 114 | 26 | 13 | 5 | 307 | 1,265,311 |
| R07 | 88 | 88 | 112 | 21 | 10 | 3 | 233 | 1,274,031 |
| R08 | 90 | 90 | 110 | 19 | 11 | 4 | 221 | 1,244,479 |
| R09 | 102 | 102 | 106 | 11 | 10 | 5 | 264 | 1,265,311 |
| R10 | 45 | 45 | 169 | 9 | 6 | 5 | 162 | 2,461,644 |
| R11 | 85 | 85 | 107 | 26 | 11 | 5 | 291 | 1,316,268 |
<!-- eval:end -->

Known limits: dates are blacked out in the event packets, so every rule that needs hours (24 h insulin,
72 h alcohol, COVID swab within 24 h) comes back "can't tell". The SST is a format we designed today.

## Next steps

- A second provider profile and a side-by-side readiness view for the hospital case manager.
- Mapping the SST to FHIR and Open Referral so it is not a one-off format.
- A pilot with IHH's coordinator on live faxes, with the coordinator confirming every field.

## Data and privacy

This project uses only data from the event repo and public sources. It contains no real patient or
client information. The de-identified packets stay on local disk and are never committed. It does not
connect to any Illumination Health + Home system and sends nothing to anyone.

## License

[MIT](LICENSE). The admission criteria and referral form under `providers/ihh/` are Illumination Health
+ Home's documents and remain theirs.
