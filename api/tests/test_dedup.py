"""Duplicate rules: same log source and near-identical detection values (plan increment 10)."""

import pytest

from app.detection.dedup import duplicate_similarity, fingerprint


def rule(values: list[str], *, category: str = "process_creation", field: str = "CommandLine|contains") -> str:
    lines = "\n".join(f"      - '{value}'" for value in values)
    return (
        f"title: t\nlogsource:\n  category: {category}\n  product: windows\ndetection:\n  selection:\n"
        f"    {field}:\n{lines}\n  condition: selection\n"
    )


A5 = ["a", "b", "c", "d", "e"]
BASE = "title: t\nlogsource:\n  category: process_creation\n  product: windows\ndetection:\n  selection:\n    Image|endswith: '\\\\cmd.exe'\n"


def required(field: str, value: str) -> str:
    return BASE + f"  parent:\n    {field}|endswith: '{value}'\n  condition: selection and parent\n"


def excluded(field: str, value: str) -> str:
    return BASE + f"  filter_parent:\n    {field}|endswith: '{value}'\n  condition: selection and not 1 of filter_*\n"


@pytest.mark.parametrize(
    ("left", "right", "duplicate"),
    [
        (rule(A5), rule(A5), True),  # identical
        (rule(A5), rule(["A", "B", "C", "D", "E"]), True),  # case-insensitive
        (rule(A5), rule(A5, field="CommandLine|endswith"), True),  # modifiers don't make a different rule
        (rule(A5), rule([*A5[:4], "f"]), False),  # 4 shared of 6: Jaccard 0.67, no containment
        (rule([*A5, "f"]), rule([*A5, "g"]), False),  # Jaccard 5/7 = 0.71
        (rule([*A5, "f", "g", "h", "i"]), rule([*A5, "f", "g", "h", "j"]), True),  # Jaccard 8/10 = 0.80
        (rule(["a", "b", "c"]), rule(["a", "b", "c", "d"]), True),  # contained, 3 values, Jaccard 0.75
        (rule(["a", "b", "c"]), rule(["a", "b", "c", "d", "e"]), True),  # contained, Jaccard 0.60 exactly
        (rule(["a", "b", "c"]), rule(["a", "b", "c", "d", "e", "f"]), False),  # contained, but Jaccard 0.50
        (rule(["a", "b"]), rule(["a", "b", "c"]), False),  # contained, but only 2 values
        (rule(A5), rule(A5, category="file_event"), False),  # same values, different log source
        (rule(A5), rule(A5, field="ParentCommandLine|contains"), False),  # same values, different field
        (rule(A5), rule(A5, field="CommandLine|base64offset|contains"), False),  # encoded, so a different match
        (rule(["a\\Sx"], field="CommandLine|re"), rule(["a\\sx"], field="CommandLine|re"), False),  # regex case matters
        (required("ParentImage", "explorer.exe"), excluded("ParentImage", "explorer.exe"), False),  # required vs excluded
        (excluded("ParentImage", "explorer.exe"), excluded("ParentImage", "Explorer.exe"), True),
    ],
)
def test_duplicate_pairs(left, right, duplicate):
    assert (duplicate_similarity(fingerprint(left), fingerprint(right)) is not None) is duplicate
    assert (duplicate_similarity(fingerprint(right), fingerprint(left)) is not None) is duplicate  # symmetric


def test_values_come_from_every_selection_and_keyword_lists_but_not_the_condition():
    sigma = """title: t
logsource:
  category: process_creation
  product: windows
detection:
  selection:
    Image|endswith: '\\\\schtasks.exe'
    CommandLine|contains|all: ['/create', '/sc onlogon']
  filter:
    - ParentImage: 'C:\\\\Windows\\\\explorer.exe'
  keywords:
    - 'Updater'
  condition: selection and not filter or keywords
"""

    logsource, values = fingerprint(sigma)

    assert logsource == ("process_creation", "windows", None)
    assert values == frozenset({
        "image=\\schtasks.exe", "commandline=/create", "commandline=/sc onlogon",
        "!parentimage=c:\\windows\\explorer.exe", "=updater",  # the condition negates the filter
    })


def test_unparseable_text_has_no_fingerprint():
    assert fingerprint("title: [unclosed") is None
    assert fingerprint("- just\n- a list\n") is None


def test_null_values_count_as_values():
    sigma = "title: t\nlogsource:\n  category: process_creation\n  product: windows\ndetection:\n  sel:\n    OriginalFileName: null\n  condition: sel\n"

    assert fingerprint(sigma)[1] == frozenset({"originalfilename=<null>"})
