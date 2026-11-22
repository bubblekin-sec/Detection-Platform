"""Approved rules as one multi-document Sigma YAML file.

Each rule is exported as stored, with line breaks normalised and any BOM removed: the reviewer's text (comments
included) with the platform's id and article reference, which every save pins. Text carrying its own document
markers or directives (as YAML's parser sees them, whatever the line breaks) is re-serialized from the loaded rule,
losing only comments, so each rule is exactly one document in the file. Every rule is re-validated first; one that no longer validates (e.g. after a pySigma upgrade), or has lost
its article reference, is left out and named in a header comment, never silently dropped.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import yaml
from sigma.rule.base import SigmaYAMLLoader

from app.detection.rules import KEY_ORDER, identity_error, single_mapping
from app.detection.sigma import parse_sigma
from app.models import DetectionRule


@dataclass
class Export:
    documents: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)

    def text(self) -> str:
        noun = "rule" if len(self.documents) == 1 else "rules"
        header = [f"# {len(self.documents)} approved {noun}, exported from the Detection Platform."]
        header += [f"# Left out (no longer validates): {rule_id}" for rule_id in self.left_out]
        body = "\n---\n".join(self.documents)
        return "\n".join(header) + ("\n---\n" + body + "\n" if body else "\n")


def export_rules(rules: Iterable[tuple[DetectionRule, str]]) -> Export:
    """`rules` pairs each rule with its article URL, which must still be in the rule's references."""
    result = Export()
    for rule, article_url in rules:
        document = exportable_document(rule, article_url)
        if document is None:
            result.left_out.append(rule.id)
        else:
            result.documents.append(document)
    return result


def exportable_document(rule: DetectionRule, article_url: str) -> str | None:
    """The rule as one Sigma document ready to publish (no trailing newline), or None when it no longer validates
    or has lost its article reference. Shared by the YAML export and the rules folder sync."""
    parsed = parse_sigma(rule.sigma_yaml)
    document = single_mapping(rule.sigma_yaml)
    if (
        parsed.rule is None
        or document is None
        or identity_error(parsed.rule, rule.id, rule.sigma_yaml)
        or article_url not in [str(reference) for reference in parsed.rule.references]
    ):
        return None
    return _as_document(rule.sigma_yaml, document)


def _as_document(sigma_yaml: str, document: dict[str, Any]) -> str:
    text = "\n".join(sigma_yaml.removeprefix("﻿").splitlines()).rstrip()
    if not _has_markers(text):
        return text
    ordered: dict[str, Any] = {key: document[key] for key in KEY_ORDER if key in document}
    ordered.update({key: value for key, value in document.items() if key not in ordered})
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=120).rstrip()


def _has_markers(text: str) -> bool:
    """`---`, `...` or a directive: concatenated as is, they would add empty documents to the file."""
    for event in yaml.parse(text, Loader=SigmaYAMLLoader):
        if isinstance(event, yaml.DocumentStartEvent) and (event.explicit or event.version or event.tags):
            return True
        if isinstance(event, yaml.DocumentEndEvent) and event.explicit:
            return True
    return False
