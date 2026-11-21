"""Sigma parsing and validation. The only module that decides whether Sigma is valid."""

import logging
from dataclasses import dataclass, field

import yaml
from sigma.collection import SigmaCollection
from sigma.exceptions import SigmaError
from sigma.rule import SigmaRule
from sigma.rule.base import SigmaYAMLLoader

logger = logging.getLogger(__name__)


@dataclass
class SigmaParseResult:
    rule: SigmaRule | None
    errors: list[str] = field(default_factory=list)


def _invalid(*errors: str) -> SigmaParseResult:
    return SigmaParseResult(rule=None, errors=list(dict.fromkeys(errors)))


def parse_sigma(sigma_yaml: str) -> SigmaParseResult:
    """Parse and fully validate exactly one Sigma detection rule, collecting every error found."""
    try:
        # SigmaYAMLLoader rejects duplicate keys, which plain safe_load silently collapses.
        documents = [doc for doc in yaml.load_all(sigma_yaml, Loader=SigmaYAMLLoader) if doc is not None]
    except yaml.YAMLError as error:
        return _invalid(f"Invalid YAML: {error}")

    for number, document in enumerate(documents, start=1):
        if not isinstance(document, dict):
            return _invalid(f"Document {number} is not a mapping.")

    try:
        collection = SigmaCollection.from_dicts(documents, collect_errors=True)
    except SigmaError as error:
        return _invalid(str(error))
    except (AttributeError, TypeError, ValueError, KeyError) as error:
        # pySigma does not type-check every field (e.g. `detection: foo`) before indexing into it.
        logger.info("Unstructured Sigma input rejected: %r", error)
        return _invalid(f"Invalid Sigma structure: {error}")

    if collection.errors:
        return _invalid(*(str(error) for error in collection.errors))

    unsupported = [item for item in collection.rules if not isinstance(item, SigmaRule)]
    if unsupported:
        kinds = sorted({type(item).__name__ for item in unsupported})
        return _invalid(f"Only detection rules are supported; filter/correlation documents are not supported ({', '.join(kinds)}).")
    if len(collection.rules) != 1:
        return _invalid(f"Expected exactly one Sigma rule, found {len(collection.rules)}.")

    rule = collection.rules[0]
    # Conditions parse lazily; parse now so a broken condition is a validation error, not a conversion error.
    condition_errors = []
    for condition in rule.detection.parsed_condition:
        try:
            condition.parse()
        except SigmaError as error:
            condition_errors.append(str(error))
        except TypeError:
            condition_errors.append(f"Condition must be a string, got {type(condition.condition).__name__}.")
    if condition_errors:
        return _invalid(*condition_errors)

    return SigmaParseResult(rule=rule)
