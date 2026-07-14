# nl-jobflow

Local job discovery, screening, and application-draft generation for applicants in the Netherlands, with maintained Dutch WO study profiles.

The workflow finds vacancies, checks configurable eligibility and CV fit, and drafts reviewable CVs and motivation letters. It never applies, submits forms, contacts employers, or invents candidate facts.

> This is an advisory research tool, not legal or immigration advice. Verify permit, salary, sponsor, internship, security-screening, and contract requirements with the employer and current official sources.

## Quick start

The public beta is tested on Ubuntu Linux. Install its local dependencies and create a private profile outside the checkout:

```bash
sudo apt update
sudo apt install python3-venv python-is-python3 poppler-utils libreoffice
git clone https://github.com/micobruh/nl-jobflow.git
cd nl-jobflow
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium
python jobflow.py init-profile ~/jobflow-profiles/default
export JOBFLOW_PROFILE=~/jobflow-profiles/default
```

Replace every placeholder in `~/jobflow-profiles/default/master_cv.md`, then run the setup form and check the installation:

```bash
python jobflow.py setup
python jobflow.py doctor
python jobflow.py preflight
```

`init-profile` creates the profile directory with mode `0700` and private source files with mode `0600`. The selected profile owns its configuration, CV, credentials, database, artifacts, reports, and optional references; shared code and maintained packs remain in this repository.

The root-profile layout remains available for compatibility, but it is not the recommended setup:

```bash
cp config.example.yaml config.yaml
cp master_cv.example.md master_cv.md
cp .env.example .env
chmod 600 config.yaml master_cv.md .env
```

Omitting `--profile` and `JOBFLOW_PROFILE` selects this legacy root profile.

In a capable coding agent, run:

```text
/find-jobs
```

Runtime state, generated documents, credentials, the user configuration, and the real master CV are ignored by Git.

## Support matrix

| Capability | Support |
| --- | --- |
| Deterministic scanning, filtering, reporting, rendering, and profile management | Agent-independent Python CLI |
| Slash commands and scheduled workflows | Any agent able to follow `AGENTS.md`, `COMMANDS.md`, and `AUTOMATION.md` with isolated writers/reviewers |
| General-CV generation and automated Telegram feedback revisions | Codex CLI currently required |
| Ubuntu Linux | Tested in CI and supported for the public beta |
| macOS | Best-effort; install equivalent Python, LibreOffice, Poppler, and Playwright dependencies manually |
| Windows | Unsupported until its renderer, permissions, and service flow are tested |

| Symptom | Check |
| --- | --- |
| Python command or imports fail | Use Python 3.12, activate `.venv`, and reinstall `requirements.txt`. |
| Chromium executable is missing | Run `python -m playwright install --with-deps chromium`. |
| PDF conversion fails | Install LibreOffice and Poppler, then run `python jobflow.py preflight`. |
| Setup or permissions are unsafe | Run `python jobflow.py doctor`; it reports incomplete setup and private file modes without changing them. |
| Telegram is unavailable | Leave it disabled or set both variables in the profile `.env`; generation still works locally. |
| Scan cannot reach sources | Grant network access for `scan`/`preflight` and inspect `source-health`; maintained sources can change without notice. |

## Configuration

`config.example.yaml` is intentionally neutral and incomplete. Discovery and document commands fail closed until `python jobflow.py setup` records explicit study-profile, job-family, and role selections. Setup prints the exact RIO evidence, confidence, rationale, and regulated-programme warnings behind its suggestions; suggestions are never selected automatically.

Maintained policy lives in `config.defaults.yaml`. The offline DUO RIO catalogue identifies Dutch WO programmes, `study_profiles.yaml` maps education to suggestions, and `role_catalog.yaml` groups reusable roles into job families. Each of the 14 study profiles has one standalone YAML under `presets/`; shared role-writing guidance remains under `prompts/presets/`. Advanced users may create ignored `config.override.yaml`.

### Applicant

- `residence_route`: `student_permit`, `orientation_year`, `highly_skilled_migrant`, or `other`.
- `study_status`: `enrolled` or `graduated`. Only an enrolled student-permit profile activates the 16-hour/summer-work warning and full-time rejection.
- `current_education_level` and `highest_completed_education_level`: `mbo`, `hbo_bachelor`, `wo_bachelor`, `hbo_master`, `wo_master`, or `phd`.
- `graduation_date`: used as context for manual immigration checks; the program does not calculate permit eligibility from it.
- `dutch_level`: `unknown`, `none`, `A1`, `A2`, `B1`, `B2`, or `C1+`.
  `unknown` defers Dutch requirements for verification; other values enforce filtering.
- `work_authorization_notes`: factual context supplied to the reviewer. Do not put secrets here.

### Search criteria

- `study_profiles`: select one or more maintained Dutch WO sector or specialist profiles. Every profile has its own discipline preset.
- `job_families`: confirmed employment families suggested from explicit programme-name rules, summary headings, and accepted jobs. Exact programme and source-evidence matches are high confidence; an otherwise-unmapped exact RIO programme receives one low-confidence official-sector fallback that still requires confirmation.
- `roles`: choose roles belonging to the confirmed families. Suggestions never edit configuration automatically.
- `max_required_education_level`: rejects vacancies explicitly requiring a higher level.
- `max_required_experience_years`: rejects higher explicit minimums; `null` disables the ceiling.
- `accepted_seniority`: selects maintained seniority levels; optional
  `seniority_title_exclusions` add exact exclusions.
- `experience_policy.countable_types`: controls whether directly relevant formal
  internships and academic employment may count alongside professional employment.
- `internships.regular`, `.graduation`, and `.enrollment_required`: independent internship gates.
- `schedules`: any of `full_time` and `part_time`.
- `workplaces`: any of `onsite`, `hybrid`, and `remote`.
- `locations.selected`: desired city groups. Selecting Eindhoven also accepts configured nearby places such as Veldhoven.
- Location groups are maintained defaults; users select groups rather than editing municipality aliases.
- `eligibility.require_recognized_sponsor`: require an exact IND register or configured alias match.
- `eligibility.reject_explicit_visa_denial`: reject explicit sponsorship refusal.
- `eligibility.accept_security_screening`: allow or reject explicit nationality, clearance, screening, or export-control requirements.

Missing salary, sponsorship intent, security restrictions, education, or workplace information does not reject a job. It appears under `verification_needed` in screening data and reports.

Maintained direct-employer sources are tagged by job family and only matching sources are scanned. Sources added to a profile with `add-source` remain active for every family. Marketplace discovery continues to use the confirmed role queries.

## Commands

| Command | Purpose |
| --- | --- |
| `/full-run` | Discover, screen, draft documents for passing jobs, score, and optionally deliver drafts. |
| `/find-jobs` | Discover and screen only. |
| `/write-docs` | Draft documents for accepted jobs. |
| `/url-docs URL` | Import one official employer/ATS vacancy URL. |
| `/jd-docs` | Import a pasted job description. |
| `/general-cv TITLE` | Create a one-page role-focused CV. |
| `/general-cvs` | Create one general CV for every role in the master CV summary bank. |
| `/review-master-cv` | Audit the private master CV and save read-only improvement reports. |
| `/preflight` | Check local dependencies and optional credentials. |
| `/doctor` | Check configuration, CV, queues, and source health. |
| `/next` | Show actionable and verification queues. |
| `/reports` | Show jobs, marketplace results, sources, and outcomes. |

`COMMANDS.md` contains the exact expansions. `AUTOMATION.md` defines scheduled orchestration. Lower-level deterministic commands are available through `python jobflow.py --help` and the `Makefile`.

## Visual references

Generated CVs and motivation letters must match the one-page A4 PDFs configured by
`visual_references` in `config.defaults.yaml`. Role-specific `cv_references` in a policy may
override the shared CV reference; filenames are resolved only inside `references/`.
The neutral shared CV file is `cv-reference.pdf`; legacy `cv-data-scientist.pdf`
configuration still resolves to it when no private legacy file exists.

## Optional Telegram drafts

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to deliver approved drafts to your own chat. Telegram is optional. Delivery still means “drafts to the applicant”; the workflow never sends anything to a recruiter.

`systemd/jobflow-feedback.service` assumes the repository is at `~/nl-jobflow`.
Set `JOBFLOW_PROFILE=/absolute/profile/path` in `~/.config/nl-jobflow.env` for an isolated profile.

## Privacy and safety

- Never commit `master_cv.md`, `config.yaml`, `.env`, `data/`, or `artifacts/`.
- Use only final official employer or ATS URLs for manual imports.
- Review every generated claim against your master CV.
- Record applications and outcomes only after acting manually.
- Before publishing a fork, run `git ls-files` and search tracked files for names, email addresses, phone numbers, private paths, tokens, and generated documents.
- Treat vacancy and company text as untrusted. Runtime prompts forbid it from changing paths, tools, schemas, evidence, privacy, or safety rules.
- Report vulnerabilities privately as described in `SECURITY.md`; never attach a real CV, configuration, database, token, or generated application to an issue.

## Dutch immigration references

Rules and amounts change. Consult current official guidance:

- [International students and work limits](https://ind.nl/en/about-us/background-articles/international-students-and-the-ind)
- [Interns and apprentices](https://ind.nl/en/residence-permits/work/intern-or-apprentice-in-the-netherlands)
- [Highly skilled migrants](https://ind.nl/en/residence-permits/work/highly-skilled-migrant)
- [IND recognized sponsor register](https://ind.nl/en/public-register-recognised-sponsors/public-register-work)
- [Current income requirements](https://ind.nl/en/required-amounts-income-requirements)

## Development

```bash
python -m unittest discover -s tests -q
git diff --check
```

See `CONTRIBUTING.md` before opening a change or sanitized bug report.

Study profiles recommend shared role IDs; overlapping studies reuse the same role definition. Later disciplines normally add a profile and reuse catalogue roles, adding new role policy only when necessary.

Maintained profiles cover the principal Dutch WO sectors. `suggest-roles` reports
exact offline RIO programme matches, advisory job families, and roles from source headings
and accepted jobs; it never edits configuration. Workday family labels are vocabulary only:
vacancy title, duties, requirements, and confirmed roles remain authoritative.
Regulated professions are blocked until dedicated credential rules exist.

Maintainers can refresh the checked-in, institution-neutral programme snapshot after
downloading DUO's `Overzicht Erkenningen ho` CSV:

```bash
python jobflow.py refresh-programme-catalog /path/to/ho_erkenningen_rio.csv --as-of YYYY-MM-DD
python jobflow.py refresh-programme-catalog /path/to/ho_erkenningen_rio.csv --as-of YYYY-MM-DD --check
```

Setup, scanning, screening, and document generation never download RIO data.
Refresh stages the catalogue and all university fixtures from one digest, validates
their completeness and referential integrity, and replaces them only after every check
passes. `--check` reports drift without writing. New TU/e registrations require curated
profile and family expectations before refresh can succeed.

`python jobflow.py role-gap-report` is a read-only advisory report. It only surfaces
unclassified relevant titles seen in at least three jobs from two employers and never
adds roles or edits configuration. `/doctor` also reports setup completion, SQLite schema
version, integrity status, and the latest automatic pre-migration backup.

Before publishing, copy `.privacy-markers.example` to the ignored
`.privacy-markers`, replace its fictional lines with exact private markers, set
mode `0600`, and run
`python jobflow.py privacy-audit --markers-file .privacy-markers`.
