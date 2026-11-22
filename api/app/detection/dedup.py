"""Duplicate Sigma rules: the same log source and near-identical detection values.

A rule's fingerprint is its log source plus the set of `field=value` pairs in its detection (every selection, filter
and keyword list). Values under a search the condition negates (`not filter`) are marked as excluded. Values are
case-folded (except regexes) and the matching modifiers (contains, startswith, endswith, all) are ignored, so
`contains` and `endswith` on the same value count as the same detection; modifiers that change what a value means
(re, base64offset, cidr, windash...) are kept. Two rules are duplicates when the Jaccard similarity of their sets is at
least 0.80, or when one set contains the other, the smaller has at least 3 values, and Jaccard is at least 0.60.
"""

import fnmatch
import re
from typing import Any

import yaml

DUPLICATE_JACCARD = 0.80
CONTAINED_JACCARD = 0.60
CONTAINED_MIN_VALUES = 3
NON_SEARCH_KEYS = {"condition", "timeframe"}
# Modifiers that change what a value matches (unlike contains/startswith/endswith/all, which only narrow or widen it).
MEANINGFUL_MODIFIERS = {"re", "base64", "base64offset", "cidr", "windash", "wide", "utf16", "utf16le", "utf16be",
                        "expand", "fieldref", "exists", "lt", "lte", "gt", "gte"}
NEGATED = re.compile(r"\bnot\s+(?:(?:1|all)\s+of\s+)?([\w*]+)", re.IGNORECASE)

Fingerprint = tuple[tuple[str | None, str | None, str | None], frozenset[str]]


def fingerprint(sigma_yaml: str) -> Fingerprint | None:
    try:
        document = yaml.safe_load(sigma_yaml)
    except yaml.YAMLError:
        return None
    return fingerprint_document(document)


def fingerprint_document(document: Any) -> Fingerprint | None:
    """The fingerprint of an already-loaded Sigma document (a reference ruleset's files are loaded once)."""
    if not isinstance(document, dict) or not isinstance(document.get("detection"), dict):
        return None
    source = document.get("logsource") if isinstance(document.get("logsource"), dict) else {}
    logsource = tuple(_text(source.get(key)) for key in ("category", "product", "service"))
    negated = _negated_patterns(document["detection"].get("condition"))
    values: set[str] = set()
    for name, search in document["detection"].items():
        if name not in NON_SEARCH_KEYS:
            # A value a rule requires and one it excludes (`and not filter`) are different detections.
            prefix = "!" if any(fnmatch.fnmatchcase(str(name), pattern) for pattern in negated) else ""
            _collect(search, prefix, values)
    return logsource, frozenset(values)  # type: ignore[return-value]


def _negated_patterns(condition: Any) -> set[str]:
    """Search names (or `filter*` patterns) that appear after `not` in the condition(s)."""
    conditions = condition if isinstance(condition, list) else [condition]
    patterns: set[str] = set()
    for text in conditions:
        for match in NEGATED.finditer(str(text or "")):
            patterns.add("*" if match.group(1) == "them" else match.group(1))
    return patterns


def duplicate_similarity(left: Fingerprint | None, right: Fingerprint | None) -> float | None:
    """The Jaccard similarity when the two rules are duplicates, otherwise None."""
    if left is None or right is None or left[0] != right[0] or not left[1] or not right[1]:
        return None
    shared = len(left[1] & right[1])
    jaccard = shared / len(left[1] | right[1])
    smaller = min(len(left[1]), len(right[1]))
    contained = shared == smaller
    if jaccard >= DUPLICATE_JACCARD or (contained and smaller >= CONTAINED_MIN_VALUES and jaccard >= CONTAINED_JACCARD):
        return round(jaccard, 3)
    return None


def _collect(node: Any, field: str, values: set[str]) -> None:
    """`field` is `[!]name[|meaningful|modifiers]` for the values below it."""
    if isinstance(node, dict):
        negation = "!" if field.startswith("!") else ""
        for key, value in node.items():
            name, *modifiers = str(key).casefold().split("|")
            kept = sorted(set(modifiers) & MEANINGFUL_MODIFIERS)
            _collect(value, negation + "|".join([name, *kept]), values)
    elif isinstance(node, list):
        for item in node:
            _collect(item, field, values)
    elif node is None:
        values.add(f"{field}=<null>")  # `Field: null` checks that the field is empty
    else:
        value = str(node).strip()
        if "|re" not in field:  # regex case (\S vs \s) and escapes are meaningful
            # In Sigma, `\\` is an escaped backslash: '\\schtasks.exe' and '\schtasks.exe' are the same value.
            value = value.casefold().replace("\\\\", "\\")
        values.add(f"{field}={value}")


def _text(value: Any) -> str | None:
    return str(value).casefold() if value is not None else None
