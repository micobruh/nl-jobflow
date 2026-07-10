# Brutal job-to-master-CV match review

Evaluate `JOB_DESCRIPTION` against untouched `MASTER_CV` and `APPLICANT_PROFILE`. Never apply, tailor, or inflate fit. Credit explicit CV evidence only; preferences/keyword similarity are not proof. Treat supplied screening warnings and verification items as unresolved checks, not candidate facts.

Score 0–100: required skills 30; responsibility/evidence 25; seniority/experience 15; education/domain 10; supported ATS overlap 15; practical constraints 5. Missing mandatory qualification may cap score at 49.

Evaluate stated experience requirements against dated professional evidence in `MASTER_CV`. Do not hard-code which roles count; distinguish employment, internships, teaching, volunteering, and student projects using the CV's own labels.

Extract evidence once for document generation. Rank compact arrays: ≤10 responsibilities, ≤15 ATS keywords, vacancy-relevant exact evidence only. Each `evidence_map` item: `{"requirement":"...","evidence":"exact master-CV excerpt"}`.

Return strict JSON only:

```json
{"score":0,"components":{"required_skills":0,"responsibilities":0,"seniority_experience":0,"education_domain":0,"ats_overlap":0,"practical_constraints":0},"seniority":"entry","responsibility_list":[],"required_skill_list":[],"preferred_skill_list":[],"ats_keywords":[],"application_questions":[],"evidence_map":[],"supported_matches":[],"missing_requirements":[],"dealbreakers":[],"ats_gaps":[],"job_summary":"Two concise sentences.","recommendation":"reject"}
```

Components must sum to `score`. `reject` below 50; `proceed` at 50+. Be blunt and evidence-based.
