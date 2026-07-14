# Short Commands

Use this file as the cross-agent command catalog. When a user starts a message
with one of these commands, expand it to the matching workflow. Keep all
`AGENTS.md` and `AUTOMATION.md` safety rules active.

Initial or updated user configuration is collected with `python jobflow.py setup`, including multiple studies and the recommended roles the user wants to keep.

Use one active `JOBFLOW_PROFILE`, or pass `--profile PATH` before each subcommand.
Never mix profile roots within one workflow.

## `/full-run`

Run `prompts/start_all_now.md`.

Purpose: scan/discover jobs, prune inactive jobs, evaluate eligible jobs,
generate PDFs only for passing jobs, deliver allowed drafts, and report summary
and blockers.

## `/find-jobs`

Run discovery and screening only:

1. Read `AGENTS.md` and `AUTOMATION.md`.
2. Follow the optional agent marketplace step in `AUTOMATION.md`, then run `python jobflow.py scan [--marketplace-results ...]` and `python jobflow.py prune`; capture the returned `scan_run_id` and `screening_job_ids`.
3. Run reports: `lead-report`, `marketplace-report`, `source-health`.
4. If the scan found zero listings or returned no `screening_job_ids`, stop with no job decisions or PDFs. Otherwise run `python jobflow.py jobs --status active --workflow-status screening --scan-run <scan_run_id>` and evaluate only those returned jobs with `prompts/evaluate_job.md`; save `data/matches/<job-id>.json`; run `python jobflow.py record-match <job-id> data/matches/<job-id>.json`.
5. Stop before contacts, writers, reviewers, scoring, rendering, and delivery.

Report scan summary, marketplace summary, accepted/rejected/deferred jobs,
`generated PDFs: none`, and blockers.

## `/write-docs`

Write documents only for already accepted database jobs:

1. Read `AGENTS.md` and `AUTOMATION.md`.
2. Run `python jobflow.py jobs --status active --workflow-status accepted`.
3. Process jobs in returned order.
4. For each job, run contacts, then follow `AUTOMATION.md` steps 4-8 with
   `prompts/write_documents.md` and `prompts/review_documents.md`.
5. If writer/reviewer spawning fails or limits are hit, run
   `python jobflow.py mark-needs-review <job-id> --blocker "..."`

Do not scan, prune, import, or evaluate new screening jobs.

## `/url-docs`

Import one final official employer/ATS HTTPS job URL, evaluate it, and write
documents only if accepted:

1. Read `AGENTS.md` and `AUTOMATION.md`.
2. Run `python jobflow.py add-lead linkedin <official-url>`.
3. Run `python jobflow.py lead-report`.
4. If a screening job was created, evaluate it with `prompts/evaluate_job.md`,
   save `data/matches/<job-id>.json`, then run `record-match`.
5. If accepted, run contacts and follow `AUTOMATION.md` steps 4-8 with
   `prompts/write_documents.md` and `prompts/review_documents.md`.

Required user input: final official employer/ATS job URL.

## `/jd-docs`

Run `prompts/start_from_given_description.md`.

Purpose: import a pasted job description, evaluate it, and write documents only
if accepted.

Required user input: company, title, location, and pasted job description.

## `/general-cv`

Run:

```bash
python jobflow.py general-cv --title "TITLE"
```

Purpose: write a general one-page CV for a job title, without using a vacancy
description and without delivering anything.

Required user input: a title supported by the selected preset, for example `TARGET ROLE`.

## `/general-cvs`

Run:

```bash
python jobflow.py general-cvs
```

Purpose: regenerate one general one-page CV for each `###` role heading in the
master CV's `Professional Summary Bank`, without using vacancy descriptions and
without delivering anything.

Use `python jobflow.py general-cvs --skip-current` to skip matching PASS
artifacts whose master CV and prompt digests are current.

Required user input: none.

## `/review-master-cv`

Review the private master CV without editing it:

1. Read `AGENTS.md`; do not run `AUTOMATION.md` or any job workflow.
2. Run `python jobflow.py master-cv-audit` and save the JSON to `/tmp/master-cv-audit.json`.
3. Give a fresh review-only agent `prompts/review_master_cv.md`, the untouched master CV, audit JSON, configured target roles, and `master_cv_review.schema.json`. Save its strict JSON output to `/tmp/master-cv-review.json`.
4. Run `python jobflow.py record-master-cv-review /tmp/master-cv-review.json`.
5. Report status, score, priority actions, and saved report paths.

Never edit `master_cv.md`, browse, load external skills, contact anyone, deliver files, or change job/database state. This command is explicit only and is never part of `/full-run`.

Required user input: none.

## `/preflight`

Run:

```bash
python jobflow.py preflight
```

Purpose: diagnose renderer, converter, temp, Telegram, manual contacts, and
browser dependencies.

## `/doctor`

Run:

```bash
python jobflow.py doctor
```

Purpose: run safe environment diagnostics plus queue counts, Codex CLI
availability, master CV presence, and recent source issues.

## `/next`

Run:

```bash
python jobflow.py next
```

Purpose: show the next actionable queues: screening jobs needing match review,
accepted jobs needing documents, `NEEDS REVIEW` jobs, feedback queue, and
source issues.

## `/reports`

Run:

```bash
python jobflow.py jobs --status active
python jobflow.py lead-report
python jobflow.py marketplace-report
python jobflow.py source-health
python jobflow.py role-gap-report
python jobflow.py outcome-report
```

Purpose: show current active jobs, manual/marketplace import summaries, source
health, repeated unclassified role gaps, and recorded application outcome statistics.
