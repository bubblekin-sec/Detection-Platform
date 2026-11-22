# Approved detection rules

Rules approved in the Detection Platform's Review screen, written here by:

```sh
cd api
uv run python -m app.cli sync-rules        # or --dir <another rules folder>
```

Nothing is committed or pushed automatically: review the diff, then commit.

## Layout

Each rule is three files under `<product>/<logsource category>/<title>-<first 8 characters of its id>`:

| File | Contents |
| --- | --- |
| `.yml` | The Sigma rule of record, exactly as `GET /api/rules/export` publishes it. Its `id` is stable across edits and exports; `references` holds the source article. |
| `.kql` | The Defender XDR query to deploy: the reviewer's hand-edited KQL if current, else the model-written query, else the converted one. Missing when no current query passed its checks (the `.meta.json` says why). |
| `.meta.json` | Provenance: article, pipeline run, profile version, model, the query's origin, warnings and the Defender tables it reads, any likely duplicate, and the review (including the reviewer's note, unless synced with `--without-notes`). |

`.platform-manifest.json` lists every file the platform wrote, with its hash.

## Editing

Edit rules in the platform's Review screen, then sync again. The sync never overwrites a file you changed by hand and
never deletes a file it didn't write; it reports those as conflicts. A rule that is rejected or sent back for review is
removed on the next sync. Other files in this folder (your own rules, notes) are left alone.
