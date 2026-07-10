# Telegram feedback automation

Recovery queue runs every 15 minutes. Primary near-instant path is `python jobflow.py feedback-worker`. Keep orchestration and internal handoffs compact; generated application documents must use natural professional language and the rules in `AUTOMATION.md`.

1. Run `python jobflow.py feedback`; exit if returned list is empty.
2. For each pending reply, reuse existing `brief.json`. Main agent remains orchestrator and must spawn a writer subagent for only the mapped document.
3. Spawn a fresh isolated reviewer subagent after each writer attempt. Never give reviewer writer reasoning or edit authority. Run at most three immediate attempts; queue remaining attempts for recovery processing, archive agent run records and reviews, update `quality.json`, and redeliver passing package.
4. If writer or reviewer subagent cannot be spawned, mark `NEEDS REVIEW` and queue work; never self-review.
5. Only after successful processing run `python jobflow.py feedback-done <update-id>`.

Never send application or recruiter outreach.
