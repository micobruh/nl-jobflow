# Tailored one-page CV content

Create ATS-friendly CV for `JOB_DESCRIPTION`. Candidate facts: `MASTER_CV` only. Vacancy supplies priorities/terms, not candidate evidence.

## Select

Rank seniority, responsibilities, skills, domain, outcomes, tools, repeated terms. Choose closest evidence; prefer ownership, impact, production use, requested tech. Never duplicate achievements.

Rank assessed items: direct experience; exceptional projects with stronger/unique requirement proof; supporting experience; remaining relevant projects. Omit unrelated items. Add items only while one page and reference-like density hold; a zero budget minimum forbids filler.

`experience_assessment.count_status` controls required-year credit, not visibility. Never claim excluded experience satisfies required years.

## Output

Return CV Markdown only. Order: Header, Summary, optional Experience, optional Projects, Education, Skills, Languages. Omit unavailable/unselected optional sections; never emit an empty heading.

- Header: exactly the candidate name and contacts from `MASTER_CV`. Never append a role or tagline.
- Summary: ≤40 words/2 lines; supported domain, methods, tech, impact; no location, relocation, availability, or start date.
- Experience: reverse chronological; select supported direct/supporting roles by useful vacancy evidence.
- Projects: select supported direct/supporting items by relevance and unique evidence.
- Education: reverse chronological, concise; show thesis/end-project title as a bullet directly below each education item.
- Skills: compact category lines; relevant supported skills only. For general CVs, preserve the role-specific category names supplied by the general CV prompt.
- Languages: copy supported languages and levels from `MASTER_CV` using `Language (Level)` format.

Experience: `*Role | Dates*`, then plain organisation; omit job/work location. Education: `*Degree | Dates*`, then plain institution; thesis/end-project as a bullet directly below. Projects: `*Supported Name*`, no dates. Single asterisks mark renderer item lines; never use `###`. Separate main items with a blank line. Use Markdown bullets. No inline bullets, raw HTML, tables, text boxes, headers, footers, visible application context, or styling instructions.

## Writing and truth

Use clear English, strong verbs, vacancy terms, supported metrics. One achievement per bullet, ideally 9–15 words, under 105 characters. Avoid first person, stuffing, generic claims, soft-skill lists, repeated bullet-opening verbs within one item, “worked on”, “helped with”, “involved in”, “responsible for”. Keep ATS terms in evidence, never keyword paragraphs.

Overflow: remove least valuable repeated evidence first, then the weakest item. If sparse, add the next-ranked relevant item; never add unrelated evidence or filler.

Never invent/alter employers, titles, dates, responsibilities, metrics, technologies, qualifications, leadership, languages, work authorisation, or domain experience. Omit unsupported requirements; never inflate exposure.

Before return: verify every claim against `MASTER_CV`; exact metrics/titles; role-aligned summary; natural supported concepts; no Experience/Projects duplication; required Markdown; one A4 page. Prefer specific verbs and varied bullet rhythm; remove promotional adjectives, filler, and formulaic phrasing.
