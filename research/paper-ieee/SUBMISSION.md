# ICEEICT 2027 submission sheet

Venue: 7th International Conference on Electrical Engineering and Information &
Communication Technology (ICEEICT 2027), MIST, Dhaka, 28–30 January 2027.
Rules read on 2026-10-23, re-checked on 2026-10-24 (unchanged), from https://iceeict.mist.ac.bd/authors/paper-submission/,
/authors/camera-ready/, /authors/important-dates/ and the CFP PDF.

## Deadline

**25 October 2026, 23:59:59 Dhaka time (17:59:59 UTC)**, final extension (from the
site's countdown, `data-deadline="2026-10-25T17:59:59+00:00"`). Notification
5 December 2026; camera-ready and registration 20 December 2026.

## What the rules require, and where the paper stands

| Rule (quoted from the site) | Status |
|---|---|
| "Maximum 6 pages including all figures, tables, and references" | 6 pages |
| "Paper Size: A4" | `a4paper`; PDF reports 595 x 842 pt |
| IEEE double-column template, conference mode | `IEEEtran` (conference) |
| "Type 3 fonts are not allowed. All fonts are to be embedded" | all fonts embedded; none Type 3 (`pdffonts`) |
| "File size: 3 MB" | about 0.35 MB |
| Double-blind: no names, affiliations, addresses, "our previous/earlier work" self-citation, or anything that "directly or indirectly" discloses identity | `\blindtrue`: authors, links and the self-citation hidden; system names replaced by SysX / JC (both lead to the public repository) |
| "similarity index must be less than 30%" (camera-ready: also below 3% from a single source) | 8-word overlap with every public source of this project is at most 1.2%; 3.8% combined |
| IEEE multiple-submission policy (linked from the submission page) | the work must not be under review elsewhere; the older journal draft in `research/paper/` stays unsubmitted while this paper is under review |

## CMT fields (paste as plain text)

**Title:** When Does Joint Autoscaling Pay? A Pre-Registered Simulation Study of
Replica, Cache, and Model-Tier Control for LLM Serving

**Subject area / track:** Track 1, Artificial Intelligence and Data Analytics
(topic "Distributed and Cloud Computing").

**Keywords:** autoscaling; large language model serving; semantic caching; model
routing; pre-registered evaluation

**Abstract:** the abstract of `main.tex`, without LaTeX. It mentions no system
name, so it is safe for the double-blind form.

**Authors:** entered in CMT only (not in the PDF). Every co-author needs a CMT
account, and all authors confirm the author order, so fix it before submitting.

**File:** upload `ICEEICT2027_submission.pdf`, built with `\blindtrue` (the
default in `main.tex`). Do not put author names in the file name.

## Camera-ready (if accepted), 20 December 2026

- Set `\blindfalse` and `\camerareadytrue`: this prints the authors, the real
  system names, the data links, the conference header and the copyright line
  `979-8-3503-8577-9/26/$31.00 ©2027 IEEE` that the camera-ready page specifies.
- The camera-ready version must also fit 6 pages. It does: with `\blindfalse`
  and `\camerareadytrue` the paper builds to 6 pages, header and copyright line
  included. Re-check the page count after any edit made during review.
- The site's PDF eXpress ID (62016X) and the copyright line look copied from 2024
  (the home page gives IEEE Conference ID 72922). Confirm both with the organizers
  before generating the final PDF.
- Package: PID.zip (PID.pdf passed by PDF eXpress, the pass e-mail, a revision
  summary, the abstract, a 7-minute video or poster) plus PID_Copyright.pdf.
- One author must register and present; no-show papers are not sent to IEEE Xplore,
  and "no refund of the paid fees may be claimed by the no-show author."
