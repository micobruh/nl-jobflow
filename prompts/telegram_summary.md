# Telegram delivery summary

Create Telegram message text only from already-approved deterministic delivery data. Do not decide whether delivery is allowed.

Inputs: role data, optional posting date, scores, summary, gaps, near-pass scores.

Rules:

- Keep concise and readable on mobile; no Markdown table.
- Never say an application was applied, submitted, emailed, sent to a recruiter, or otherwise acted on.
- Include role, company, location, match score, quality status, CV/letter scores, summary, main gaps, and URL.
- Include unresolved eligibility verification items when present.
- If known, show exact posting date and age in days; otherwise omit it.
- Always include exactly: `Drafts only; nothing sent to recruiter.`
- If any final document score is 85–89, include a care-needed disclaimer naming each below-90 document and score.
- Do not mention private reviewer reasoning or hidden workflow details.
