# Project context

This personal project is intended to move into an enterprise environment. The deployment target is a React UI served by nginx and a FastAPI backend, each in its own Azure Container App. Check the repository before describing any part of that target as implemented.

Use synthetic examples and developer-controlled dependencies for local work. Introduce SQL, messaging, or an external API when a feature actually needs it. Keep the application and its tests runnable with ordinary project commands.

## Working commands

Use the package manifests, lockfiles, contributor guide, and CI configuration to find the supported commands and their working directories. Once verified, record them here or link to the existing authoritative guide. On an empty repo, record the first working build and test commands as part of the first feature.

Feature plan: [docs/plans/intel-to-detection.md](docs/plans/intel-to-detection.md).

### API (`api/`, Python 3.13, uv)

| Task | Command (run in `api/`) | Status |
| --- | --- | --- |
| Start Postgres (repo root) | `docker compose up -d --wait db` (creates `detection` and `detection_test`) | verified |
| Install deps | `uv sync` | verified |
| Migrate | `uv run alembic upgrade head` | verified |
| Seed sources | `uv run python -m app.cli seed-sources` (idempotent; only adds missing slugs) | verified |
| Fetch feeds | `uv run python -m app.cli fetch --source <slug>` (repeatable) or `--all-active`. Makes real outbound requests; exits 1 if any feed fails | verified |
| Seed profiles | `uv run python -m app.cli seed-profiles` (adds the `detection` profile v1 if missing) | verified |
| Priority report | `uv run python -m app.cli priority-report [--drop TERM]` (scores every stored article with the Huntable-seeded keyword lists; writes nothing) | verified |
| Score articles | `uv run python -m app.cli score-articles [--profile detection] [--missing]` (stores priority scores for the profile's latest version; `--missing` also refreshes scores from an older scorer; never queues runs). API: `POST /api/profiles/{slug}/rescore` | verified |
| Queue runs (batch) | `uv run python -m app.cli queue-runs [--source <slug>] [--min-priority <score>] [--limit 20]` (articles with no successful run yet; `--min-priority` takes the highest scores first) | verified |
| Worker | `uv run python -m app.worker` (polls) or `--once` (drains the queue, then exits). Needs `ANTHROPIC_API_KEY` in `api/.env`; `LLM_PROVIDER=offline` runs without a model and fails each stage with an explanation | verified (offline + missing-key paths) |
| Tests | `uv run pytest` (needs the compose db; migrates and truncates `detection_test`, exits with code 2 if unreachable) | verified |
| New migration | `uv run alembic revision --autogenerate -m "<change>" --rev-id <NNNN>`, then review the generated file | verified |
| Run locally | `uv run uvicorn app.main:app --reload` (http://localhost:8000/api/health, docs at `/docs`) | verified |
| Container image | `docker build -t detection-api .` then `docker run --rm -p 8000:8000 detection-api` | verified (runs as uid 10001) |

- `pysigma` is held at `>=1.5,<2` because `pysigma-backend-kusto` 1.x (the Defender XDR converter) requires pySigma 1.x. Move to pySigma 2.x only when the backend supports it, and re-run the tests. Older backends (0.4.x) declare `pysigma>=0.10` but break on 1.x and 2.x, so never leave the backend unpinned.
- Sigma validation lives only in `app/detection/sigma.py`; Defender XDR KQL conversion only in `app/detection/kql.py`.
- Export: `GET /api/rules/export` (`app/rules/export.py`) returns every approved rule as one multi-document Sigma YAML file, each re-validated and keeping its stored id and article reference. A rule that no longer validates is left out and named in a header comment. KQL artifacts are not exported yet.
- KQL artifacts follow the profile's `query_strategy` (`app/pipeline/rule_generation.py`): `converted` never calls a model; the default `converted_with_llm_fallback` asks `RULES_MODEL` for KQL only when conversion fails. Model-written KQL is checked against the Defender XDR catalog in `app/detection/kql_schema.py` (unknown tables or columns make the artifact `failed`).
- Settings come from env or `api/.env` (see `api/.env.example`). Use `127.0.0.1`, not `localhost`, in local database URLs: on Windows, `localhost` tries IPv6 first and adds about 5 seconds to every connection.
- The database engine is built only in `app/db.py` `make_engine()`. Tests use the same function.
- Seed sources come from the vendored Huntable list. Its provenance and pinned commit are in `THIRD_PARTY_NOTICES.md`. Never edit the vendored YAML; put corrections in `SEED_FIXES` in `app/sources/seed.py`.
- LLM calls go only through the `LLMClient` interface (`app/llm/client.py`), built only by `app/llm/factory.py` `build_llm_client()`, and only the worker builds one. Tests use `tests/fakes.py` `ScriptedLLM` and must never call a real model. Per-stage model settings: `RELEVANCE_MODEL` (default `claude-haiku-5-5`), `EXTRACTION_MODEL` and `RULES_MODEL` (default `claude-opus-5-5`).
- Priority gate: new articles are scored at fetch time (`app/pipeline/priority.py`, keyword lists from the profile). Those at or above the profile's `priority.threshold` are auto-queued (`trigger=auto`) up to `AUTO_QUEUE_MAX_PER_DAY` (default 25, 0 = off). Below the threshold, no model is ever called unless someone clicks Run detection.
- Prompts are compiled from profile definitions in `app/profiles/compile.py`. Article text is untrusted: it goes in the user message inside `<article>` tags, and the system prompt says to ignore instructions inside it.
- All outbound HTTP goes through `app/sources/fetch.py` (SSRF guard: public addresses only, re-checked on each redirect, size cap). It is built only by `build_fetcher(settings)`; tests inject `tests/fakes.py` `FakeFetcher` via `create_app(settings, fetcher=...)`. Tests must never reach the network.

### UI (`ui/`, React 19 + Vite 8 + TypeScript 7, npm)

| Task | Command (run in `ui/`) | Status |
| --- | --- | --- |
| Install deps | `npm ci` | verified |
| Tests | `npm test` (vitest + Testing Library, jsdom; `fetch` stubbed via `src/test/fakeApi.ts`) | verified |
| Typecheck | `npm run typecheck` | verified |
| Lint | `npm run lint` (oxlint) | verified |
| Build | `npm run build` (outputs to `dist/`) | verified |
| Dev server | `npm run dev` (http://localhost:5173; proxies `/api` to `http://127.0.0.1:8000`, override with `API_PROXY_TARGET`) | verified |

- The UI calls only the same-origin `/api` path. Never put an API hostname into the bundle. In the container, nginx is planned to proxy `/api` (increment 9). It does not exist yet.
- All API calls go through `src/api.ts`. 4xx responses arrive as `{"detail": {"errors": ["field: message"]}}`; `ApiError.byField()` maps them onto form fields.
