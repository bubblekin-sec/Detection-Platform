# Plan: Threat intelligence → detection (MVP)

Status: increments 1 to 6 done. Increment 6b (LLM-written KQL fallback) and 7 (review screen) are next.

**6 notes (rule generation).**
- **Grouping:** verified observables only, grouped by `(platform, telemetry_category)` (not `logsource_hint`, which is free text and would fragment groups). Largest groups go first, capped by `MAX_RULE_GROUPS_PER_RUN` (default 6) to bound Opus calls per article.
- **Generation and validation:**
  - Per group, `claude-opus-5-5` (effort medium, refusal fallbacks) writes one Sigma rule from the observables and their quotes, not the whole article. The prompt carries the increment 1 guidance: no trailing backslashes, and tasks and services as process_creation or registry_set.
  - The draft is validated by `app/detection/sigma.py`. An invalid draft gets one repair attempt with the validator's errors.
  - The platform sets `id` (UUID, also the rule's primary key), `status: experimental`, `references` (article URL) and `date`. The model never does.
- **Per-group outcomes** (`created`, `invalid` with errors, `llm_error`) are stored in the stage output. One failed group never loses the others. A run fails only if every group fails.
- **Query artifacts:** each rule gets a `rule_queries` row (target `defender_xdr`, origin `converted`) holding the KQL or the conversion error, plus lint warnings:
  - A literal `*` in `endswith`/`startswith`/`has`/`==`/`=~`.
  - A registry `TargetObject` whose value name may need `RegistryValueName`.
  - `source_sigma_hash` marks an artifact stale when the Sigma is later edited.
- **Deviation from the data model above:** observable links are an `observable_ids` JSON list on the rule, not a join table. That's simpler, and enough until rules can be built from several runs.
- **Hardening from review:**
  - **The platform's metadata can't be bypassed.** `finalize_sigma` loads drafts with pySigma's own loader (which rejects duplicate keys) and tolerates empty extra documents. Anything that isn't exactly one mapping goes back to the model for repair. A rule is accepted only if its Sigma `id` equals the platform's id.
  - **Heartbeat per model call** during rule generation. Stage writes and `_finish` only apply while the run is still `running`, so a run another worker failed as stalled can't be revived and can't gain rules.
  - **Groups are isolated.** An unexpected error in one group is recorded as `error`, and the other groups' rules are kept. A non-refusal model error (auth, model not found, rate limit, connection) stops the remaining groups (`not_attempted`) and becomes the run error. Each group records the served model and `fallback_from`.
  - **`telemetry_category` is a fixed list** (plus `other`), so free-text variants can't fragment groups. `other` and `unknown` observables are not sent for rules.
  - **Lint** also covers `_cs`, negated, `has*` and list (`in~ (...)`) operators. The registry value-name warning only fires when a `TargetObject` value ends in a name, not a backslash.
  - **Rules are records:** runs no longer cascade-delete them. A `position` column keeps them in largest-group-first order.
  - **UI:** groups with no rule (invalid, model error, not attempted) and groups skipped by the cap are listed with their reasons. Code blocks are labelled figures.

**5 notes (extraction).**
- **The output schema is generated from the profile** (`extraction_schema`): one list per `extract_fields` entry. Each item has `value`, `evidence` and one typed field per tag; `platform` is an enum, and `telemetry_category` carries the increment 1 guidance on which Sigma categories convert. A new team profile gets its own schema with no code change.
- **Extraction model:**
  - `claude-opus-5-5` at effort `medium` (`EXTRACTION_EFFORT`), with server-side refusal fallbacks (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`), because malware command lines can trip safety classifiers.
  - It only runs for articles that pass relevance.
  - The LLM timeout was raised to 300 s, and the stall threshold to 1800 s, which is above timeout × (retries + 1).
- **Evidence check:** each value needs a verbatim quote. The worker checks the quote against the article text (whitespace- and case-insensitive). Unverified items are kept but flagged (`evidence_verified=false`), shown with a warning in the UI, and will be excluded from rule generation. Duplicate values within a field are merged.
- **Data:** an `observables` table (migration 0005), returned with each run.
- **Hardening from review:**
  - **Only `<` is escaped** in the article sent to the model. That is enough to stop `</article>` breakout, and command lines keep `&`, `>` and `2>&1` intact. A model-echoed `&lt;` is turned back into `<`.
  - **Evidence counts as verified only if** the quote is in the article *and* contains the value.
  - **Duplicates are merged on whitespace- and case-normalised values. NUL is stripped.**
  - **After a refusal fallback**, the answer is read from the text after the last `fallback` block. The stage records the model that actually served it, and `fallback_from`.
  - **Profile field and tag names** can't shadow pydantic's model API (`model_*`, `copy`, …).
- **Known risk:** extraction is a non-streaming request with a 300 s timeout, and the SDK retries timeouts twice. A very long Opus response could be billed up to 3 times before the run fails. If real runs come near the limit, switch extraction to streaming.
- **Not verified yet:** real Opus extraction quality on real articles, which needs the API key.

**4 notes (profiles, runs, worker, relevance).**
- **The detection profile is seeded as v1** from `app/profiles/seed_data/detection.yaml`. Profile versions are immutable, and every run records the version it used. The relevance prompt is compiled from the profile's `audience`, `relevant_if` and `ignore_if`, and is stored on each stage result for the later "advanced view".
- **Run lifecycle:** queued → running → filtered_out | succeeded | failed.
  - The worker claims runs with `FOR UPDATE SKIP LOCKED` and commits the claim before calling the model, so no transaction is open during LLM calls.
  - A crash marks the run failed instead of leaving it stuck. So do Ctrl+C and SIGTERM, which are recorded as "Interrupted" before the worker exits. Re-queueing an article with an active run returns that run.
  - A partial unique index allows only one queued or running run per article and profile version, so concurrent requests cannot queue duplicates.
  - Each stage updates `heartbeat_at`. Before claiming, workers fail any `running` run with no progress for `RUN_STALE_AFTER_SECONDS` (default 1800, above one LLM call's worst case), so a killed worker never blocks an article.
  - `queue-runs` re-queues articles whose runs all failed.
- **LLM client:**
  - Uses `messages.create` with an `output_config.format` JSON schema, not `messages.parse`. `parse` validates JSON before `stop_reason` can be checked, so a refusal or a truncated response would surface as a pydantic error.
  - API errors keep the API's own message, capped at 300 characters.
  - Tested against canned responses through `httpx2.MockTransport` (`tests/test_anthropic_client.py`).
- **Article input:** XML-escaped, so it cannot close `<article>` and pose as instructions. Capped at 100k characters; the stage output records `article_truncated`.
- **Relevance:**
  - Uses `claude-haiku-5-5` at effort `low` with structured outputs (a JSON schema on `messages.create`). Scores are clamped to 0–10 and compared with the profile's `min_score` (6).
  - Refusals, API errors and output-limit hits fail the run with a readable message, never a secret.
  - Token usage is stored per stage.
- **Credentials:** only the worker builds an LLM client. With no key it refuses to start (exit 2, actionable message), and the API keeps working. `LLM_PROVIDER=offline` lets the UI and worker run end to end at no cost.
- **Portability:**
  - The worker is a second Container App with no ingress.
  - `ANTHROPIC_API_KEY` comes from a Container Apps secret or Key Vault reference.
  - Egress must allow `api.anthropic.com`, or the approved provider's endpoint once `build_llm_client` gains it.
  - Scaling to more than one worker is safe because of `SKIP LOCKED`.
- **Not verified yet:** a real model call. Needs `ANTHROPIC_API_KEY` in `api/.env`, then `uv run python -m app.cli queue-runs --limit 5` and `uv run python -m app.worker --once`.

**3 notes (fetch now → articles).**
- **Live check, 2026-11-21:** all 28 active seed sources fetched, giving about 750 articles. Only about 20 items were too short.
  - Two upstream feed URLs had moved. They are corrected in `SEED_FIXES` (`app/sources/seed.py`); the vendored YAML stays verbatim.
  - Summary-only feeds are handled with a plain article-page fetch, using the source's `extract.body_selectors`. The first selector whose text reaches `min_content_length` wins, because early generic selectors like `main` can match only a teaser. No Playwright is needed so far.
- **Per-fetch work is bounded** to the newest `max_items_per_fetch` items (default 50). Huntress returns its whole ~760-item archive.
- **Text is block-aware:** inline `<code>`, `<em>` and `<a>` stay inside their sentence. This keeps command lines intact for extraction in increment 5.
- **Dedup:** by canonical URL (fragment and tracking parameters removed), then by content hash. Both have unique constraints, and inserts use `ON CONFLICT DO NOTHING`. US-CERT items are correctly caught as copies of CISA posts.
- **Fetch failures** (HTTP status, not-a-feed, blocked destination) return 502 and set `source.last_error`. A failure on a single article page only counts that item as too short.
- **Hardening from review:**
  - **No transaction or row lock is held during network I/O.** Ingest runs in phases: read, then network, then a short write.
  - **One bad item never fails a fetch.** Malformed links are counted as filtered. NUL bytes are stripped. Each insert runs in a savepoint.
  - **The fetcher has an overall deadline** (2 × timeout), so slow-drip servers cannot hold a fetch open. It also has a 5 MB cap for article pages, separate from the 20 MB cap for feeds, and blocks NAT64, IPv4-compatible and site-local IPv6 addresses.
  - **HTML-to-text is a single linear walk.** A hostile 200k-element page takes about 3 s instead of hanging.
  - **Future publish dates are clamped to fetch time.**
- **`SEED_FIXES` only reaches databases seeded after it was added**, because seeding never updates rows. Older installs should edit those two sources in the UI.
- **Proxy note for the enterprise:** the SSRF guard resolves DNS locally even when `HTTPS_PROXY` is set. A container that can only resolve names through its proxy will fail every fetch with "Could not resolve". If the target environment works that way, add a setting that skips local resolution when a proxy is configured, and rely on proxy egress allow-listing as the control. Run `check-portability` on this at handoff.
- **Portability:**
  - Outbound HTTP honours `HTTPS_PROXY`/`NO_PROXY` (`trust_env`).
  - The SSRF guard refuses non-public addresses on every hop. DNS rebinding between the check and connect remains possible, so enterprise egress policy is the backstop.
  - Fetching is manual (API button or CLI). A scheduled Container Apps job running `python -m app.cli fetch --all-active` is the planned automation.
  - The SPA uses history routes (`/articles/:id`), so nginx in increment 9 needs `try_files $uri /index.html`.

**2b notes.** `PATCH /api/sources/{id}` and the Sources page (list, add, inline edit, active toggle). Migration 0002 enforces in the database that an active source has an RSS feed.

**2a notes.**
- Huntable `sources.yaml` is pinned at `904a6c4`: 38 sources, 28 seeded active (upstream active and has an RSS feed). The rest start inactive, with `config.upstream_active` recording the upstream flag.
- Seeding only inserts missing slugs and never updates a row, so upstream changes reach existing installs only through a deliberate migration or by editing them.
- `POST /api/sources` creates RSS sources and returns 409 when the name's slug is already taken.
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
| Models (decided 2026-11-21) | **Relevance: `claude-haiku-5-5`** (cheap triage, about $1 per 750 articles). **Extraction and rule generation: `claude-opus-5-5`.** Each stage's model is a setting, so it can change without code. JSON comes back through structured outputs (a JSON schema generated from pydantic, sent with `messages.create`). Opus calls use server-side refusal fallbacks, since threat content can trip cyber safety classifiers. |
| Run trigger | **Manual.** "Run detection" on an article, or a CLI batch. Nothing runs automatically after a fetch, so spend happens only on request. |
| Credentials | The user puts `ANTHROPIC_API_KEY` in `api/.env` (gitignored). The app never logs it. The test suite forces the fake LLM. |
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
- `observables`: run_id, field_name, value, evidence, evidence_verified, `tags` JSONB
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
| 5 | Extraction (done) | Observables are saved with their tags. Quotes not found in the article, or that do not contain the value, are kept but flagged unverified and excluded from rules. |
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
