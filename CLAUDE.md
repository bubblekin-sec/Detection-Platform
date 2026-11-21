# Project context

This personal project is intended to move into an enterprise environment. The deployment target is a React UI served by nginx and a FastAPI backend, each in its own Azure Container App. Check the repository before describing any part of that target as implemented.

Use synthetic examples and developer-controlled dependencies for local work. Introduce SQL, messaging, or an external API when a feature actually needs it. Keep the application and its tests runnable with ordinary project commands.

## Working commands

Use the package manifests, lockfiles, contributor guide, and CI configuration to find the supported commands and their working directories. Once verified, record them here or link to the existing authoritative guide. On an empty repo, record the first working build and test commands as part of the first feature.

Feature plan: [docs/plans/intel-to-detection.md](docs/plans/intel-to-detection.md).

### API (`api/`, Python 3.13, uv)

| Task | Command (run in `api/`) | Status |
| --- | --- | --- |
| Install deps | `uv sync` | verified |
| Tests | `uv run pytest` | verified |
| Run locally | `uv run uvicorn app.main:app --reload` (http://localhost:8000/api/health, docs at `/docs`) | verified |
| Container image | `docker build -t detection-api .` then `docker run --rm -p 8000:8000 detection-api` | verified (runs as uid 10001) |

- `pysigma` is held at `>=1.5,<2` because `pysigma-backend-kusto` 1.x (the Defender XDR converter) requires pySigma 1.x. Move to pySigma 2.x only when the backend supports it, and re-run the tests. Older backends (0.4.x) declare `pysigma>=0.10` but break on 1.x and 2.x, so never leave the backend unpinned.
- Sigma validation lives only in `app/detection/sigma.py`; Defender XDR KQL conversion only in `app/detection/kql.py`.
