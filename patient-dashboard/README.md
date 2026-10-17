# Patient dashboard ("My recovery plan")

A single, self-contained HTML page that shows a patient where their recuperative care referral stands.
Open `recovery-plan.html` in a browser. No server, no build step.

## What it shows

- Where things stand: program status, a 3-step progress bar (sent, in review, answer), leave date, last update
- What is still missing, who is getting it, and whether the patient has to do anything
- Logistics before leaving the hospital: health plan approval, doctor follow-up, 30-day medicine supply, equipment
- Questions the patient can ask their team
- A short help note (tell your nurse; call 911 in an emergency)

## Data rules

- Reads only `patient_summary` and fields marked `x-patient_visible` in `sst/sst.schema.json`.
  No diagnoses, medicines, labs or scores. Status and logistics only.
- Opens in the language of `patient_summary.language` (falls back to `patient.preferred_language`).
  Spanish and English are built in.
- When the viewer switches to a language the summary was not written in, the text is built from the
  structured fields (`gaps`, `meta.parties.providers`, `arrangements`). That text is generated, not staff-written.
- Shows "Draft" while `patient_summary.requires_staff_review` is true and nobody has reviewed it.

## What is real and what is mocked

- Embedded data is `sst/example.fictional.json` (made-up patient). The staff section at the bottom
  can open any other SST `.json` file locally in the browser; nothing is uploaded or sent anywhere.
- The Listen button uses the browser's built-in speech; voices vary by device.
- Not tested: read-aloud audio, dark mode, loading other SST files.
