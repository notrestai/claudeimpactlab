# The Single Source of Truth (SST): template and rules

The SST is one JSON document per referral. It is what the extractor fills from the packet, what the
hospital and the patient add to, what the provider evaluates, and what every screen in the portal
renders. There is no other copy of the facts.

Files:

| File | What it is |
|---|---|
| `sst/sst.schema.json` | The shape of one SST (JSON Schema 2020-12). Also the structured-output target for Claude |
| `sst/sst.blank.json` | An empty SST with every field `pending`. The extractor starts from this |
| `sst/example.fictional.json` | A made-up example for UI work and tests. `meta.fictional` is true. Not from any packet |
| `providers/ihh/profile.json` | Every IHH rule ID mapped to the SST fields that answer it |
| `docs/02b-sst-field-reference.md` | Every field with its type, who supplies it, and the rules it feeds |
| `sst/build.py` | Generates all of the above from one field table. Edit the table, re-run |

## Six principles

1. **Unknown is unknown.** A fact that is not in the packet is `not_in_packet`, never assumed normal.
2. **Evidence or it did not happen.** A `found` value carries at least one page number and a short
   verbatim quote. No quote, no value.
3. **Redacted is not missing.** A blacked-out date or name is `redacted`. It is not a missing document
   and never becomes a gap request.
4. **Conflicts are shown, not resolved by the machine.** When two pages disagree (age 57 on one, 58 on
   another), the field is `conflict`, both sources are listed, and a person picks.
5. **Nothing identifying is stored.** No names, dates of birth, record numbers, addresses, phone numbers
   or photos, ever. The patient is "Patient". The hospital is a label.
6. **History is append-only.** Every change records who, when, what path, old and new value.

## The shape

```
meta              who the parties are, what is on each page of the packet, provider statuses
documents         the DOC-1 to DOC-10 checklist: present or not, on which pages
referral          why they are in hospital, insurance, authorization, inpatient vs emergency department
patient           age, gender, language, phone, car, service animal, hospice. Nothing identifying
housing           the basis for recuperative care (GEN-1 to GEN-3)
function          ADLs, continence, mobility, transfers, equipment
cognition         orientation, self-administering medications, learning
mental_health     stability, suicidal ideation, command hallucinations, conservatorship, aggression
substance_use     intoxication at admission, CIWA, withdrawal, detox compliance
safety_legal      registered sex offender (case by case)
infection_control MRSA, COVID, TB, C. diff, isolation
medications       the list, insulin, anticoagulants, IV meds, 30-day supply
lines_devices_oxygen  IV access, oxygen, CPAP/BiPAP, trach, dialysis, fixators
wounds            one row per wound
vitals            last sets, sepsis signs, blood pressure stability
labs              glucose series, troponin series, cultures, A1C, INR
imaging_cardiac   chest imaging, EKG, ejection fraction, arrhythmia
conditions        primary diagnosis, problem list tagged to rule IDs, condition flags
disease_specific  the extra diabetes, infection, respiratory and cardiac questions
arrangements      home health, wound care, PT, hospice, follow-ups, oxygen delivery, dialysis
eligibility       computed: one result per rule, hard stops, recommendation, the coordinator's decision,
                  and the readiness score with its breakdown
gaps              what to ask for, from whom, and whether it has been requested or received
patient_summary   plain-language status for the patient, reviewed by staff before it is shown
conversations     transcribed coordination calls and patient conversations, each with a summary and
                  proposed SST updates that a person accepts or rejects
analyses          read-only analyses the provider ran: briefings and answered questions, with citations
history           every change
```

Each clinical field (everything from `documents` to `arrangements`) is wrapped the same way:

```json
{
  "value": 150,
  "status": "found",
  "evidence": [{"source": "packet", "page": 4, "doc_type": "pt_ot_eval", "ref": null, "timestamp": null,
                "quote": "ambulated 150 ft with FWW, mod I"}],
  "conflicts": [],
  "confidence": "high",
  "updated_by": "extractor",
  "updated_at": "2026-10-17T10:30:00-07:00",
  "note": null
}
```

Evidence has a `source`: `packet` (a page), `transcript` (a conversation, with its id and a timestamp),
`entered` (typed by a party) or `external` (another uploaded document). Whatever the source, a quote is
required.

## Statuses

| Status | Meaning | Who sets it |
|---|---|---|
| `pending` | Nobody has looked yet | the blank record |
| `found` | Read from the packet, with evidence | extractor |
| `entered` | Typed in by a party, not from the packet | hospital, patient, provider |
| `confirmed` | A person checked an extracted value and agreed | hospital, provider |
| `not_in_packet` | Looked, not there. Unknown | extractor |
| `redacted` | Present but blacked out for privacy | extractor |
| `cant_tell` | Present but not enough to answer (needs a date or hours, or illegible) | extractor, rules |
| `conflict` | Two pages disagree; both listed under `conflicts` | extractor |

A rule can only be `met` or `not_met` when every field it needs is `found`, `entered` or `confirmed`.
Anything else makes the rule `cant_tell` and creates a gap.

## Who supplies what

Every field has an `x-owner` in the schema: the party expected to supply it.

- **hospital**: almost every clinical fact. The hospital case manager can also type a value the packet
  lacks (status becomes `entered`) or upload more pages (the extractor re-runs on the new pages only).
- **patient**: preferred language, interpreter, phone, car, service animal. Things only they know.
- **provider**: the evaluation, confirmations, gap requests, the decision.
- **system**: eligibility results, gap generation, history.

Any party may add a `note`. Only the owner or the provider may change a `value`. The extractor never
overwrites a `confirmed` or `entered` value; it adds a conflict instead.

## The lifecycle

1. **Hospital** creates the referral and attaches the packet (today: the coordinator does this from the fax).
2. **Extractor** indexes every page, then fills the SST section by section with evidence.
3. **Rules** run the provider's profile: each rule gets a status, hard stops are listed, gaps are generated.
4. **Hospital** sees the gaps while the patient is still admitted, marks each as sent or enters the value.
5. **Extractor** re-runs on anything new; **rules** re-run; gaps close.
6. **Provider** confirms the key fields, reads the readout, records the decision and the reason.
7. **Patient** sees a plain-language summary in their language: what was sent, what is still needed, who is
   getting it, which programs are considering them. Staff review it first.

## The readiness score

`eligibility.scores` is computed from the rule results, never by a model, so it can always explain
itself. It answers "how ready is this packet to be decided by this provider", not "will it be approved".

| Component | What it measures | Default weight |
|---|---|---|
| completeness | checklist documents present, out of those this packet needs | 0.3 |
| rule_coverage | applicable rules that can be answered from the record | 0.3 |
| rules_met | of the answerable rules, how many are met | 0.3 |
| arrangements | conditional rules whose arrangement is confirmed | 0.1 |

Hard stops are counted separately and are never averaged in: one hard stop sets the band to
`blocked_by_hard_stop` whatever the number says. The score comes with a two-sentence explanation and up
to three actions, each tied to a gap, that would raise it most. Weights live in the provider profile so
a second provider can weigh things differently. The patient never sees the score.

## Conversations

A conversation is a call with the hospital case manager or a talk with the patient, captured by browser
dictation, an uploaded recording, pasted text or typed notes. `consent_confirmed` must be true before a
transcript is stored; California requires every party's consent to record a call. The transcript is
scrubbed for names, dates of birth, phone numbers and record numbers before storage. Claude writes a
summary and proposes SST updates with a quote and timestamp each; nothing changes in the record until a
person accepts the item, and an accepted item carries transcript evidence.

## Analyses

The provider's Analyze button runs in read-only mode. It reads the SST and the page images, never
writes to either, and every answer cites SST paths and pages and lists what the record does not
contain. Four modes: a briefing, a question, compare sources (form against chart), and what would change
(which missing items would flip which rules). Each run is kept under `analyses` and logged in history.

## What the patient view may contain

Status and logistics only. Allowed: which documents were sent and which are still needed; who is
getting them; which programs are reviewing and their status; questions the patient can ask their team;
what recuperative care is, in one sentence. Not allowed: diagnoses explained, lab values interpreted,
eligibility predictions, anything that reads as medical, legal or immigration advice. The text is
generated into `patient_summary`, marked `requires_staff_review: true`, and shown only after a staff
member clears it.

## Mapping to IHH's rules

`providers/ihh/profile.json` lists all 145 IHH rule IDs (general criteria, the 48 rules, the body-system
lists, the referral checklist and the form screen). Each has the SST fields that answer it. Thirty
IDs are eligible-condition names (post abdominal surgery, cataract surgery, and so on); those are
answered through `conditions.problem_list[].matched_rule_ids`, not a dedicated field.

The precedence rule from IHH's own documents is carried in the profile: the internal outline wins over
the referral form; sex offender status is case by case; wheelchair users who transfer independently are
eligible; tuberculosis and refusing detox are exclusions from the form only.

## Adding or changing a field

Edit the `FIELDS` table in `sst/build.py`. Each row is: path, type, owner, rule IDs it feeds, checklist
docs it comes from, whether the patient view shows it, and a one-line meaning. Run `python3 sst/build.py`.
It validates the schema, the blank and the example, and regenerates the profile and field reference.
