# Prompt provenance

Runtime prompts in this repository are self-contained. External skill files are build-time references only and are not loaded during job runs.

## Pinned references

| Reference | Pin | License | Use in this repository |
|---|---|---|---|
| `blader/humanizer` | v2.8.2, commit `1b48564898e999219882660237fde01bf4843a0f` | MIT | Distilled checks for concrete language, varied rhythm, restrained professional tone, and clusters of formulaic AI-writing patterns. The separate draft/audit/rewrite workflow was intentionally omitted to avoid another model pass. |
| `wshobson/agents`, `prompt-engineering-patterns` | commit `5cc2549a50fc672230efd0a0307e2fd27ffba792` | MIT | Distilled explicit constraints, structured outputs, prompt versioning, compact context, representative tests, and latency/token measurement. |
| OpenAI primary runtime `documents` skill | package `26.630.12135` | Source-available | Distilled render-before-delivery, deterministic structure, stable page geometry, cacheable rendering, and visual QA after cheap checks pass. |
| `aalvaaro/skills`, `cv-generator` | commit `d0ac0354d7564afde250e16018221d5702491701` | No repository license found | Reviewed but not copied. Compatible principles already present here are evidence-only claims, role-specific selection, concise achievement writing, and one-page output. |
| `antigravity-awesome-skills`, `llm-prompt-optimizer` and `evaluation` | commit `b1f921a534cdbd903dff530b0cef361920a34297` | See upstream repository | Distilled explicit role/context/constraints, evidence-only drafting, multi-dimensional comparison, repeated samples, and regression gates. |

## Distillation boundaries

- Candidate facts still come only from the untouched master CV and evidence map.
- Vacancy facts still come only from the imported vacancy and verified contact context.
- Natural-writing rules may change phrasing, never meaning or evidence.
- The writer performs one silent language audit in the same generation call; there is no humanizer subagent or second rewrite pass.
- Deterministic checks run before expensive rendering. Reviewer judgment remains isolated and starts only after deterministic gates are clean enough.
- Upstream skills are never fetched automatically. Update this file, prompts, and tests together when intentionally refreshing a reference.
