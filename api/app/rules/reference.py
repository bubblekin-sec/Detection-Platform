"""Reference rulesets: folders of Sigma files that generated rules are cross-referenced against.

Locally the reference is the repo's own rules/ folder; at enterprise it is a checkout of the organisation's rules
repo (REFERENCE_RULE_DIRS). Files are untrusted input: only *.yml / *.yaml under the folder are read (no symlinks out
of it, no dot-directories such as .git), each at most MAX_FILE_BYTES, with YAML aliases refused (alias bombs) and a
cap on files and nodes. A file that breaks a rule is skipped and reported, never fatal.

Matching reuses the duplicate check (app/detection/dedup.py): same log source and near-identical detection values.
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.detection.dedup import Fingerprint, duplicate_similarity, fingerprint_document

MAX_FILE_BYTES = 64 * 1024
MAX_FILES = 20_000
MAX_EVENTS = 20_000  # YAML parse events per file
MAX_DEPTH = 64  # nesting levels; real Sigma rules use fewer than 10


@dataclass
class ReferenceRule:
    path: str  # "<folder name>/<path inside it>"
    rule_id: str | None
    title: str | None
    fingerprint: Fingerprint


@dataclass
class ReferenceSet:
    rules: list[ReferenceRule] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)


@dataclass
class Match:
    path: str
    rule_id: str | None
    title: str | None
    similarity: float


def load_reference_rules(folders: Iterable[Path]) -> ReferenceSet:
    result = ReferenceSet()
    seen = 0
    for folder in folders:
        root = folder.resolve()
        if not root.is_dir():
            result.skipped.append((str(folder), "not a folder"))
            continue
        for directory, subdirectories, files in os.walk(root, followlinks=False):
            subdirectories[:] = sorted(name for name in subdirectories if not name.startswith("."))
            for name in sorted(files):
                if not name.lower().endswith((".yml", ".yaml")):
                    continue
                seen += 1
                if seen > MAX_FILES:
                    result.skipped.append((str(root), f"more than {MAX_FILES} rule files; the rest were not read"))
                    return result
                _load_file(root, Path(directory) / name, result)
    return result


def best_match(own: Fingerprint | None, rule_id: str, references: ReferenceSet) -> Match | None:
    """The most similar reference rule, never the rule's own file (same Sigma id)."""
    best: Match | None = None
    for reference in references.rules:
        if reference.rule_id == rule_id:
            continue
        similarity = duplicate_similarity(own, reference.fingerprint)
        if similarity is not None and (best is None or similarity > best.similarity):
            best = Match(reference.path, reference.rule_id, reference.title, similarity)
    return best


def _load_file(root: Path, path: Path, result: ReferenceSet) -> None:
    label = f"{root.name}/{path.relative_to(root).as_posix()}"
    try:
        resolved = path.resolve()
        if root not in resolved.parents:
            result.skipped.append((label, "points outside the folder"))
            return
        if resolved.stat().st_size > MAX_FILE_BYTES:
            result.skipped.append((label, f"larger than {MAX_FILE_BYTES // 1024} KB"))
            return
        text = resolved.read_text(encoding="utf-8")
        depth = 0
        for count, event in enumerate(yaml.parse(text, Loader=yaml.SafeLoader), start=1):
            if isinstance(event, yaml.AliasEvent):
                result.skipped.append((label, "uses YAML aliases"))
                return
            if count > MAX_EVENTS:
                result.skipped.append((label, "too large to parse"))
                return
            # Loading (and fingerprinting) recurse per level: refuse deep nesting before it can hit the stack limit.
            depth += isinstance(event, yaml.CollectionStartEvent) - isinstance(event, yaml.CollectionEndEvent)
            if depth > MAX_DEPTH:
                result.skipped.append((label, "nested too deeply"))
                return
        documents = [document for document in yaml.safe_load_all(text) if isinstance(document, dict)]
        fingerprints = [(document, fingerprint_document(document)) for document in documents]
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError) as error:
        result.skipped.append((label, f"unreadable ({type(error).__name__})"))
        return
    for document, own in fingerprints:
        if own is None:
            continue  # no detection: e.g. an `action: global` template or another kind of YAML
        result.rules.append(ReferenceRule(
            path=label,
            rule_id=str(document["id"]) if document.get("id") is not None else None,
            title=str(document["title"]) if document.get("title") is not None else None,
            fingerprint=own,
        ))
