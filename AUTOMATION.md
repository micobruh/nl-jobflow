# Codex automation prompt

Run weekdays at `30 11 * * 1-5` in Europe/Amsterdam.

Keep orchestration/JSON terse; documents use natural professional English. Routine runs load no external skills. Resolve one `JOBFLOW_PROFILE` or global `--profile PATH` for the entire run.

Main agent only orchestrates deterministic commands/artifacts/routing. Never draft/review. Use isolated subagents:

- Writer: `prompts/write_documents.md`; writing-only; one run per generation attempt.
- Reviewer: `prompts/review_documents.md`; fresh review-only agent per attempt.

If either role is unavailable, run `.venv/bin/python jobflow.py mark-needs-review <job-id> --blocker "..."` and stop that job. Retain best artifacts/scores. Never merge roles.

Codex sandbox: grant network before `scan`/`preflight`; restricted scans persist false backoff.

1. In Codex/Claude Code follow `prompts/discover_marketplaces_with_plugins.md`, then run `jobflow.py scan [--marketplace-results source=/tmp/file.json]` and prune; elsewhere omit the option. Capture final `scan_run_id`, `found`, and `screening_job_ids`. If found is zero or IDs are empty, run reports and stop: report `accepted/rejected/deferred: none`, `generated PDFs: none`, and blockers; do not evaluate, generate, or deliver. Pasted JDs use `import-jd`. Require exact sponsor/alias matches; URLs refresh and two misses close a vacancy. Never apply, message, connect, or create accounts.
2. Run `jobflow.py jobs --status active --workflow-status screening --scan-run <scan_run_id>` and evaluate only returned IDs/files in order. Run `shadow-extract`. Give a fresh reviewer `prompts/evaluate_job.md`, profile, master CV, job data, warnings, and verification items. Save `data/matches/<job-id>.json`; run `record-match`. Below 50 rejects; passes create `brief.json`.
3. Run `.venv/bin/python jobflow.py jobs --status active --workflow-status accepted --scan-run <scan_run_id>`; process only its returned IDs in order (pre-tailoring match score, then recency). Run `contacts` per job; official vacancy/company contacts only. Placeholders are valid. Historical accepted jobs are handled only by `/write-docs`.
4. Give a writer compact `brief.json`, constraints, evidence map, assigned prompt, and newest compact failure summary. Supply full master CV only for exact evidence checks. Supply no prior conversations. Assign exact `cv.md`, `letter.md`, `outreach.md` paths; validate `agent_run.schema.json`. Generate all once; regenerate only failed documents.
5. Run `.venv/bin/python jobflow.py score <job-id> --documents all` first, then `--documents cv` or `--documents letter` for revisions. Cheap truth/structure/question/tone checks precede rendering; unchanged evaluations/renders are reused. Fix truth/tone/layout/contact/question failures before ATS.
6. Give a fresh reviewer newest outputs and both `visual_comparison` images after deterministic gates. Regenerate only failures with a new writer. Missing/material visual mismatch blocks in `layout_risks`. Truth/natural quality beat ATS; stop after eight attempts.
7. Write `quality.json`: `PASS` only when reviewer score ≥90, deterministic CV/letter scores ≥90, categorized gates pass, all questions are answered, CV/letter PDFs are one page, both latest visual comparisons exist, and `layout_risks` is an empty array. Otherwise `NEEDS REVIEW`.
8. Run `.venv/bin/python jobflow.py deliver <job-id>`. If `NEEDS REVIEW`, send drafts only when no final document score is below 85 and 1–2 final documents score 85–89; add a below-90/care-needed Telegram disclaimer. Telegram wording follows `prompts/telegram_summary.md`; delivery gates stay deterministic. Never apply/contact/submit/message.

Use `.venv/bin/python jobflow.py preflight` to diagnose renderer, converter, temp, Telegram, manual contacts.
