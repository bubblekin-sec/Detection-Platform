# Plan: Threat intelligence → detection (MVP)

Status: increments 1 and 2a done. Increment 2b (UI scaffold) is next.

**2a notes.**
- Huntable `sources.yaml` is pinned at `904a6c4`: 38 sources, 28 seeded active (upstream active and has an RSS feed). The rest start inactive, with `config.upstream_active` recording the upstream flag.
- Seeding only inserts missing slugs and never updates a row, so upstream changes reach existing installs only through a deliberate migration or by editing them.
- `POST /api/sources` creates RSS sources and returns 409 when the name's slug is already taken. Editing a source (PATCH) is not built yet.
- **Portability:**
  - `DATABASE_URL` is the only DB setting. On Azure it comes from a Container Apps secret reference, with `sslmode=require` for Azure Database for PostgreSQL; Entra token auth comes later.
  - Run `alembic upgrade head` and `python -m app.cli seed-sources` as a Container Apps job using the same image, not at API startup.
  - The image has been checked running both commands against the compose database. Paths below are proposed until an increment lands.

Reference design: [Huntable-CTI-Studio](https://github.com/dfirtnt/Huntable-CTI-Studio) (MIT). We reuse its pipeline design and seed source list, not its stack.

## Decisions

| Topic | Decision |
| --- | --- |
| Output | Sigma rules + KQL for **Microsoft Defender XDR Advanced Hunting** (Device* tables) from day one |
| KQL approach | Sigma is the rule of record. Each rule has one or more **query artifacts** per target (`defender_xdr`), each tagged with an `origin`: `converted` (pySigma Kusto backend + Microsoft XDR pipeline, deterministic), `llm` (LLM-written from the Sigma and evidence), or `human_edit`. A per-profile `query_strategy` chooses `converted`, `llm`, `converted_with_llm_fallback` (default: the LLM writes KQL only when conversion fails, e.g. DNS, service install, scheduled task) or `both` (shown side by side). LLM KQL is checked against the Defender XDR table and column catalog before review. A syntax check against real Defender is done only at enterprise handoff. |
| LLM | One `LLMClient` interface, chosen by settings. Anthropic first; an approved enterprise provider (e.g. Azure OpenAI) later. Tests never call a real LLM. |
| MVP scope | Ingest → junk filter → relevance → extraction → Sigma + KQL → human review → YAML export |
| Rule destination | Stays in the platform (approve / reject / edit, then export). No GitHub or SIEM push yet. |
| Flexibility | Pipeline is driven by **profiles**: plain-language forms compiled into prompts. Detection is the first profile. The editor UI and other teams' profiles come later. |
| Sources | Seeded from Huntable's `config/sources.yaml` at a pinned commit (MIT notice in `THIRD_PARTY_NOTICES.md`). Users can add RSS sources. RSS only in the MVP. |
| Auth | None in the MVP (single local user). Entra ID at enterprise handoff. |
| Stack | FastAPI, SQLAlchemy 2, Alembic, Postgres, uv/pytest. React + Vite + TS, vitest, nginx. No Celery/Redis, LangGraph or pgvector yet. |

## Observable outcome

1. **Fetch now** on a source (`POST /api/sources/{id}/fetch`) returns `{new, duplicates, too_short}`, and new articles appear.
2. **Run detection** (`POST /api/articles/{id}/runs {profile: "detection"}`) returns 202 with a `queued` run.
3. A worker carries the run through these stages:
   - `junk_filter`
   - `relevance`: an LLM score against the profile's `min_score`
   - `extraction`: observables, each with an evidence excerpt and `platform`, `telemetry_category` and `logsource_hint` tags
   - `rule_generation`: one LLM call per observable group, with Sigma validated by pySigma. KQL query artifacts are then produced according to the profile's `query_strategy`.
4. The Review screen lists `pending_review` rules with their Sigma, KQL artifacts (each showing its origin and any warnings), evidence and article link. Editing a rule's Sigma revalidates it and regenerates the `converted` KQL. Existing `llm` and `human_edit` queries are marked stale, not discarded.
5. `GET /api/rules/export?status=approved` returns multi-document YAML containing only approved rules. Each rule keeps a stable `id`, with `references: [article url]`.

Failure cases, each covered by a test:
- **Off-topic article:** the run ends `filtered_out` at `relevance` after exactly one LLM call.
- **Invalid or empty Sigma after one retry:** no rule is created for that group, and the errors are saved in the stage result. If every group fails, the run ends `failed`.
- **Sigma with no Defender XDR mapping:** the rule is still saved. The `converted` artifact is `failed` with its reason. Under `converted_with_llm_fallback`, an `llm` artifact is generated next.
- **LLM KQL referencing an unknown table or column:** the artifact is saved as `failed` with the unknown names listed. It is never shown as usable.
- **Feed unreachable or malformed:** `source.last_error` is set and no articles are created.

## Profiles: which stages they drive

| Stage | Driven by | Notes |
| --- | --- | --- |
| ingest (RSS parse, normalize, dedup by canonical URL + content hash) | source config | Shared by all profiles |
| junk_filter (min length, min words, title keywords) | source config, deterministic | Profile-independent |
| relevance | profile: `relevant_if`, `ignore_if`, `min_score` | Generic, so other teams can reuse it |
| extraction | profile: `extract_fields[{name, description, tags[]}]` | Generic |
| output | profile `output_kind` | MVP: `sigma_kql` only. A later `summary` kind would have its own table. |

Platform detection is the `platform` tag on each observable, not a separate stage. The compiler (`profiles/compile.py`) is a set of pure functions. The later "advanced view" displays their output.

## Data model (Postgres, Alembic from the first table)

- `sources`: slug UNIQUE, name, url, rss_url NULL, active, check_frequency_seconds, `config` JSONB (Huntable's scrape fields kept verbatim), origin (`huntable_seed`|`user`), last_checked_at, last_error
- `articles`: source_id, url UNIQUE (canonical), title, author, published_at, content_text, content_hash (indexed), word_count, fetched_at
- `profiles`: slug UNIQUE, name, output_kind
- `profile_versions`: profile_id, version, `definition` JSONB, UNIQUE(profile_id, version). **Immutable.**
- `pipeline_runs`: article_id, profile_version_id, status (`queued|running|filtered_out|succeeded|failed`), current_step, error, llm_provider, llm_model, timestamps
- `stage_results`: run_id, stage, status, `output` JSONB, compiled_prompt, token usage, timestamps
- `observables`: run_id, field_name, value, evidence_excerpt, `tags` JSONB
- `detection_rules`: id UUID (= Sigma `id`), run_id, article_id, group_key JSONB, title, sigma_yaml, sigma_hash, sigma_valid, validation_errors JSONB, status (`pending_review|approved|rejected`), reviewer_note, reviewed_at
- `rule_queries`: id, rule_id, target (`defender_xdr`; later e.g. `sentinel`, `splunk`), origin (`converted|llm|human_edit`), query_text NULL, status (`ok|failed`), error NULL, warnings JSONB (lint and schema findings), source_sigma_hash (the artifact is stale when this differs from the rule's `sigma_hash`), llm_model NULL, created_at. Adding a new SIEM target or strategy adds rows, not columns.
- `detection_rule_observables`: join table

## Integration points (constructed in one place each)

| Dependency | Constructed in | Local / tests | Enterprise change |
| --- | --- | --- | --- |
| Settings | `api/app/config.py` (pydantic-settings) | `.env` / env vars | Container Apps env + secret refs |
| LLM client | `api/app/llm/factory.py` `build_llm_client(settings)`, called in the `create_app()` lifespan and `worker.main()` | `FakeLLMClient` (scripted, records calls), with `LLM_PROVIDER=fake` forced in `conftest.py` | Provider, endpoint and credential (e.g. Azure OpenAI with managed identity) |
| Fetcher | `api/app/sources/fetch.py` `build_fetcher(settings)` | Fake serving fixture XML | Outbound proxy, egress allow-list |
| Database | `api/app/db.py` `make_engine(settings)` | compose Postgres + `TEST_DATABASE_URL` | `DATABASE_URL`, later Entra token auth |
| UI → API | `ui/nginx/templates/default.conf.template` using `API_UPSTREAM`; the Vite dev server proxies `/api` | `http://api:8000` | Internal API origin |

The worker claims queued runs with `SELECT … FOR UPDATE SKIP LOCKED`. On Azure it becomes a second Container App with no ingress, or a Container Apps job for scheduled fetching.

## Proposed layout

```
api/  pyproject.toml uv.lock Dockerfile alembic.ini migrations/
  app/ main.py config.py db.py models.py cli.py worker.py
       llm/{client,factory,anthropic_client}.py
       sources/{seed,fetch,rss,router}.py  sources/seed_data/huntable_sources.yaml
       articles/router.py
       profiles/{schema,compile}.py  profiles/seed_data/detection.yaml
       pipeline/{run,junk,relevance,extraction}.py
       detection/{sigma,kql,generate,router,export}.py
  tests/ conftest.py fakes.py fixtures/{feeds,sigma}/
ui/   package.json vite.config.ts Dockerfile nginx/templates/default.conf.template src/
docker-compose.yml  THIRD_PARTY_NOTICES.md
```

## Increments

| # | Slice | Proving test |
| --- | --- | --- |
| 1 | API skeleton + `POST /api/detection/convert` (validate Sigma → KQL), `GET /api/health` | A process_creation rule converts to KQL with `DeviceProcessEvents` / `ProcessCommandLine`. A registry_set rule converts to `DeviceRegistryEvents`. Malformed Sigma returns 422. `product: aws` returns 200 with `kql: null` and a reason. Record which categories convert. |
| 2a | Postgres + seeded source catalog (`cli seed-sources`, `GET/POST /api/sources`) | Seeding is idempotent and never overwrites user edits. Scrape config is preserved. A bad URL returns 422. |
| 2b | UI scaffold + Sources page | The table renders, and a 422 shows as a field error |
| 3 | Fetch now → articles (RSS, dedup, junk filter) | Re-fetch reports duplicates. A short item counts as `too_short`. A broken feed sets `last_error` and creates no rows. |
| 4 | Profiles, runs, worker, relevance | A low score ends the run as `filtered_out` after one LLM call. Two workers never claim the same run. A missing API key fails startup without leaking it. |
| 5 | Extraction | Unknown fields or missing evidence are rejected. Observables are saved with their tags. |
| 6 | Rule generation (Sigma + `converted` query artifacts + KQL lint warnings) | 2 observable groups give 2 LLM calls and 2 rules. Invalid output twice gives no rule and saved errors. An unconvertible rule saves a `failed` artifact with its reason. A registry rule with a trailing backslash gets a lint warning. |
| 6b | LLM-written KQL (`query_strategy`, Defender XDR schema check) | A DNS rule under `converted_with_llm_fallback` makes exactly one KQL LLM call and saves an `llm` artifact. A query naming a nonexistent column is saved `failed` with that column listed. Under `converted`, the KQL LLM is never called. |
| 7 | Review screen (`GET /api/rules`, `PATCH /api/rules/{id}`, KQL edit gives a `human_edit` artifact) | An invalid Sigma edit returns 422 and the stored rule is unchanged. A Sigma edit regenerates `converted` and marks the `llm` artifact stale. |
| 8 | YAML export | Approved rules only, the output re-validates, and ids are stable |
| 9 | Containers end to end (ui/nginx, api, worker, db) | `/api/health` responds through nginx in compose |
| 10 | Batch dedup | Table-driven pairs: Jaccard ≥ 0.80, or containment with ≥ 3 values and Jaccard ≥ 0.60 |

## Increment 1 findings (2026-11-21)

Library versions: `pysigma` 1.5.1 + `pysigma-backend-kusto` 1.0.1, both current. The backend requires pySigma `>=1,<2`, so pySigma 2.x waits on a backend release. Older backends (0.4.x) declare `pysigma>=0.10` but only work on 0.11, which is why an unpinned install can pick a broken pair.

Validation (`app/detection/sigma.py`) rejects with 422:
- malformed YAML and duplicate keys
- non-mapping documents and unstructured fields
- undefined, malformed or legacy pipe conditions, and non-string conditions
- correlation documents
- zero or several rules
- input over 64 KB

Filter documents are applied to the rule they reference. Backend-unsupported features (e.g. `|cased`) give a `kql_error`, not a 500. Every 422 uses the shape `{"detail": {"errors": [str]}}`.

**Coverage.** Tested logsources against the Microsoft XDR pipeline:

| Converts | Does not convert ("Unable to determine table name") |
| --- | --- |
| `process_creation` → DeviceProcessEvents | `dns_query` |
| `registry_set` / `registry_add` / `registry_event` → DeviceRegistryEvents | `service: system` (7045 service install) |
| `network_connection` → DeviceNetworkEvents | `service: security` (4698 scheduled task) |
| `file_event` → DeviceFileEvents | `service: taskscheduler` |
| `image_load` → DeviceImageLoadEvents | `ps_script` |

**Fidelity gaps (rule output that converts but is wrong):**
1. **Trailing backslash.** A value like `contains: '\...\Run\'` turns the implied wildcard into an escaped literal `*`, which gives `endswith "...\Run*"` and never matches. This is tracked by a strict xfail test in `api/tests/test_detection_convert.py`.
2. **Registry key vs value name.** Sigma `TargetObject` (key + value name) maps only to `RegistryKey`. Defender stores the value name in `RegistryValueName`, so matches on `...\Run\Updater` miss.

**Implications for increments 5–6:**
- The rule-generation prompt should steer services and scheduled tasks to `process_creation` (`sc.exe`, `schtasks.exe`) or registry rules (`...\Services\`, `...\Schedule\TaskCache\`).
- The prompt should also avoid trailing backslashes in `contains`/`startswith` values.
- Add a post-conversion KQL lint that flags a literal `*` inside `endswith`/`startswith`/`=~`/`==`, and registry `TargetObject` matches that include a value name. Report the result as a review warning, not a rejection.
- The review UI should show each query artifact's error and lint warnings next to the Sigma.
- Unconvertible categories are the main reason for LLM-written KQL (increment 6b). Defender has these in `DeviceEvents` (`ActionType` values such as `DnsQueryResponse`, `ServiceInstalled`, `ScheduledTaskCreated`). The schema check can reuse the table and column catalog that ships with the Kusto backend (`sigma/pipelines/microsoftxdr/tables.py`).

## Risks and defaults

- **Summary-only RSS feeds.** Pick 3 MVP sources whose feeds carry full text. If that fails, fetch the article page with httpx (still no Playwright).
- **pySigma XDR coverage.** Increment 1 measures it. The conversion code is isolated in `detection/kql.py`.
- **pySigma is LGPL-2.1.** Enterprise legal should confirm. Only `detection/{sigma,kql}.py` import it.
- **Prompt injection from article text.** LLM output is constrained by a pydantic schema and Sigma validation, and nothing it produces is executed.
- **SSRF from user-added sources.** `POST /api/sources` allows only http/https, but it still accepts `http://localhost/` and `http://169.254.169.254/`. Before increment 3 fetches any user-supplied URL, the fetcher must resolve the host and refuse loopback, private, link-local and metadata addresses. It must re-check after every redirect and cap response size. Egress policy is an enterprise input.
- **LLM cost.** Relevance gates extraction, token usage is stored per stage, and there is no automatic schedule in the MVP.
- **Stale `running` runs after a crash.** Re-running creates a new run. Reclaiming stale runs is deferred.
- **Duplicate rules on re-run.** Accepted until increment 10.

## Deferred

SigmaHQ similarity and novelty scoring; pgvector; PR submission and GitHub/SIEM push; scraping, Playwright and sitemap/listing discovery; robots.txt handling; scheduled fetching; preserving publisher-authored Sigma; the profile editor UI and compiled-prompt view; vuln management, IR and leadership profiles; Entra ID auth; Azure OpenAI provider; managed identity for the DB; `hunt_queries` extraction; KQL syntax validation against a live Defender tenant (Advanced Hunting API) or Microsoft's Kusto.Language parser; other SIEM targets.
