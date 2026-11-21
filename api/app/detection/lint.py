"""Warnings for converted KQL that is valid but likely wrong (see the increment 1 findings in the plan)."""

import re

# Operators with no wildcard support, including case-sensitive (_cs) and negated (!) variants.
NO_WILDCARD_OPS = r"(?:!?(?:endswith|startswith|has|hasprefix|hassuffix|contains)(?:_cs)?|=~|==|!=|!~)"
SINGLE_VALUE = re.compile(rf'(?<![\w!=~])({NO_WILDCARD_OPS})\s+"([^"]*)"', re.IGNORECASE)
LIST_VALUE = re.compile(r'(?<![\w!])(!?in~?|has_any|has_all)\s*\(([^)]*)\)', re.IGNORECASE)
QUOTED = re.compile(r'"([^"]*)"')


def lint_kql(kql: str | None, *, sigma_yaml: str = "") -> list[str]:
    warnings: list[str] = []
    if not kql:
        return warnings
    for match in SINGLE_VALUE.finditer(kql):
        if "*" in match.group(2):
            warnings.append(_star_warning(match.group(0), match.group(1)))
    for match in LIST_VALUE.finditer(kql):
        if any("*" in value for value in QUOTED.findall(match.group(2))):
            warnings.append(_star_warning(match.group(0)[:120], match.group(1)))
    if "Registry" in kql and _registry_value_may_include_name(sigma_yaml):
        warnings.append(
            "Defender keeps the registry value name in RegistryValueName, but the converted query matches TargetObject "
            "against RegistryKey only. If the Sigma value includes a value name (e.g. ...\\Run\\Updater), add a "
            "RegistryValueName condition or match the key alone."
        )
    return warnings


def _star_warning(fragment: str, operator: str) -> str:
    return (
        f"`{fragment}` compares against a literal * ({operator} has no wildcards), so it will rarely match. "
        "Usually caused by a value ending in a backslash; drop the trailing backslash in the Sigma."
    )


def _registry_value_may_include_name(sigma_yaml: str) -> bool:
    """TargetObject values that end in a name segment (not a backslash) may carry a value name."""
    for line in sigma_yaml.splitlines():
        if "TargetObject" not in line or ":" not in line:
            continue
        value = line.split(":", 1)[1].strip().strip("'\"")
        if value and not value.endswith("\\") and "\\" in value:
            return True
    return False
