# Tailored one-page CV content

Create ATS-friendly CV for `JOB_DESCRIPTION`. Candidate facts: `MASTER_CV` only. Vacancy supplies priorities/terms, not candidate evidence.

## Select

Rank seniority, responsibilities, skills, domain, outcomes, tools, repeated terms. Choose closest evidence; prefer ownership, impact, production use, requested tech. Never duplicate achievements.

Fit beats fixed counts. Prefer ≤3 experiences/projects; remove weakest to keep one page. Follow `generation_constraints.cv_word_budget`. DOCX renderer owns font, alignment, bullets, dates, margins, spacing.

`experience_assessment.count_status` controls required-year credit, not whether a role may appear. Prefer direct roles and strong projects; use relevant supporting/excluded roles only as fallback when stronger evidence is sparse. Never present excluded experience as satisfying required years.

## Output

Return structured CV Markdown only; no analysis, score, notes, or code fence. Markdown feeds a Word-style DOCX/PDF renderer. Order: Header, Summary, Experience, Projects, Education, Skills, Languages.

- Header: exactly the candidate name and contacts from `MASTER_CV`. Never append a role or tagline.
- Summary: ≤40 words/2 lines; supported domain, methods, tech, impact; no location, relocation, availability, or start date.
- Experience: reverse chronological; select only supported, relevant roles; key role ≤4 bullets; others only unique useful bullets.
- Projects: supported names; select by relevance, evidence, tech, impact.
- Education: reverse chronological, concise; show thesis/end-project title as a bullet directly below each education item.
- Skills: compact category lines; relevant supported skills only. For general CVs, preserve the role-specific category names supplied by the general CV prompt.
- Languages: copy supported languages and levels from `MASTER_CV` using `Language (Level)` format.

Experience: `*Role | Dates*`, then plain organisation; omit job/work location. Education: `*Degree | Dates*`, then plain institution; thesis/end-project as a bullet directly below. Projects: `*Supported Name*`, no dates. Single asterisks mark renderer item lines; never use `###`. Separate main items with a blank line. Use Markdown bullets. No inline bullets, raw HTML, tables, text boxes, headers, footers, visible application context, or styling instructions.

## Writing and truth

Use clear English, strong verbs, vacancy terms, supported metrics. One achievement per bullet, ideally 9–15 words, under 105 characters. Avoid first person, stuffing, generic claims, soft-skill lists, repeated bullet-opening verbs within one item, “worked on”, “helped with”, “involved in”, “responsible for”. Keep ATS terms in evidence, never keyword paragraphs.

Overflow: remove least relevant bullet; shorten; trim skills/education; remove weakest project/role. Add evidence only when space remains.

Never invent/alter employers, titles, dates, responsibilities, metrics, technologies, qualifications, leadership, languages, work authorisation, or domain experience. Omit unsupported requirements; never inflate exposure.

Before return: verify every claim against `MASTER_CV`; exact metrics/titles; role-aligned summary; natural supported concepts; no Experience/Projects duplication; required Markdown; one A4 page. Prefer specific verbs and varied bullet rhythm; remove promotional adjectives, filler, and formulaic phrasing.
