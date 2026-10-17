# Build plan for the rest of the day

*Written at 11:55 AM. Submissions close at 4:00 PM. That is four hours. Everything here is sized for that.*

## The one rule

**At 3:00 PM we freeze.** Whatever is not working at 3:00 goes into the README's "Next steps" and is
described honestly in the demo. From 3:00 to 3:45 we fix only what breaks the demo path, record the
video, finish the README and submit. The whole team pushes work at least every 30 minutes.

## Roles

Four lanes. With fewer people, merge lanes in this order: D into C, then B into A.

| Lane | Who | Owns |
|---|---|---|
| A. Pipeline | backend 1 | ingest, page index, extraction, merge, store, eval harness |
| B. Rules and intelligence | backend 2 | evaluate, gaps, draft reply, score, analyze, conversations |
| C. Frontend | frontend | the three views, score card, Analyze panel, consent and dictation |
| D. Domain, words and test | non-developers | read packets by hand, expected gaps per packet, test every output, README text, patient-view wording, demo script and recording |

Lane D starts now and never waits on code: `sst/example.fictional.json` is the fixture for every screen
and every piece of copy.

## Work packages

Each package has a definition of done (DoD). A package is not done until its DoD is true on a real
packet, not the fixture.

| # | Package | Lane | Time box | Depends on | DoD |
|---|---|---|---|---|---|
| WP1 | Store and API skeleton: SQLite, `POST /referrals`, `GET /sst`, `PATCH /sst`, role header, history writes | A | 11:55 to 12:30 | none | Create a referral, read the blank SST, patch a patient-owned field as `patient`, see it in history |
| WP2 | Ingest and page index: render pages with pypdfium2, Stage A call, `POST /ingest` | A | 12:00 to 12:45 | WP1 | R01 renders 34 pages; the page index names the sideways MAR pages and the upside-down PT eval |
| WP3 | Extraction: Stage B per section, cached packet prefix, schema validation, retry, merge, `POST /extract` | A | 12:45 to 2:00 | WP2 | R01 produces an SST where every `found` value has a page and a quote; `cache_read_input_tokens` is non-zero from the second section call |
| WP4 | Evaluate and gaps: deterministic rules from `providers/ihh/profile.json`, gap generation, draft reply, `POST /evaluate`, `GET /reply` | B | 11:55 to 1:15 | fixture only; WP3 for real data | On the fixture: MOB-3 met, INF-1 cant_tell, gaps G1 to G3, a draft reply listing what to send. Then the same on R01 |
| WP5 | Score: `score.py`, `/score`, `/scores` table, runs after evaluate | B | 1:15 to 1:45 | WP4 | Fixture scores 46 and `needs_documents`; a forced hard stop gives `blocked_by_hard_stop`; unit tests pass |
| WP6 | Analyze: `analyze.py`, `/analyze` with the four modes, citation validator, refused for `patient` | B | 1:45 to 2:30 | WP3 | Briefing on R01 cites pages; an unanswerable question lands in `unknowns`; a `patient` call gets 403 |
| WP7 | Judgment rules, decision gate, patient summary, role filtering: `/decision` refuses unconfirmed fields, `/summary`, `/summary/review` | B | 2:30 to 3:00 | WP4, WP5 | Decision refused until confirmations exist; Spanish summary on the fixture with `requires_staff_review` true |
| WP8 | Conversations: consent gate, `scrub.py`, Stage G call, accept and reject, re-score | B | 2:30 to 3:15, only if WP4 to WP6 are green | WP5 | A pasted mock transcript with a fake date and phone comes back scrubbed and flagged; accepting one item writes transcript evidence and the score moves |
| WP9 | Coordinator view: upload, page index table, SST sections with evidence hover, readout, gaps, draft reply | C | 11:55 to 1:45 | fixture; WP1 for live | Renders the fixture fully; then renders R01 from the live API |
| WP10 | Score card and Analyze panel | C | 1:45 to 2:30 | WP5, WP6 | Score with four bars and three actions; Analyze panel with mode picker, answer, clickable page citations |
| WP11 | Hospital view and patient view: gaps with mark-sent and enter-value; patient status page in the patient's language, printable | C | 2:30 to 3:00 | WP4, WP7 | Hospital enters a value, rule flips, score updates; patient page shows no clinical text and no score |
| WP12 | Consent and dictation panel: consent checkbox with the California note, browser speech recognition, speaker toggle, proposed items with accept and reject | C | 3:00 to 3:15, only if WP8 lands | WP8 | A 30-second dictation produces a summary and at least one proposed item |
| WP13 | Blind eval: `run_eval.py` over all 11 packets, table in the README | A | 2:00 to 2:45 | WP3, WP4 | Table of packet, recommendation, IHH decision, agree or not. Reported as is |
| WP14 | Hand review: expected gaps and conflicts for R01, R05, R10 written before the tool runs; compare after | D | 11:55 to 1:30 | packets downloaded | Three one-page checklists; discrepancies logged as issues |
| WP15 | Words: patient-view wording in English and Spanish, README sections, "where it gets things wrong" | D | 1:30 to 3:30 | WP13 | README complete except the video link |
| WP16 | Demo: script, rehearsal twice, two-minute recording, submission form | D with C | 3:00 to 3:50 | everything above | Video link in README; form submitted; final push by 3:50 |

## Timeline

| Time | A. Pipeline | B. Rules and intelligence | C. Frontend | D. Domain and words |
|---|---|---|---|---|
| 11:55 | WP1 store and API | WP4 evaluate on fixture | WP9 coordinator view on fixture | Download packets; WP14 hand review of R01 |
| 12:30 | WP2 ingest and page index | WP4 gaps and draft reply | WP9 evidence hover, readout | WP14 R05, R10 |
| **12:45 checkpoint** | API contract check: every route in the PRD answers with the fixture shape | | | |
| 1:00 | WP3 extraction | WP5 score | WP9 live against R01 | Compare R01 output with the hand review |
| **1:45 checkpoint** | First end-to-end on R01: upload, extract, evaluate, score, readout on screen | | | |
| 1:45 | WP3 hardening, retries | WP6 analyze | WP10 score card and Analyze panel | WP15 patient wording |
| 2:00 | WP13 blind eval | WP7 judgment rules, decision gate, summary | WP11 hospital and patient views | WP15 README |
| 2:30 | WP13 results into README | WP8 conversations (if green) | WP11 finish | Issue triage with A and B |
| **3:00 freeze** | Fix only demo breakers | Fix only demo breakers | WP12 dictation (if WP8 landed) | WP16 script and rehearsal |
| 3:30 | Final push | Final push | Final push | WP16 record and submit |
| 3:50 | Done | Done | Done | Form submitted |

## Contracts the lanes agree on now

- **The SST is the only data shape.** Frontend reads `GET /referrals/{id}/sst` and renders it. No view-specific endpoints except `/scores`, `/analyze`, `/reply` and `/conversations`.
- **Role header.** Every request carries `X-Role: provider | hospital | patient`. The backend filters; the frontend does not hide anything itself.
- **Fixture first.** `sst/example.fictional.json` is what every screen is built against until 1:45 PM. If a screen needs a field the fixture lacks, add it to the example in `sst/build.py`, not to the frontend.
- **Evidence is clickable.** Every evidence object with a page opens `GET /referrals/{id}/pages/{n}.png`. Transcript evidence opens the conversation at its timestamp.
- **Unknown is visible.** Fields with status `not_in_packet`, `cant_tell`, `redacted` or `conflict` render with that word, never blank.

## Cut lines, in order

1. WP12 dictation UI, then WP8 conversations. Fallback: show the fixture's conversation, say it is a mock.
2. WP11 patient view. Fallback: print the fixture's patient summary and describe it.
3. WP6 analyze modes 3 and 4 (compare sources, what would change). Keep briefing and question.
4. WP13 full eval. Fallback: report whichever packets ran.

Never cut: WP3 extraction with evidence, WP4 evaluate and gaps, WP5 score, WP9 coordinator view.

## Risks for today

| Risk | What we do now |
|---|---|
| `pypdfium2` or `anthropic` fails to install on someone's laptop | One person installs first at 11:55 and posts the exact commands. Fallback renderer: `pdftoppm` if Poppler is present |
| Structured output rejects the big schema | Extract per section, never the whole schema at once. Each section schema is a few KB |
| Rate limits or slow calls on 34 pages | Concurrency of 3 for section calls. Stage A on `claude-sonnet-5-5`. Cache the packet prefix and check the usage counters |
| Judges hand us an unseen packet | Nothing is tuned to R01. Test on at least four packets before 3:00 |
| Browser speech recognition only works in Chrome | Demo laptop runs Chrome. Pasted text is the fallback and is what the backend tests use |
| A transcript or a packet page shows something that looks like a real name or date | Stop. Tell an organizer. Do not paste it anywhere |
| The score reads as "approval likelihood" to a judge or to IHH | The card says "readiness to decide", shows the four bars and the hard-stop line separately, and the coordinator's decision is a separate button |

## Done means

- A coordinator can upload a packet and, within a few minutes, see the SST with citations, the readout, the gaps, the draft reply and the readiness score.
- The Analyze briefing cites pages and names what is unknown.
- The hospital view can close a gap and the score moves.
- The README says what is real, what is mocked, and where it got things wrong, with the blind-eval table.
- The repo is public, has the license, has no packets and no keys, and the form is submitted before 4:00 PM.
