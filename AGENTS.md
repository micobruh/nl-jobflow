# AGENTS.md

This repo automates job discovery, screening, and document generation.

## Main workflow

For scheduled or manual automation, read and follow `AUTOMATION.md`.

## Short commands

If the user starts with a short command such as `/full-run`, `/find-jobs`,
`/write-docs`, `/url-docs`, `/jd-docs`, `/general-cv`, `/general-cvs`,
`/preflight`, `/doctor`, `/next`, or `/reports`, read `COMMANDS.md`, expand
the matching command, and follow the referenced prompt or deterministic command
flow.

Default command flow:

- run scan/discovery
- prune inactive jobs
- evaluate eligible jobs
- generate PDFs only for passing jobs
- report summary and blockers

## Safety rules

- Never apply to jobs.
- Never contact recruiters or companies.
- Never submit forms.
- Never fabricate experience, education, visa status, or availability.
- Enforce the configured internship, enrollment, work-hours, and immigration-route criteria.

## Verification

After code changes, run:

```bash
python -m unittest discover -s tests -q
```

## Output expectations

Report:

- scan summary
- marketplace discovery summary
- jobs accepted/rejected/deferred
- generated PDFs
- blockers
