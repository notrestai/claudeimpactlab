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
# 1. put the 11 packets in data/packets/ (download from the event's Drive folder; never commit them)
pip install -r backend/requirements.txt
export ANTHROPIC_API_KEY=...        # or put it in .env, which git ignores. Without a key the app runs in MOCK mode on fictional data.
uvicorn backend.app:app --reload    # then open http://127.0.0.1:8000
```

In the page: pick a role, create a referral from the fictional example or upload a packet, press **Run all**.
Provider sees the score, readout, gaps, draft reply, Analyze and conversations. Hospital sees the gaps and the
electronic referral form. Patient sees the plain-language status once staff have reviewed it.

```bash
python3 -m pytest -q backend/tests          # 7 tests, run in mock mode
python3 backend/eval/run_eval.py            # blind eval over every packet in data/packets (needs the API key)
python3 sst/build.py                        # regenerates the schema, blank record, IHH profile and field reference
```

Python 3.13 and an Anthropic API key. No keys in the repo. Launch notes for relayhealth.dev are in `deploy/README.md`.

## What is real and what is mocked

| Part | Status |
|---|---|
| SST schema, blank record, IHH rule map, field reference (234 fields, 145 rules) | working |
| Packet ingest and page index (Stage A) | working: pages render locally; the index is a Claude call |
| Section extraction with evidence (Stage B) | working code, tested only in mock mode in the cloud session; live run needs the key |
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

[What we saw it miss on the 11 packets, and the blind score against IHH's decisions. Fill in before 4:00 PM.]

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
