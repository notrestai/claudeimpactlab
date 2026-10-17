# Idea evaluation: a Single Source of Truth for the referral

*Written on the day, before building. Read with `docs/02-sst-template.md` and `docs/03-backend-prd.md`.*

## The idea in one paragraph

Today a hospital faxes 15 to 35 pages to each recuperative care provider, and a coordinator at each
provider reads all of it by hand against six pages of rules. We turn that packet into one shared,
structured record: the **Single Source of Truth (SST)**. The SST is built from the packet by Claude,
with a page citation for every fact and "unknown" wherever the packet is silent. Providers such as
Illumination Health + Home (IHH) publish their requirements as data. The SST checks itself against
those requirements, lists what is missing, and the hospital gets that list **while the patient is
still in the bed**, when a nurse can still do the swab or the therapist can still write the note. The
hospital, the patient and the provider work from the same record, and the patient has a plain-language
view of where things stand.

## Why it is a strong idea

- **It attacks the root cause, not the symptom.** Challenge 1 as written is "help the coordinator read
  faster". The real loss is a denial for a missing PT note that costs a day, while the hospital has
  already discharged the patient. Catching the gap before the fax is sent is where the time is.
- **The provider side is usable on day one.** A coordinator can drop a faxed PDF into the tool and get
  the SST, the gap list, the rule-by-rule readout and a draft reply. That is the 40 percent of the
  score for day-one impact, and it does not depend on any hospital adopting anything.
- **Requirements as data already exists.** IHH's criteria are in `providers/ihh/admission-criteria.json`
  with stable rule IDs. A second provider is another file. Hospitals send to several providers at once,
  so one SST checked against several profiles is a real win for the hospital case manager too.
- **It gives the patient something.** The brief asks for "a bed instead of the street". A patient who
  can see "your hospital still owes the program three documents" can ask for them. Nobody else in the
  room is building for the patient on Challenge 1.
- **It fits the "product and idea" score.** Three roles, one record, no retyping. Judges will see why
  it is different in the first minute.

## Where it is risky, and what we do about it

| Risk | Why it matters | What we do |
|---|---|---|
| Scope. Three portals, a dashboard and messaging in under six hours | "Small and working beats big and unfinished." Features that need IHH's live systems do not place | Build in layers (below). The portal is three views over one JSON document, not three apps. Messaging is a list of drafted requests with a status, not chat |
| Hospitals will not adopt a new portal today | Day-one impact rests on the provider side | The demo starts from the fax. The hospital and patient views are shown as "what this becomes" |
| Extraction errors on crooked faxes | A confident wrong answer is the worst outcome | Every value carries a page and a quote or it is not a value. Unknown is the default. The coordinator confirms before anything is called a decision. We score ourselves blind against IHH's 11 real decisions |
| Dates are blacked out in the event packets | Hour-based rules (24 h insulin, 72 h alcohol, 24 h COVID swab) cannot be checked | Those rules return `cant_tell` and become gaps. We say this in the demo. In production the dates are present |
| Patient-facing content | Rules forbid medical, legal or immigration advice in anything client-facing | The patient view shows status and logistics only: what was sent, what is missing, who is getting it, what the options are. Staff review it before it is shown |
| "Single source of truth" is a strong claim | A record edited by three parties needs provenance | Every field has a status, an owner, who last changed it and evidence. History is append-only. The chart stays the truth; the SST is a structured index into it with citations |
| Many patients have no phone | A patient dashboard they cannot open helps nobody | The patient view also prints to one page and can be shown on the case manager's screen |

## Fit against the judging sheet

| Weight | Area | How we score |
|---|---|---|
| 40% | Day-one impact | Coordinator uploads a fax, gets the SST, gaps and readout in minutes. Works without any hospital change |
| 35% | Product and idea | One record, three roles, requirements as data, patient has a view. Nobody else does this |
| 25% | Clarity of demo | One packet, one screen per role, one outcome: "the hospital was asked for the swab before discharge" |

Judges may hand us an unseen packet. The extraction path must be robust on any packet, not tuned to R01.

## Build order and cut lines

1. **Must ship.** Packet in, SST out, with page citations and unknowns. Gap list and rule-by-rule readout
   against IHH's profile. Draft reply to the hospital. Coordinator view. Blind score on the 11 packets.
2. **Should ship.** Hospital view: see the gaps, mark each as sent or enter the value, upload more pages.
   Patient view: plain-language status, in the patient's language, printable.
3. **If time.** Second provider profile to show the comparison. Field-level history view.

Everything below the line we are working on at 3:00 PM gets cut and goes into "Next steps" in the README.

## What we tell the judges we did not do

- Nothing is sent. Requests are drafted; a person sends them.
- No login, payments or real patient data. Roles are a switcher in the prototype.
- Hour-based rules cannot be checked on the event packets because dates are redacted.
- The SST is a prototype format we designed today, not a standard. Open Referral and FHIR exist; mapping is a next step.

## Decisions made

- Challenge 1 is the submission. The portal is the vision; the extractor plus gap check is the product.
- Python backend (FastAPI) with the Anthropic SDK, SQLite for storage, one JSON document per referral.
- The SST schema is generated from one field table in `sst/build.py` so the schema, the blank record,
  the IHH rule map and the field reference never disagree.
