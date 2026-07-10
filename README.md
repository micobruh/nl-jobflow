# nl-jobflow

Local, Codex-first job discovery, screening, and application-draft generation for non-EEA applicants in the Netherlands. Data Science/AI is the first maintained study-background preset.

The workflow finds vacancies, checks configurable eligibility and CV fit, and drafts reviewable CVs and motivation letters. It never applies, submits forms, contacts employers, or invents candidate facts.

> This is an advisory research tool, not legal or immigration advice. Verify permit, salary, sponsor, internship, security-screening, and contract requirements with the employer and current official sources.

## Quick start

Install Python 3.12, LibreOffice, Poppler, and Chromium support:

```bash
sudo apt update
sudo apt install python3-venv python-is-python3 poppler-utils libreoffice
git clone https://github.com/YOUR-USER/nl-jobflow.git
cd nl-jobflow
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp config.example.yaml config.yaml
cp master_cv.example.md master_cv.md
cp .env.example .env
```

Replace every placeholder in `master_cv.md`, then run the setup form and check the installation:

```bash
python jobflow.py setup
python jobflow.py preflight
python -m unittest discover -s tests -q
```

In Codex, run:

```text
/find-jobs
```

Runtime state, generated documents, credentials, the user configuration, and the real master CV are ignored by Git.

## Configuration

`config.example.yaml` contains only normal user choices. `python jobflow.py setup` safely regenerates `config.yaml` through a terminal questionnaire.

Maintained policy lives in `config.defaults.yaml`. `study_profiles.yaml` recommends roles from the shared `role_catalog.yaml`; specialized CV checks and guidance remain under `presets/` and `prompts/presets/`. Advanced users may create ignored `config.override.yaml`.

### Applicant

- `residence_route`: `student_permit`, `orientation_year`, `highly_skilled_migrant`, or `other`.
- `study_status`: `enrolled` or `graduated`. Only an enrolled student-permit profile activates the 16-hour/summer-work warning and full-time rejection.
- `current_education_level` and `highest_completed_education_level`: `mbo`, `hbo_bachelor`, `wo_bachelor`, `hbo_master`, `wo_master`, or `phd`.
- `graduation_date`: used as context for manual immigration checks; the program does not calculate permit eligibility from it.
- `dutch_level`: `none`, `A1`, `A2`, `B1`, `B2`, or `C1+`.
- `work_authorization_notes`: factual context supplied to the reviewer. Do not put secrets here.

### Search criteria

- `study_profiles`: select one or more of `data_science_ai`, `computer_science`, `statistics`, and `software_engineering`.
- `roles`: choose from the deduplicated roles recommended by the selected studies; setup initially selects all recommendations.
- `max_required_education_level`: rejects vacancies explicitly requiring a higher level.
- `max_required_experience_years`: rejects higher explicit minimums; preferred experience remains reviewable.
- `internships.regular`, `.graduation`, and `.enrollment_required`: independent internship gates.
- `schedules`: any of `full_time` and `part_time`.
- `workplaces`: any of `onsite`, `hybrid`, and `remote`.
- `locations.selected`: desired city groups. Selecting Eindhoven also accepts configured nearby places such as Veldhoven.
- Location groups are maintained defaults; users select groups rather than editing municipality aliases.
- `eligibility.require_recognized_sponsor`: require an exact IND register or configured alias match.
- `eligibility.reject_explicit_visa_denial`: reject explicit sponsorship refusal.
- `eligibility.accept_security_screening`: allow or reject explicit nationality, clearance, screening, or export-control requirements.

Missing salary, sponsorship intent, security restrictions, education, or workplace information does not reject a job. It appears under `verification_needed` in screening data and reports.

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
| `/preflight` | Check local dependencies and optional credentials. |
| `/doctor` | Check configuration, CV, queues, and source health. |
| `/next` | Show actionable and verification queues. |
| `/reports` | Show jobs, marketplace results, sources, and outcomes. |

`COMMANDS.md` contains the exact expansions. `AUTOMATION.md` defines scheduled orchestration. Lower-level deterministic commands are available through `python jobflow.py --help` and the `Makefile`.

## Optional Telegram drafts

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to deliver approved drafts to your own chat. Telegram is optional. Delivery still means “drafts to the applicant”; the workflow never sends anything to a recruiter.

`systemd/jobflow-feedback.service` assumes the repository is at `~/nl-jobflow`; edit both paths before installing it when using another location.

## Privacy and safety

- Never commit `master_cv.md`, `config.yaml`, `.env`, `data/`, or `artifacts/`.
- Use only final official employer or ATS URLs for manual imports.
- Review every generated claim against your master CV.
- Record applications and outcomes only after acting manually.
- Before publishing a fork, run `git ls-files` and search tracked files for names, email addresses, phone numbers, private paths, tokens, and generated documents.

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

Study profiles recommend shared role IDs; overlapping studies reuse the same role definition. Later disciplines normally add a profile and reuse catalogue roles, adding new role policy only when necessary.
