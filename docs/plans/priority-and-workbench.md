# Plan: priority scoring, gated model use, workbench UI, then 6b

Status: P1–P4 done (P4 to be reviewed with P5/P6), P5 next.

**P2/P3 notes.**
- Detection profile v2 (`detection.yaml` `version: 2`) expands `priority: {source: huntable_keyword_registry, threshold: 37.5}` into concrete lists at seed time, so the stored version is self-contained. Seeding adds a higher seed version beside existing ones and never edits them.
- Dev DB: v2 was added beside v1. `score-articles` scored 715 articles in 7.3 s, so the rescore endpoint stays synchronous. 109 articles are at or above 37.5.
- Fetch summaries now include `scored`, `auto_queued` and `deferred`. Scoring and gating failures are logged and never fail the fetch.
- The existing ~109 above-threshold articles were not auto-queued, because rescoring never queues. To process that backlog deliberately: `queue-runs --min-priority 37.5 --limit N`.

**P1 measurement (2026-11-21, 715 real articles, 8 ms per article).**
- **Distribution:** mean 12.9, median 3.7, p90 52.5, max 92.8. Bands: Excellent 27, Good 33, Fair 42, Poor 8, Very Poor 605 (85%).
- **A natural cliff:** 110 articles score at least 20 and 109 score at least 37.5. Almost every article above "noise" contains at least one *perfect* term, which alone is worth 37.5. The other buckets together max out at 25, so noisy terms (`network` in 58% of articles, `detection` in 54%) can't push an article over 37.5 on their own. No further terms are dropped for now.
- **Agreement with real relevance verdicts:** all 5 articles scored by Haiku so far fall on the same side. 80.6 and 38.4 passed relevance; 1.8, 0.0 and 6.1 were filtered. At 37.5, the three filtered articles would never have reached a model.
- **Threshold for detection profile v2: 37.5** ("at least one strong detection signal"). That's about 15% of articles, so with roughly 50 new articles a day, about 7–8 Haiku calls a day before the cap.
- **Source averages** line up with intuition: DFIR Report 62, Securelist 48, Hunter's Ledger 47 … Dark Reading 2, SecurityWeek 2, CISA analysis reports 2. CISA's feed items are mostly advisories without technical detail. Follows `intel-to-detection.md` (increments 1–7 done; 8 export and 9 containers stay queued after this plan).

## Decisions (user, 2026-11-21)

| Topic | Decision |
| --- | --- |
| Priority scoring | Deterministic keyword "hunt score" (0–99.9), modelled on Huntable CTI Studio's `ThreatHuntingScorer` (MIT). Computed for each new article at fetch time; free. |
| Gating | Below the profile's threshold: never sent to any model. At or above it: automatically queued for relevance (Haiku); extraction and rules continue only if relevance passes. Manual "Run detection" still works on any article. |
| Keyword lists | Owned by the profile. The detection profile is seeded from Huntable's `config/keyword_registry.yaml` (vendored verbatim at commit `904a6c4`, with fixes applied in code). Other teams' profiles get their own lists and threshold. |
| UI | Sidebar workbench with Dashboard, Articles (priority queue), Review, Sources, and later Profiles. |
| Order | Scoring (P1–P3), API for the UI (P4–P6), UI (P7–P10), then 6b (P11) and the lint repair round (P12). |

## Scoring algorithm

- Text = title + "\n" + content, case-folded. Each bucket counts **distinct** matching terms.
- `geo(n, M) = M·(1 − 0.5ⁿ)` for n > 0, else 0.
- Score = `clamp(0, 99.9, perfect + lolbas + intelligence + good − negative)`, rounded to 1 decimal place.
- Bucket caps: perfect 75, lolbas 10, intelligence 10, good 5, negative penalty 15.
- Matching:
  - A word boundary applies only at an end of the term that is a word character, so symbol terms like `\pipe\` and `==` need no boundary.
  - A small set of Huntable's "partial" terms (hunting, detection, monitor, alert, executable, spawn, …) match as prefixes.
- Fixes over Huntable:
  - Terms are deduplicated across buckets (perfect > lolbas > intelligence > good), enforced by the profile validator.
  - The noisy `MZ`, `C:\` and `D:\` are dropped.
  - Further noisy good-bucket terms are dropped only on evidence from the P1 document-frequency report.
- Bands (fixed in code, in `pipeline/priority.py`): Excellent 80+, Good 60–79, Fair 40–59, Poor 20–39, Very Poor 0–19. Only the threshold is profile-owned.

## Data and contracts

- **`article_scores` table** (migration 0007):
  - Columns: article_id, profile_version_id, score, breakdown JSONB `{bucket: {terms, points}}`, scorer tag, scored_at.
  - UNIQUE `(article_id, profile_version_id)`; index on `(profile_version_id, score DESC)`.
  - Highlight spans are recomputed on demand, not stored.
- **`ProfileDefinition.priority`** (optional, so v1 still validates): buckets of `{max_points, terms: [{term, match: word|prefix}]}` plus a nullable `threshold`. A null threshold means auto-queue is off.
- **Detection profile v2:** carries the priority config. Seeding inserts a higher version and never updates an existing one.
- **`pipeline_runs.trigger`** (migration 0008): `manual|batch|auto`. Manual runs are claimed before auto runs.
- **Auto-queue** runs only for freshly inserted articles, only if no run of any status exists for that article and profile version, and under a daily cap: `AUTO_QUEUE_MAX_PER_DAY`, default 25, highest scores first, the rest counted as `deferred`. Rescoring never auto-queues. Backlog: `queue-runs --min-priority N`.
- **API additions (all additive):**
  - Fetch summary gains `scored`, `auto_queued`, `deferred`.
  - Article list gains sort, filters and `priority` / `pipeline` fields.
  - Article detail gains a priority breakdown and highlight spans (UTF-16 offsets).
  - Observables gain `evidence_span`.
  - New: `GET /api/dashboard`, `GET /api/rules/counts`, `POST /api/profiles/{slug}/rescore`.
- **Spend:** stage tokens × a price table (`llm/pricing.py`); an unknown model gives null.

## Increments

| # | Slice | Proving test |
| --- | --- | --- |
| P1 | Scorer, profile priority schema, Huntable keyword conversion, `cli priority-report --from-seed` (no DB writes) | `test_priority.py`: geo and caps, clamp, distinct counting, boundary, symbol and prefix rules, MZ/C:\/D:\ dropped, cross-bucket dedupe, validator rejects overlaps, v1 still validates. Then measure the distribution and fix the threshold. |
| P2 | Stored scores, profile v2, scoring at ingest, `score-articles`, rescore endpoint | v2 seeded once and v1 untouched; ingest creates scores; rescore is idempotent; drift test |
| P3 | Auto-queue gate, run `trigger`, daily cap, manual-first claim | Above threshold queued as auto; below threshold makes zero model calls; no re-queue; cap defers the lowest; null threshold queues nothing |
| P4 | Article list API: sort, band, status, priority, pipeline | Exact orderings and filters; default response unchanged |
| P5 | Article detail: breakdown, keyword spans, evidence spans | Spans slice back to their terms; emoji offsets; whitespace-collapsed evidence |
| P6 | Dashboard and rule-count API, price table | Funnel numbers; spend = tokens × prices; unknown model gives null |
| P7 | UI shell: sidebar, `/` = Dashboard, `/sources`, query-string state | Routes; sidebar Review count; `aria-current` |
| P8 | UI article queue | Sort and filter requests; badges and chips |
| P9 | UI article detail: highlighted text, priority breakdown, run panel side by side | `segments()` overlaps and newlines; evidence marks after a run |
| P10 | UI dashboard: hand-rolled bar charts with accessible tables | Funnel and volume tables; empty states |
| P11 | 6b: LLM-written KQL fallback with a Defender XDR schema check | Fallback only when conversion fails; unknown column gives a failed artifact; `converted` strategy makes no call |
| P12 | Lint-driven repair round (trailing backslash) | Repaired once and saved clean; a worse repair is discarded; a clean draft makes no call |

## Known gaps and defaults

- The threshold stays null (auto-queue off) until P1 measures the real distribution.
- Cache-creation tokens aren't stored, so spend is slightly under-reported. `rule_generation` records the configured model even when a fallback served the call.
- No router or chart library: five routes and bar charts don't justify one (reconsider for nested layouts or time-axis charts).
- After P11, a Sigma edit in review can't regenerate stale LLM-written KQL, because the API has no LLM client.
