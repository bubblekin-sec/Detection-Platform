"""Turning a model's Sigma draft into a rule of record: metadata the platform owns is set here, never by the model."""

from datetime import date
from typing import Any

import yaml
from sigma.rule.base import SigmaYAMLLoader

# Sigma's conventional key order, so stored and exported YAML reads naturally.
KEY_ORDER = [
    "title", "id", "status", "description", "references", "author", "date", "tags",
    "logsource", "detection", "falsepositives", "level",
]


IDENTITY_ERROR = "The rule must be a single YAML document (extra documents or duplicate keys found)."


def identity_error(parsed_rule, rule_id: str) -> str | None:
    """A rule is accepted only if its Sigma id is the platform's id. pySigma merges `action: global` documents into
    the next one, so a multi-document text can parse as one rule with a different (or no) id."""
    return None if str(parsed_rule.id) == rule_id else IDENTITY_ERROR


def pin_platform_fields(sigma_yaml: str, *, rule_id: str, reference_url: str) -> str:
    """For a reviewer's edit: keep their text as written, but the id stays the rule's id and the article stays
    in `references` (provenance). Rewrites the YAML only when one of those needs fixing. Text that is not exactly one
    mapping is returned unchanged for the validator to report.
    """
    try:
        documents = [doc for doc in yaml.load_all(sigma_yaml, Loader=SigmaYAMLLoader) if doc is not None]
    except yaml.YAMLError:
        return sigma_yaml
    if len(documents) != 1 or not isinstance(documents[0], dict):
        return sigma_yaml
    document = documents[0]
    references = document.get("references")
    references = list(references) if isinstance(references, list) else ([references] if references else [])
    if str(document.get("id", "")) == rule_id and reference_url in references:
        return sigma_yaml
    document["id"] = rule_id
    if reference_url not in references:
        document["references"] = [reference_url, *references]
    ordered: dict[str, Any] = {key: document[key] for key in KEY_ORDER if key in document}
    ordered.update({key: value for key, value in document.items() if key not in ordered})
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=120)


def finalize_sigma(sigma_yaml: str, *, rule_id: str, reference_url: str, today: date) -> str:
    """Set id, status, references and date on a single-rule draft.

    Loads with pySigma's own loader (which rejects duplicate keys) and tolerates empty extra documents. Anything that
    is not exactly one mapping, or has duplicate keys, is returned unchanged so the validator reports it and the
    model gets a repair attempt: finalizing must never hide a problem by re-serializing it away.
    """
    try:
        documents = [doc for doc in yaml.load_all(sigma_yaml, Loader=SigmaYAMLLoader) if doc is not None]
    except yaml.YAMLError:
        return sigma_yaml
    if len(documents) != 1 or not isinstance(documents[0], dict):
        return sigma_yaml
    document = documents[0]
    document.pop("author", None)
    document.update(
        {"id": rule_id, "status": "experimental", "references": [reference_url], "date": today.isoformat()}
    )
    ordered: dict[str, Any] = {key: document[key] for key in KEY_ORDER if key in document}
    ordered.update({key: value for key, value in document.items() if key not in ordered})
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=120)
