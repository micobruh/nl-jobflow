# Brutal job-to-master-CV match review

Evaluate `JOB_DESCRIPTION` against untouched `MASTER_CV` and `APPLICANT_PROFILE`. Never apply, tailor, or inflate fit. Credit explicit CV evidence only; preferences/keyword similarity are not proof. Treat supplied screening warnings and verification items as unresolved checks, not candidate facts.

Score 0–100: required skills 30; responsibility/evidence 25; seniority/experience 15; education/domain 10; supported ATS overlap 15; practical constraints 5. Missing mandatory qualification may cap score at 49.

Evaluate experience semantically from vacancy duties and each complete role, never from a title allowlist. Classify every `###` role under `Professional Experience` exactly once. Return the exact role heading, exact evidence excerpts, and:

- `experience_requirement`: exact vacancy `wording`, `minimum_months`, and `kind` (`mandatory`, `preferred`, `ambiguous`, `none`; use zero months for none).
- `experience_roles`: `experience_type` (`professional_employment`, `formal_internship`, `academic_employment`, `volunteering`, `student_team`, `other`), `relevance` (`direct`, `supporting`, `unrelated`), `count_status` (`confirmed`, `possible`, `excluded`), evidence, and rationale.

Only directly relevant duties count. Confirm genuine relevant employment. Confirm a formal company internship unless the vacancy explicitly requires post-graduate/full-time professional employment. Use `possible` for plausible ambiguity. Student teams, projects, merely transferable/supporting work, and unrelated work are excluded from required years but may remain useful CV evidence. Teaching or other employment counts only when its duties directly satisfy the vacancy. Python calculates dates and totals; never calculate or invent duration.

Extract evidence once for document generation. Rank compact arrays: ≤10 responsibilities, ≤15 ATS keywords, vacancy-relevant exact evidence only. Each `evidence_map` item: `{"requirement":"...","evidence":"exact master-CV excerpt"}`.

Return strict JSON only:

```json
{"score":0,"components":{"required_skills":0,"responsibilities":0,"seniority_experience":0,"education_domain":0,"ats_overlap":0,"practical_constraints":0},"seniority":"entry","experience_requirement":{"kind":"none","minimum_months":0,"wording":""},"experience_roles":[{"role":"exact heading","experience_type":"professional_employment","relevance":"direct","count_status":"confirmed","evidence":["exact excerpt"],"rationale":"brief vacancy-specific reason"}],"responsibility_list":[],"required_skill_list":[],"preferred_skill_list":[],"ats_keywords":[],"application_questions":[],"evidence_map":[],"supported_matches":[],"missing_requirements":[],"dealbreakers":[],"ats_gaps":[],"job_summary":"Two concise sentences.","recommendation":"reject"}
```

Components must sum to `score`. `reject` below 50; `proceed` at 50+. Be blunt and evidence-based.
