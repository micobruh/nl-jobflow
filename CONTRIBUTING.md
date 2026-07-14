# Contributing

Keep changes small, local-first, candidate-neutral, and compatible with the safety rules in `AGENTS.md`.

1. Fork the repository and work on a focused branch.
2. Use only fictional people, employers, contacts, vacancies, and education records.
3. Do not commit `master_cv.md`, `config.yaml`, `.env`, databases, artifacts, credentials, or private paths.
4. Run `python -m unittest discover -s tests -q` and `git diff --check`.
5. Explain the behavior change and any verification limitations in the pull request.

For bugs, include the platform, Python version, command, sanitized error, `/doctor` result, and relevant `source-health` entry. Remove names, contact details, URLs containing private identifiers, CV text, tokens, and generated documents. Report security issues through `SECURITY.md`, not a public issue.
