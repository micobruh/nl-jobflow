# Telegram feedback automation

Recovery queue runs every 15 minutes. Primary near-instant path is `python jobflow.py feedback-worker`. Python owns orchestration and all file writes; the selected Codex, Claude, or Cursor CLI only returns validated structured drafts and reviews. Generated application documents must use natural professional language and the rules in `AUTOMATION.md`.

1. Run `python jobflow.py feedback`; exit if returned list is empty.
2. For each pending reply, reuse existing `brief.json`; `process_feedback` launches a fresh writing-only provider process for only the mapped document.
3. It launches a separate fresh review-only process after each staged writer result. Reviewer input never includes writer reasoning or edit authority. Python runs deterministic gates, allows at most three immediate attempts, updates `quality.json`, and redelivers only a passing package.
4. Missing binaries, authentication failures, timeouts, malformed output, schema failures, or quality failures are queued with provider-specific diagnostics and exponential retry timing.
5. `feedback-done` remains available for manual recovery only; the worker records successful provider-driven revisions itself.

Never send application or recruiter outreach.
