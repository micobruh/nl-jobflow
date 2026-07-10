# Codex automation prompt

Run weekdays at `30 11 * * 1-5` in Europe/Amsterdam.

Keep orchestration/internal JSON terse; recruiter-facing documents use natural professional English. These rules are self-contained. Routine runs load no external skills.

Main agent only orchestrates deterministic commands/artifacts/routing. Never draft/review. Use isolated subagents:

- Writer: `prompts/write_documents.md`; writing-only; one run per generation attempt.
- Reviewer: `prompts/review_documents.md`; fresh review-only agent per attempt.

If either role cannot spawn or hits a usage limit, run `.venv/bin/python jobflow.py mark-needs-review <job-id> --blocker "..."` and stop that job. Retain best artifacts/scores. Never merge roles.

1. Run `.venv/bin/python jobflow.py scan`, then `.venv/bin/python jobflow.py prune`. For pasted JDs, skip scan/prune and run `.venv/bin/python jobflow.py import-jd --company ... --title ... --location ... --description-file ...`. Marketplace employers need exact normalized IND sponsor/configured alias matches. Existing URLs refresh without restarting generation. Two source misses close a vacancy.
2. Evaluate all `data/screening/*.json` by ascending `priority`: relevance, recency, then configured schedule. Run `shadow-extract` for measurement. Give a fresh reviewer `prompts/evaluate_job.md`, applicant profile, master CV, job data, warnings, and verification items. Save JSON to `data/matches/<job-id>.json`; run `record-match`. Score below 50 rejects; passes create `brief.json`.
3. Run `.venv/bin/python jobflow.py jobs --status active --workflow-status accepted`; process its order (pre-tailoring match score, then recency). Run `contacts` per job; official vacancy/company contacts only. Placeholders are valid.
4. Give a writer compact `brief.json`, constraints, evidence map, assigned prompt, and newest compact failure summary. Supply full master CV only for exact evidence checks. Supply no prior conversations. Assign exact `cv.md`, `letter.md`, `outreach.md` paths; validate `agent_run.schema.json`. Generate all once; regenerate only failed documents.
5. Run `.venv/bin/python jobflow.py score <job-id> --documents all` first, then `--documents cv` or `--documents letter` for revisions. Cheap truth/structure/question/tone checks precede rendering; unchanged evaluations/renders are reused. Fix truth/tone/layout/contact/question failures before ATS.
6. Give a fresh reviewer newest outputs only after deterministic render/page gates are clean enough. Regenerate failing documents only, from categorized failures, with a new writer. Truth/natural quality beat ATS; never add visible keyword stuffing. Stop after eight attempts.
7. Write `quality.json`: `PASS` only when reviewer score ≥90, deterministic CV/letter scores ≥90, categorized gates pass, all questions answered, and CV/letter PDFs are one page. Otherwise `NEEDS REVIEW`.
8. Run `.venv/bin/python jobflow.py deliver <job-id>`. If `NEEDS REVIEW`, send drafts only when no final document score is below 85 and 1–2 final documents score 85–89; add a below-90/care-needed Telegram disclaimer. Telegram wording follows `prompts/telegram_summary.md`; delivery gates stay deterministic. Never apply/contact/submit/message.

Use `.venv/bin/python jobflow.py preflight` to diagnose renderer, converter, temp, Telegram, manual contacts.
