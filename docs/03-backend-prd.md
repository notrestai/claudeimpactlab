# Backend PRD: SST referral service

*Version 0.2, October 17, 2026 (0.2 adds the readiness score, the read-only Analyze mode and conversation transcription). Prototype for the OC Claude Impact Lab. Nothing here connects to any IHH system.*

## 1. Goal

A service that turns a faxed referral packet into a Single Source of Truth (SST), checks it against a
provider's requirements, scores how ready the packet is to be decided, produces the gap list and a draft
reply, lets the provider analyze the record in read-only mode, captures coordination conversations as
transcripts that propose updates, and lets three roles (hospital, patient, provider) read and update the
same record. Everything the portal shows comes from this service.

Success today: a coordinator uploads any of the 11 packets (or an unseen one handed over by a judge) and
within a few minutes gets an SST with page citations, a rule-by-rule readout, the gaps and a draft reply.
Our blind score on the 11 known decisions is reported honestly in the README.

## 2. Users and roles

| Role | Does | Must never |
|---|---|---|
| Provider coordinator (IHH) | Uploads a packet, reads the SST and readout, confirms fields, requests gaps, records the decision | Be shown a decision as final without their confirmation |
| Hospital case manager | Sees gaps, enters values the packet lacks, uploads more pages, marks requests as sent | Edit the provider's evaluation |
| Patient | Reads the plain-language summary in their language, sees what is still needed and who is getting it | See clinical interpretation or eligibility predictions |
| Developer / admin | Runs the eval harness, inspects runs and token usage | |

Auth in the prototype is a role switcher on every request (`X-Role` header). No accounts, no passwords.

## 3. Scope

**P0, must ship**

- Upload a packet PDF; render pages; build the page index.
- Extract the SST section by section with evidence; merge; persist; history entry.
- Evaluate against the IHH profile; generate gaps and a draft reply.
- Read and update the SST per role; field-level history.
- Eval harness: run all 11 packets blind, compare recommendation with `eval/decisions.json`.

**P1, should ship**

- Readiness score per provider (Stage E): deterministic, explained, with top actions. Cheap once Stage C exists.
- Analyze mode (Stage F): read-only briefing and question answering over the SST and pages, with citations.
- Hospital actions on gaps (mark sent, enter value, upload more pages, re-run on new pages only).
- Patient summary generation in the patient's language, with the staff-review flag.
- Page image crops served for evidence hover.

**P2, if time**

- Conversations (Stage G): browser dictation or pasted transcript of a hospital call or patient conversation,
  consent gate, identifier scrub, summary, proposed SST updates accepted by a person.
- Second provider profile and a side-by-side readiness view (the score makes this a table).
- Export the SST as a one-page PDF.

**Non-goals today**

- Sending email, fax or text. Requests are drafted only.
- Audio file transcription. Claude does not take audio input; today's conversations use the browser's built-in
  speech recognition (Chrome) or pasted text. A speech-to-text service is a next step.
- A score that predicts approval. The readiness score measures completeness and alignment, never a probability.
- Real authentication, consent management, audit beyond `history`.
- Any connection to IHH's systems. Any real patient data beyond the de-identified packets on local disk.
- Production-grade de-identification. The event packets are already de-identified; the extractor is told never to output names or dates.

## 4. Architecture

```
FastAPI (Python 3.13)  ──  SQLite (one row per referral, SST as JSON, history table)
   │
   ├── /ingest        PDF -> pages (PNG) -> page index (Claude)
   ├── /extract       pages + schema -> SST sections (Claude, structured output, parallel per section)
   ├── /evaluate      SST + provider profile -> rule results, hard stops, gaps, draft reply (code + Claude for judgment rules)
   ├── /summary       SST -> patient_summary in the patient's language (Claude), requires_staff_review = true
   ├── /score         rule results + profile weights -> eligibility.scores (code only)
   ├── /analyze       SST + pages + question -> cited answer, read-only (Claude)
   ├── /conversations transcript -> scrub -> summary + proposed SST updates (Claude); accept/reject by a person
   └── /sst/...       read / patch / confirm / history per role
```

Libraries: `fastapi`, `uvicorn`, `anthropic`, `pypdfium2` (render pages to PNG; no system dependency),
`jsonschema`, `sqlite3` from the standard library. Frontend is separate and talks JSON.

Directory layout:

```
backend/
  app.py              FastAPI app and routes
  pipeline/
    ingest.py         PDF -> page PNGs under data/pages/<referral_id>/p001.png
    page_index.py     Stage A
    extract.py        Stage B
    evaluate.py       Stage C
    gaps.py           gap generation and draft reply
    summary.py        Stage D
    score.py          Stage E
    analyze.py        Stage F
    conversations.py  Stage G: scrub, summarize, propose updates
    scrub.py          identifier check used by extract, conversations and the merge step
  store.py            SQLite access, history
  roles.py            who may change what
  eval/run_eval.py    blind score against eval/decisions.json
sst/                  schema, blank, example, build.py
providers/ihh/        criteria, profile
data/packets/         the 11 PDFs, never committed
data/pages/, data/runs/   generated, never committed
```

## 5. Pipeline

### Stage A: page index

Input: every page as a PNG at about 150 dpi. Output: `meta.packet.pages[]` (doc type, orientation,
legibility, earlier-stay flag, duplicate-of). One call per batch of 10 pages, with each image preceded
by a text block `[Page N]`. Structured output using the `PageRow` schema. This stage is cheap and lets the
UI show a table of contents immediately.

### Stage B: section extraction

One call per SST section (19 sections), run in parallel with a small concurrency limit. Each call sends:

- system: the extraction rules (below) and the IHH criteria text, cached;
- user: all page images with `[Page N]` labels, cached as a prefix (identical across the 19 calls, so
  only the first call pays for the images), then the section's schema and instructions.

Output: the section object, validated against `sst.schema.json` with `jsonschema`. On validation failure,
retry once with the error message; on second failure, mark the section's fields `cant_tell` with a note.

Extraction rules given to the model, verbatim in the prompt:

1. Only record what a page shows. For every `found` value give the page number and a short verbatim quote.
2. If the packet does not contain it, `not_in_packet`. Never assume normal.
3. A black box is `redacted`. Do not report it as missing and do not guess what was under it.
4. If two pages disagree, set `conflict`, record the value you consider most recent, and list the other under `conflicts`.
5. Rules that need a date or a count of hours are `cant_tell` when dates are blacked out.
6. Never output a name, date of birth, record number, address, phone number or anything that identifies a person.
7. Check the chart, not only the referral form. The form is one person's summary and can be wrong.

### Stage C: evaluation

Deterministic first. For each rule in `providers/ihh/profile.json`, read its `sst_fields`:

- any needed field with status `pending`, `not_in_packet`, `redacted`, `cant_tell` or `conflict` makes the rule `cant_tell`;
- otherwise a small Python function per rule family decides `met` or `not_met` (thresholds such as 100 ft,
  CIWA under 8, five readings, EF 25 percent, SBP under 170, two negative troponins);
- rule kind `ask` becomes an open question, never pass/fail; kind `conditional` is `needs_confirmation`
  until the matching `arrangements` field is `confirmed`; kind `exclude` that is `met` is a hard stop;
- eligible- and ineligible-condition IDs come from `conditions.problem_list[].matched_rule_ids`.

Then one Claude call for the judgment rules only (for example OTH-2 sex offender case by case, INT-X2
"complicated large abscess", GEN-1 "too ill or frail"), given the SST and the rule text, returning
`RuleResult` objects with evidence. The model never changes a deterministic result.

Recommendation: any hard stop gives `likely_ineligible`; any `cant_tell` on a `must`, `exclude` or
`document` rule that applies gives `request_information`; all applicable rules `met` gives
`likely_eligible`. The coordinator's decision is a separate field and is the only thing called a decision.

### Gaps and the draft reply

A gap is created for every `cant_tell` rule and every checklist document that is `not_in_packet`,
grouped by `who_can_supply`, with `draft_request_text` written from a template plus the rule text. The
draft reply to the hospital is assembled from the gaps and the hard stops in IHH's own format: yes, no
with a brief reason, or a list of what to send. It is text in the API response; nothing is sent.

### Stage D: patient summary

One Claude call with the SST's `documents`, `gaps`, `meta.parties.providers` and `patient.preferred_language`
only (not the clinical sections), producing `patient_summary` in that language, at a plain reading
level. `requires_staff_review` is always true on generation. The prompt forbids medical, legal or
immigration advice and eligibility predictions.

### Stage E: readiness score

Pure code in `score.py`, run after every evaluation. Inputs: `eligibility.rules`, `documents`, the profile's
weights (defaults 0.3 completeness, 0.3 rule coverage, 0.3 rules met, 0.1 arrangements). Steps:

1. **Applicable rules.** A rule applies when its condition flag is true or it is unconditional. Rules for
   conditions the patient does not have (for example cardiac rules for a cellulitis case) are not counted.
2. **Completeness** = checklist documents present divided by those this packet needs. DOC-9 cultures only
   count when an infection is present; DOC-10 is always needed.
3. **Rule coverage** = applicable rules whose status is `met` or `not_met`, divided by all applicable rules.
4. **Rules met** = `met` divided by `met` plus `not_met`. Undefined when nothing is answerable; then 0.
5. **Arrangements** = conditional rules that are `confirmed`, divided by conditional rules that apply.
6. **Readiness** = the weighted sum, rounded. **Band**: 85 and above `ready_to_decide`, 65 to 84 `nearly_ready`,
   below 65 `needs_documents`. Any hard stop forces `blocked_by_hard_stop`; the number is still shown so the
   coordinator can see how complete the packet was.
7. **Explanation and top actions** are built from templates: the component with the biggest shortfall is named
   first, and the up-to-three gaps whose closure would raise the score most are listed by gap id.

The score is recomputed whenever a field changes status. It is stored under `eligibility.scores`, logged
in history as `scored`, and filtered out of the patient role's reads. For the hospital role the same code
runs once per provider profile and returns a table: provider, readiness, band, missing items.

### Stage F: Analyze (read-only)

`POST /referrals/{id}/analyze` with `{mode, question}`. Modes: `briefing` (a six-sentence read of the
case), `question` (free text), `compare_sources` (where the referral form and the chart disagree), and
`what_would_change` (which gaps, if closed, would flip which rules). The call sends the SST JSON (clinical
sections, eligibility and gaps, never `patient_summary`), the cached page images, and the mode's
instructions. Output is structured as `AnalysisRow`: answer, cited SST paths, cited pages, unknowns. Rules:

- Read-only. The endpoint has no write path to the SST; the role check also refuses `patient`.
- Every claim cites an SST path or a page. A claim that cites neither is removed by the validator.
- If the record does not contain what the question needs, the answer says so and lists it under `unknowns`.
- The answer never states an approval likelihood and never gives medical advice; it describes the record.
- Each run is appended to `analyses` and to history as `analyzed`, with the model name.

### Stage G: conversations

A conversation starts in the UI with a consent step: the coordinator ticks that every participant agreed
to dictation or recording, or the transcript cannot be saved. Capture is the browser's speech recognition
(live dictation during a call on speaker, speaker labels assigned by a toggle), or pasted text, or typed
notes. On save, `POST /referrals/{id}/conversations`:

1. **Scrub.** `scrub.py` looks for names against the packet's redaction patterns, full dates, phone numbers,
   record numbers and emails. Flagged text is replaced with `[redacted]` and `identifier_scrub` is `flagged`
   so a person reviews it before anything else runs.
2. **Summarize and propose.** One Claude call with the transcript, the current gaps and the field reference
   returns `summary`, `follow_ups` (each tied to a gap id where it fits) and `extracted_items`: SST path,
   proposed value as text, verbatim quote, timestamp. Only paths in the field table are allowed.
3. **Accept or reject.** `POST /referrals/{id}/conversations/{cid}/items/{n}` with `{status}`. Accepting
   writes the value with status `entered`, evidence `source: transcript`, and a history row `accepted`.
   Rejecting records `rejected`. Nothing changes until a person acts.
4. **Re-score.** Any accepted item triggers Stage C and E on the affected rules.

Patient conversations follow the same path, with `kind: patient_conversation`, and only patient-owned
fields (language, phone, car, service animal, interpreter) and `follow_ups` are proposed from them.

## 6. Claude API details

- Model: `claude-opus-5-5` for Stages B, C, D, F and G. Stage A may use `claude-sonnet-5-5` to save credits. Stage E is code.
- Thinking: leave the default (adaptive). Effort: `high` for Stage B, `medium` for A and D.
- Structured outputs: `output_config={"format": {"type": "json_schema", "schema": <section schema>}}`.
  Every object in `sst.schema.json` already has `required` listing all properties and `additionalProperties: false`.
  Citations mode is incompatible with structured output, so page numbers and quotes are fields the model fills.
- Images: each page as a base64 PNG `image` block, preceded by a `text` block `[Page N of M]`. Sending
  pre-rendered pages rather than the PDF gives reliable page numbers and lets us re-run on new pages only.
- Prompt caching: `cache_control` on the last page image block so the 19 section calls share one cached
  packet prefix. Verify with `usage.cache_read_input_tokens` on the second call.
- Streaming with `client.messages.stream(...)` and `get_final_message()` for Stage B (long input).
- `max_tokens`: 16000 for sections, 4000 for page index batches and the summary.
- Check `stop_reason` before reading content; treat `refusal` and `max_tokens` as a failed section.
- API key from `ANTHROPIC_API_KEY` in the environment. `.env` is ignored by git.

Cost estimate per 34-page packet: about 34 images at roughly 1,500 tokens each, so about 50K cached
input tokens plus the criteria text, read 19 times from cache, plus about 2K to 6K output tokens per
section. Well under a dollar per packet at cache-read rates. The event credits cover hundreds of runs.

## 7. Data model (SQLite)

| Table | Columns |
|---|---|
| `referrals` | `id`, `created_at`, `updated_at`, `packet_path`, `page_count`, `status` |
| `sst` | `referral_id`, `version`, `json` (the whole SST), `updated_at`, `updated_by_role` |
| `history` | `id`, `referral_id`, `ts`, `actor_role`, `actor_label`, `action`, `path`, `old_value`, `new_value`, `note` |
| `runs` | `id`, `referral_id`, `stage`, `started_at`, `ended_at`, `model`, `input_tokens`, `cache_read_tokens`, `output_tokens`, `ok`, `error` |
| `providers` | `id`, `name`, `profile_path`, `profile_version`, `score_weights` (JSON) |
| `conversations` | `id`, `referral_id`, `kind`, `started_at`, `consent_confirmed`, `transcript_source`, `scrub_result`, `json` (the `ConversationRow`) |
| `analyses` | `id`, `referral_id`, `ts`, `actor_role`, `mode`, `json` (the `AnalysisRow`) |

The SST is stored whole as JSON and re-validated on every write. `history` is the audit trail and is
also mirrored into `sst.history` for the UI.

## 8. API

All responses are JSON. Role comes from the `X-Role` header: `provider`, `hospital`, `patient`.

| Method and path | Role | What it does |
|---|---|---|
| `POST /referrals` | provider, hospital | Create a referral; multipart upload of the packet PDF. Returns `referral_id` |
| `POST /referrals/{id}/ingest` | provider, hospital | Render pages and build the page index (Stage A) |
| `POST /referrals/{id}/extract` | provider, hospital | Run Stage B on all pages, or `?pages=12,13` for new pages only |
| `POST /referrals/{id}/evaluate?provider=ihh` | provider | Run Stage C, regenerate gaps and the draft reply |
| `POST /referrals/{id}/summary` | provider, hospital | Generate the patient summary (Stage D) |
| `GET /referrals/{id}/sst` | all | The SST, filtered by role (patient gets `patient_summary`, `documents` statuses, `gaps` without clinical text, provider statuses) |
| `PATCH /referrals/{id}/sst` | provider, hospital, patient | Body: `[{path, value, note}]`. Checks `x-owner`; sets status `entered`; writes history |
| `POST /referrals/{id}/confirm` | provider, hospital | Body: `[path]`. Sets status `confirmed` |
| `POST /referrals/{id}/gaps/{gap_id}` | provider, hospital | Body: `{status, note}`. Moves a gap through open, requested, received, resolved |
| `POST /referrals/{id}/decision` | provider | Body: `{decision, reason}`. Records the coordinator's decision |
| `POST /referrals/{id}/summary/review` | provider | Clears `requires_staff_review` |
| `GET /referrals/{id}/pages/{n}.png` | provider, hospital | Page image for evidence hover. Never for the patient role |
| `GET /referrals/{id}/history` | provider, hospital | Full history |
| `GET /referrals/{id}/reply` | provider | The draft reply text to the hospital |
| `POST /referrals/{id}/score?provider=ihh` | provider, hospital | Recompute the readiness score (also runs after every evaluate and accepted change) |
| `GET /referrals/{id}/scores` | provider, hospital | Scores for every provider profile, as a table. Never for the patient role |
| `POST /referrals/{id}/analyze` | provider | Body: `{mode, question}`. Read-only; returns an `AnalysisRow` and appends it to `analyses` |
| `GET /referrals/{id}/analyses` | provider | Past analyses |
| `POST /referrals/{id}/conversations` | provider, hospital | Body: `{kind, participants, consent_confirmed, transcript_source, language, transcript_text}`. Refused unless consent is true. Scrubs, summarizes, proposes items |
| `GET /referrals/{id}/conversations` | provider, hospital | List with summaries |
| `POST /referrals/{id}/conversations/{cid}/items/{n}` | provider, hospital | Body: `{status: accepted or rejected}`. Accepting writes the field with transcript evidence |
| `GET /providers` | all | Available provider profiles |
| `GET /sst/schema` | all | `sst.schema.json` |

Role filtering lives in `roles.py` and is applied on every read and write. The patient role never
receives page images, clinical sections or the eligibility readout.

## 9. Quality control

- **Evidence gate.** A `found` value without a page and a quote fails validation and is downgraded to `cant_tell` with a note.
- **Quote check.** For a sample of fields the UI shows the page crop next to the quote so the coordinator can see it in one glance.
- **Unknown by default.** The blank record is `pending`; the extractor must positively set `not_in_packet`.
- **Conflict detection.** The extractor is asked to list disagreements; the merge step also flags any field two sections filled differently.
- **Confirm before decide.** `POST /decision` is refused unless the fields behind every hard stop and every `met` rule the recommendation relies on are `confirmed`.
- **Score sanity.** Unit tests on `score.py` with hand-built rule results: a hard stop always gives `blocked_by_hard_stop`; an empty packet scores 0; the fictional example scores 46.
- **Analyze grounding.** Every sentence in an analysis must cite a path or a page; the validator drops uncited sentences and logs the drop. A test asks a question the record cannot answer and expects it under `unknowns`.
- **Consent gate.** A conversation without `consent_confirmed` is refused with a 400 and nothing is stored.
- **Scrub test.** A pasted transcript containing a made-up full date and phone number comes back with both replaced and `identifier_scrub` flagged.
- **Blind eval.** `eval/run_eval.py` runs all 11 packets with `eval/decisions.json` never loaded into any prompt, then prints a table: packet, our recommendation, IHH's decision, agree or not, and the rules we flagged. The README reports it as is.
- **Red-team list.** Before the demo, try: a packet with no referral form (R10), the form contradicting the chart, a sideways MAR (R01), a rotated PT evaluation (R01), a packet from an earlier stay mixed in.

## 10. Privacy and event rules

- Packets live only under `data/packets/`, which git ignores. Page images and runs under `data/` are also ignored.
- The extractor is instructed never to output identifiers. The merge step rejects any value that looks like a date with a month and day, a phone number or a medical record number.
- No prompt, log or database row contains `eval/decisions.json` content.
- Nothing is sent to anyone. Drafts are text in responses.
- `history.actor_label` is a role label, never a person's name.
- Conversations are stored only after consent is confirmed, and after the identifier scrub. California is an all-party-consent state for recorded calls; the UI says so on the consent step.
- Analyze mode is read-only by construction and is refused for the patient role.
- The readiness score is never returned to the patient role.

## 11. Demo script (two minutes)

1. Coordinator view: drop the fax in. Page index appears with doc types and the sideways pages flagged.
2. SST fills. Hover a value, see the page crop and the quote. Point at a `conflict` (two ages) and a `redacted` date.
3. Readout: the rules met, the hard stops, the `cant_tell` rules with the exact missing item.
4. Gaps and the draft reply: "please send the MRSA swab result and the last five sets of vitals".
5. Hospital view: the same gaps, mark one as sent, enter a value, the rule flips.
6. Readiness score: 46, needs documents, with the three actions that would raise it. Press Analyze: the briefing, with citations, and the age conflict called out.
7. A dictated 30-second mock call with the hospital: the summary appears, one proposed update is accepted, the score moves.
8. Patient view: the plain-language status in Spanish, staff review flag cleared.
9. One line on the blind score against IHH's 11 real decisions, and where we got it wrong.

## 12. Plan for the rest of the day

| Time | Backend | Frontend | Words and test |
|---|---|---|---|
| 11:30 to 12:30 | `ingest.py`, `page_index.py`, `store.py`, `POST /referrals`, `/ingest` | Coordinator shell, page index table, `GET /sst` rendering from `example.fictional.json` | Download packets, read R01 to R03 by hand, write expected gaps |
| 12:30 to 2:00 | `extract.py` with caching and validation, `/extract`, `evaluate.py` deterministic rules, `gaps.py` | SST field view with evidence hover, readout, gaps | Run R01, R05, R10; log every wrong value |
| 2:00 to 3:00 | `score.py` and `/score`, `analyze.py` and `/analyze`, judgment-rule call, `/decision`, `summary.py`, role filtering, `run_eval.py` | Score card, Analyze panel, hospital view, patient view | Blind eval on all 11; README "where it gets things wrong" |
| 2:30 to 3:15 (if P0 and P1 are green) | `conversations.py`, `scrub.py`, consent gate, accept and reject | Consent step, dictation panel, proposed-items list | Script and rehearse the 30-second mock call |
| 3:00 to 3:45 | Freeze. Fix only what breaks the demo | Polish demo path only | Screen recording, README, submission form |
| 3:45 | Push final. Submit | | |

## 13. Questions for IHH staff in the room

1. When a packet is missing one document, do you deny, or call the hospital and wait? How long do you wait?
2. Does a resolved DKA still exclude? Does C. diff exclusion end when isolation ends or when treatment ends?
3. For home health, which threshold do you actually require before admission: ordered, or scheduled?
4. Would you want the patient to see the gap list, or only the hospital?
5. What do you write back today when you deny? We want the draft reply in your words.
6. Would a readiness score help you decide which packet to read first, or would you ignore it? What should the bands be called?
7. Do you take notes during hospital calls today? Where do they go? Would a transcript with proposed updates save time or add work?
