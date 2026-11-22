"""Write approved rules into a rules folder (the repo's `rules/` locally, the enterprise rules repo later).

Each approved, exportable rule becomes three files under `<product>/<category>/<slug>-<id8>`:
- `.yml`: the Sigma document, exactly as `GET /api/rules/export` publishes it;
- `.kql`: the deployable Defender XDR query (non-stale and ok: hand-edited, else model-written, else converted);
- `.meta.json`: provenance (article, run, profile, model, review, duplicate, tables read).

`.platform-manifest.json` records every file the platform wrote and its sha256. A sync writes only files whose bytes
changed, removes managed files for rules no longer approved, and never touches a file it didn't write: a managed file
edited by hand, or a hand-added file in a rule's path, is reported as a conflict and left alone. Output is
deterministic (no timestamps, sorted keys, LF), so the same rules give the same bytes and git diffs stay meaningful.
Nothing is committed or pushed: that is the user's step (or a pipeline's, at enterprise).
"""

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import UTC
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.detection.kql_schema import check_kql
from app.detection.rules import single_mapping
from app.models import Article, DetectionRule, PipelineRun, ProfileVersion, RuleQuery, StageResult
from app.rules.export import exportable_document

MANIFEST = ".platform-manifest.json"
KQL_PRECEDENCE = {"human_edit": 0, "llm": 1, "converted": 2}
MAX_SLUG = 60


class SyncRefused(Exception):
    """Nothing was written: the folder is missing, or its manifest can't be trusted."""


RulesDirMissing = SyncRefused  # earlier name


@dataclass
class SyncResult:
    written: list[str] = field(default_factory=list)
    unchanged: int = 0
    removed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # "path: reason"
    left_out: list[str] = field(default_factory=list)  # rule ids that no longer validate


def sync_rules(
    session: Session, root: Path, inventory: frozenset[str] = frozenset(), *, include_notes: bool = True
) -> SyncResult:
    if not root.is_dir():
        raise SyncRefused(f"{root} is not a folder. Create it, or pass --dir / set RULES_DIR.")
    root = root.resolve()
    manifest = _read_manifest(root)  # checked before anything is written
    result = SyncResult()
    desired = _desired_files(session, result, inventory, include_notes)
    new_manifest: dict[str, str] = {}

    for _base, rule_files in sorted(desired.items()):
        # A rule's files go together: if any of them is in conflict, none is rewritten, so the folder never pairs
        # one version's Sigma with another's KQL.
        problems = [
            problem for relative, content in rule_files.items()
            if (problem := _conflict(root, relative, content, manifest))
        ]
        if problems:
            result.conflicts.extend(problems)
            new_manifest.update({relative: manifest[relative] for relative in rule_files if relative in manifest})
            continue
        for relative, content in sorted(rule_files.items()):
            path = _inside(root, relative)
            if path.is_file() and path.read_bytes() == content:
                result.unchanged += 1
            else:
                _write(path, content)
                result.written.append(relative)
            new_manifest[relative] = _sha(content)

    wanted = {relative for rule_files in desired.values() for relative in rule_files}
    for relative, digest in sorted(manifest.items()):
        if relative in wanted:
            continue
        path = _inside(root, relative)
        if not path.is_file():
            continue
        if _sha(path.read_bytes()) != digest:
            result.conflicts.append(f"{relative}: edited by hand, and its rule is no longer approved (left in place)")
            new_manifest[relative] = digest
            continue
        path.unlink()
        result.removed.append(relative)
        _prune_empty_dirs(path.parent, root)

    _write_manifest(root, new_manifest)
    return result


def _conflict(root: Path, relative: str, content: bytes, manifest: dict[str, str]) -> str | None:
    path = _inside(root, relative)
    if not path.is_file():
        return None
    on_disk = path.read_bytes()
    if on_disk == content:
        return None
    if relative not in manifest:
        return f"{relative}: a file the platform didn't write is in this rule's place"
    if _sha(on_disk) != manifest[relative]:
        return f"{relative}: edited by hand since the last sync (edit the rule in Review)"
    return None


def _desired_files(
    session: Session, result: SyncResult, inventory: frozenset[str], include_notes: bool
) -> dict[str, dict[str, bytes]]:
    """Each rule's files, keyed by the rule's base path."""
    rows = session.execute(
        select(DetectionRule, Article)
        .join(Article, Article.id == DetectionRule.article_id)
        .where(DetectionRule.status == "approved")
        .options(selectinload(DetectionRule.queries))
        .order_by(DetectionRule.created_at, DetectionRule.run_id, DetectionRule.position, DetectionRule.id)
    ).all()
    runs = _runs(session, {rule.run_id for rule, _ in rows})
    files: dict[str, dict[str, bytes]] = {}
    for rule, article in rows:
        sigma = exportable_document(rule, article.url)
        if sigma is None:
            result.left_out.append(rule.id)
            continue
        document = single_mapping(sigma) or {}
        source = document.get("logsource") if isinstance(document.get("logsource"), dict) else {}
        product = _slug(source.get("product"), keep="_") or "any"
        folder = f"{product}/{_slug(source.get('category') or source.get('service'), keep='_') or 'other'}"
        base = f"{folder}/{_slug(rule.title) or 'rule'}-{rule.id[:8]}"
        if base in files:  # same title and id prefix: vanishingly rare, but never overwrite silently
            result.conflicts.append(f"{base}.yml: two approved rules map to this path ({rule.id} left out)")
            continue
        query = deployable_query(rule)
        rule_files = {f"{base}.yml": (sigma + "\n").encode()}
        if query is not None:
            # LF only: git (eol=lf) would otherwise store different bytes than the manifest records.
            rule_files[f"{base}.kql"] = ("\n".join(query.query_text.strip().splitlines()) + "\n").encode()
        meta = _meta(rule, article, runs.get(rule.run_id), query, inventory, include_notes)
        rule_files[f"{base}.meta.json"] = _json(meta)
        files[base] = rule_files
    return files


def deployable_query(rule: DetectionRule) -> RuleQuery | None:
    """The Defender XDR query to deploy: current (not stale) and ok, preferring a reviewer's hand edit."""
    usable = [
        query for query in rule.queries
        if query.target == "defender_xdr" and query.status == "ok" and query.query_text
        and query.source_sigma_hash == rule.sigma_hash
    ]
    return min(usable, key=lambda query: (KQL_PRECEDENCE.get(query.origin, 9), -(query.id or 0)), default=None)


def _meta(
    rule: DetectionRule, article: Article, run: dict[str, Any] | None, query: RuleQuery | None, inventory: frozenset[str],
    include_notes: bool = True,
) -> dict[str, Any]:
    tables = check_kql(query.query_text).tables if query else []
    return {
        "rule_id": rule.id,
        "title": rule.title,
        "level": rule.level,
        "status": rule.status,
        "sigma_sha256": rule.sigma_hash,
        "article": {"id": article.id, "url": article.url, "title": article.title},
        "run_id": rule.run_id,
        "profile": run["profile"] if run else None,
        "rules_model": run["rules_model"] if run else None,
        "kql": {
            "origin": query.origin,
            "llm_model": query.llm_model,
            "warnings": list(query.warnings or []),
            "tables_read": tables,
            # Not collected per TELEMETRY_TABLES: the query would find nothing there.
            "tables_not_in_inventory": [table for table in tables if inventory and table not in inventory],
        } if query else None,
        "kql_missing_reason": None if query else "No current Defender XDR query passed its checks; see the rule in Review.",
        "duplicate_of": {"id": rule.duplicate_of, "similarity": rule.duplicate_similarity} if rule.duplicate_of else None,
        # UTC whatever the database session's time zone, so the bytes don't depend on where the sync runs.
        "reviewed_at": rule.reviewed_at.astimezone(UTC).isoformat() if rule.reviewed_at else None,
        "reviewer_note": rule.reviewer_note if include_notes else None,
    }


def _runs(session: Session, run_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not run_ids:
        return {}
    runs = session.scalars(
        select(PipelineRun).where(PipelineRun.id.in_(run_ids))
        .options(joinedload(PipelineRun.profile_version).joinedload(ProfileVersion.profile))
    ).all()
    models = dict(session.execute(
        select(StageResult.run_id, StageResult.model)
        .where(StageResult.run_id.in_(run_ids), StageResult.stage == "rule_generation")
    ).all())
    return {
        run.id: {
            "profile": {"slug": run.profile_version.profile.slug, "version": run.profile_version.version},
            "rules_model": models.get(run.id),
        }
        for run in runs
    }


def _slug(value: Any, keep: str = "") -> str:
    """ASCII, lowercase, [a-z0-9-] (plus `keep`, e.g. `_` for Sigma's process_creation) only: model-written titles
    can't create paths or odd filenames."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().casefold()
    return re.sub(rf"[^a-z0-9{re.escape(keep)}]+", "-", text).strip("-_")[:MAX_SLUG].rstrip("-_")


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f"Refusing a path outside the rules folder: {relative}")
    return path


def _read_manifest(root: Path) -> dict[str, str]:
    """The previous sync's files. A manifest that can't be trusted stops the sync before it writes anything: falling
    back to an empty one would orphan the files of rules that are no longer approved."""
    path = root / MANIFEST
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SyncRefused(f"{path} is not valid JSON ({error}); resolve it (e.g. a merge conflict) and sync again.") from error
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise SyncRefused(f'{path} has no "files" mapping; restore it from git and sync again.')
    for name in files:
        try:
            _inside(root, str(name))
        except ValueError as error:
            raise SyncRefused(f"{path} lists a path outside the rules folder: {name}") from error
    return {str(name): str(digest) for name, digest in files.items()}


def _write_manifest(root: Path, files: dict[str, str]) -> None:
    content = _json({
        "about": "Files written by the Detection Platform (cli sync-rules). Edit rules in Review, not here.",
        "files": dict(sorted(files.items())),
    })
    path = root / MANIFEST
    if not path.is_file() or path.read_bytes() != content:
        _write(path, content)


def _json(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write(path: Path, content: bytes) -> None:
    """Atomic replace through a new, randomly named temp file (O_EXCL), so a planted symlink at a predictable temp
    name can't redirect the write outside the folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _prune_empty_dirs(folder: Path, root: Path) -> None:
    while folder != root and root in folder.parents and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent
