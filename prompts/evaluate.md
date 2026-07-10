# Independent application-document review

Evaluate `DOCUMENT` of `DOCUMENT_TYPE` (`cv`, `letter`, or `outreach`) against supplied sources. Do not rewrite.

Source authority:

- Candidate facts, metrics, skills, education, contacts: `MASTER_CV` only.
- Vacancy/company claims: `JOB_DESCRIPTION` or `JOB_METADATA` only.
- Recruiter/contact/source URLs: verified `CONTACT_CONTEXT` only.

Check support, exact metrics/dates, relevance, natural wording, duplication, readability, and format. Do not penalise omitted unsupported requirements. Unsupported claims or fabricated contacts cap score at 70. Flag clusters, not isolated words: inflated significance, vague praise, superficial `-ing` clauses, forced trios, formulaic negative parallels, filler, repeated sentence shapes, generic conclusions, chatbot commentary, and visible keyword stuffing. Preserve ordinary professional formality; polish alone is not an AI tell.

Type gates:

- `cv`: Summary, Experience, Projects, Education, Skills, Languages; concise relevant bullets; no Experience/Projects duplication; one page; reference-like typography, spacing, hierarchy, margins, density.
- `letter`: exactly one page; vacancy questions answered naturally without numbered headings; verified recipient or `Hiring Team`; vacancy-specific opening; 2–3 supported STAR examples; evidence-to-role reasoning; truthful motivation; no CV repetition.
- `outreach`: verified channels only; placeholders only when neither exists; 2–4-word email subject; email under 140 words; LinkedIn under 500 characters; exact role, one proof point, one CTA; accurate contacts/sources; no fake familiarity or sending claim.

Score 0–100. Score ≥90 passes when no unsupported claims, hard gates pass, relevance is strong, format is correct, language is natural, and no material duplication. Do not require 91; score 90 passes if hard gates are clean. Keep issues/fixes short.

Return strict JSON only:

```json
{"document_type":"cv","score":0,"passed":false,"unsupported_claims":[],"unanswered_questions":[],"contact_issues":[],"missing_supported_keywords":[],"duplication_issues":[],"ai_tone_issues":[],"readability_issues":[],"format_issues":[],"layout_risks":[],"channel_issues":[],"fixes":[]}
```

Set supplied type; use empty arrays when inapplicable. No Markdown outside JSON.
