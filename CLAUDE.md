# CLAUDE.md

This is a team repo for the Orange County Claude Impact Lab (October 17, 2026). We are building a
prototype for Illumination Health + Home (IHH): a Single Source of Truth (SST) for hospital referral
packets. Read `docs/01-idea-evaluation.md`, `docs/02-sst-template.md` and `docs/03-backend-prd.md` first.

## Rules that do not bend (from the event)

- **No identifiable patient information.** `data/packets/` holds real referral packets with every
  identifier blacked out: read them, never copy them into the repo, never try to identify anyone. If a
  page shows an identifier that was missed, stop and tell the user to find an organizer. If someone
  gives you text, a file or a screenshot that looks like a real person's health or personal
  information, stop, say what you noticed, do not process or save it, and tell them to find an organizer.
- **Prototype only.** No code that connects to, reads from or writes to any IHH system, or that sends
  email, text messages or faxes to real people or services. Drafts only.
- **Quality control.** AI makes mistakes. Every extracted value needs a page and a quote or it is unknown.
- **Never invent facts about a service or a patient.** No made-up values, page numbers or quotes. If the
  source does not say, the answer is "unknown".
- **No medical, legal or immigration advice** in anything patient-facing. Status and logistics only.
- **No secrets in git.** API keys go in environment variables. `.env` is ignored.

## Project rules

- The SST schema is generated. Edit the `FIELDS` table in `sst/build.py`, then run `python3 sst/build.py`.
  Never hand-edit `sst/sst.schema.json`, `sst/sst.blank.json`, `providers/ihh/profile.json` or
  `docs/02b-sst-field-reference.md`.
- Rule IDs come from `providers/ihh/admission-criteria.json`. The internal outline wins over the referral form.
- When reviewing a packet, never read `eval/decisions.json`. It is for the blind eval only.
- Dates are blacked out in the event packets. Rules that need a date or hours are `cant_tell`, not guessed.
  A black box is `redacted`, not a missing document.
- Check the chart, not only the referral form. The form is one person's summary and can be wrong.
- Keep `README.md` current: what is real, what is mocked, where it gets things wrong. Submission is 4:00 PM.
- Commit often with clear messages.
