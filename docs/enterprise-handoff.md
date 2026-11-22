# Enterprise handoff

What changes when the platform moves from this repository to the enterprise environment (Azure Container Apps), and
what stays the same. Its connections (database, model, rules folders, telemetry, upstreams) are pointed at enterprise resources by
settings; the items under "Not built yet" need code. Instructions specific to the enterprise (registries, identities, repositories) are expected at handoff time; this
lists the inputs they need to supply.

## Workloads

| Workload | Image and command | In Azure Container Apps |
| --- | --- | --- |
| ui | `ui/Dockerfile` (nginx, port 8080) | External ingress. `API_UPSTREAM=https://<api-app>.internal.<environment domain>`. Probes on `/healthz`. |
| api | `api/Dockerfile` (uvicorn, port 8000) | Internal ingress. Liveness `/api/health`, readiness `/api/ready`. No model key. |
| worker | same image, `python -m app.worker` | No ingress, min replicas 1 (more are safe). Holds the model key. Without a running worker, queued runs wait (the article page says so). |
| migrate | same image, `alembic upgrade head && python -m app.cli seed-sources && python -m app.cli seed-profiles` | A Container Apps Job run by the deploy pipeline before a new api or worker revision. |
| sync-rules | same image, `python -m app.cli sync-rules --dir <rules checkout>` | A pipeline job (or Container Apps Job) with database access and a checkout of the rules repository. It writes files; committing them is the pipeline's step. |

Details, probes and nginx notes are in [plans/intel-to-detection.md](plans/intel-to-detection.md) ("Increment 9 notes").

## Settings to supply

All settings are read in `api/app/config.py` (`Settings`), from the environment or `api/.env` locally.

| Setting | Local default | Enterprise value |
| --- | --- | --- |
| `DATABASE_URL` | compose Postgres on 127.0.0.1 | Azure Database for PostgreSQL (version 17), `sslmode=require`, as a secret reference. |
| `ANTHROPIC_API_KEY` (worker only) | `api/.env` | Key Vault or Container Apps secret reference. An approved enterprise model provider would replace this (`LLMClient` in `app/llm/`). |
| `RELEVANCE_MODEL`, `EXTRACTION_MODEL`, `RULES_MODEL` | Haiku 5.5, Opus 5.5, Opus 5.5 | Whatever the enterprise approves. |
| `RULES_DIR` | `../rules` (this repo's `rules/`) | A checkout of the enterprise rules repository, in the sync job. |
| `REFERENCE_RULE_DIRS` | empty, meaning `RULES_DIR` (our own approved rules) | Comma-separated checkouts of the organisation's existing Sigma rulesets, in the job that runs `crossref-rules`. |
| `TELEMETRY_TABLES` | empty, meaning the seven Device* event tables | The Defender XDR tables the organisation actually collects, on the api, the worker and the sync-rules job (so `.meta.json` agrees with Review). Unknown names fail at startup. |
| `API_UPSTREAM` (ui) | `http://api:8000` in compose | The api app's internal https FQDN. |
| `AUTO_QUEUE_MAX_PER_DAY` | 25 | The daily cap on automatically queued runs (0 turns automatic runs off). |

## Cross-referencing against existing rules and telemetry

Generated rules are checked against what the organisation already has, in three places:

1. **Existing rulesets.** `uv run python -m app.cli crossref-rules [--dir <ruleset> ...] [--status approved] [--json]`
   reports, for each platform rule, the closest rule in the reference folders (same log source and near-identical
   detection values; the same thresholds as duplicate detection). It writes nothing. Point `REFERENCE_RULE_DIRS` at the
   enterprise ruleset checkout. Reference files are treated as untrusted: only `*.yml`/`*.yaml`, at most 64 KB each,
   no YAML aliases, no symlinks out of the folder, no dot-directories.
   - The matcher reads **Sigma**. If the enterprise ruleset is Defender custom detections (KQL) or Sentinel analytics
     rules, it needs a converter to Sigma-like fingerprints first; that is not built yet.
   - A reference rule with the same Sigma `id` as a platform rule is treated as that rule's own exported file and
     never reported as a match. An enterprise copy kept under the same id but modified is therefore not reported.
   - It exits 2 when a reference folder is missing, so "no overlap" never comes from reading nothing.
   - Logsources must match exactly (`category`, `product`, `service`); rulesets using other conventions (for example
     `service: sysmon`) won't match until they are normalised.
2. **Rules generated earlier in the platform.** New rules are flagged as likely duplicates when saved
   (`duplicate_of`); `cli dedup-rules` recomputes everything.
3. **Available telemetry.** Each Defender query lists the tables it reads that are not in `TELEMETRY_TABLES` (Review
   shows "Not collected"), the rules folder's `.meta.json` records them as `tables_not_in_inventory`, and the model is
   only offered the inventory's tables when it writes KQL. Columns and ActionTypes are not inventoried yet.

## Rules repository

`cli sync-rules` writes each approved rule as `<product>/<category>/<title>-<id8>.yml` (Sigma, exactly as exported),
`.kql` (the deployable Defender query) and `.meta.json` (provenance), plus `.platform-manifest.json`. It is deterministic,
removes only files it wrote, and reports hand edits as conflicts (exit code 1) instead of overwriting them; a missing
folder or an untrustworthy manifest (for example a merge conflict) stops it before it writes anything (exit code 2). See
[rules/README.md](../rules/README.md). Nothing is pushed by the platform; there is no GitHub token in the app.

`.meta.json` includes the reviewer's note. Use `sync-rules --without-notes` for a repository with wider visibility.

## Not built yet (decide at handoff)

- Authentication (Entra ID) and per-user review attribution.
- Pushing rules to a repository or SIEM, or opening pull requests.
- Scheduled feed fetching (a Container Apps Job running `cli fetch --all-active` would do it).
- KQL syntax validation against a live Defender tenant.
- A Container Apps–native model provider (e.g. Azure OpenAI) behind `LLMClient`.
