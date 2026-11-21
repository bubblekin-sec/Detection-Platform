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
