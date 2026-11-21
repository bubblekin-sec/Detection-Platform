"""Priority (hunt) scoring: pure functions, no database."""

import pytest
from pydantic import ValidationError

from app.pipeline.priority import PriorityScorer, band, geo
from app.profiles.huntable_keywords import load_huntable_priority
from app.profiles.schema import PriorityConfig, ProfileDefinition


def config(**buckets) -> PriorityConfig:
    caps = {"perfect": 75, "lolbas": 10, "intelligence": 10, "good": 5, "negative": 15}
    return PriorityConfig.model_validate({
        "threshold": None,
        "buckets": {
            name: {"max_points": caps[name], "terms": [t if isinstance(t, dict) else {"term": t} for t in terms]}
            for name, terms in buckets.items()
        },
    })


@pytest.mark.parametrize(
    ("count", "cap", "expected"),
    [(0, 75, 0.0), (1, 75, 37.5), (2, 75, 56.25), (3, 10, 8.75), (1, 15, 7.5)],
)
def test_geometric_points(count, cap, expected):
    assert geo(count, cap) == expected


def test_score_combines_buckets_and_counts_distinct_terms():
    scorer = PriorityScorer(config(
        perfect=["rundll32.exe", "hklm"], lolbas=["schtasks.exe"], intelligence=["lazarus", "lateral movement"],
    ))

    result = scorer.score(
        "Lazarus abuses rundll32.exe",
        "They ran rundll32.exe again and schtasks.exe, persisted under HKLM, and used lateral movement. rundll32.exe",
    )

    assert result.score == 68.8  # 56.25 + 5 + 7.5
    assert result.buckets["perfect"] == {"terms": ["hklm", "rundll32.exe"], "points": 56.25}
    assert result.buckets["intelligence"]["terms"] == ["lateral movement", "lazarus"]


def test_negative_terms_penalise_and_the_score_never_goes_below_zero():
    scorer = PriorityScorer(config(intelligence=["threat actor"], negative=["webinar", "free trial"]))

    result = scorer.score("Join our free trial webinar", "Learn about threat actor trends.")

    assert result.score == 0.0
    assert result.buckets["negative"]["points"] == 11.25


def test_score_is_capped_at_99_9():
    perfect = [f"term{i}" for i in range(12)]
    scorer = PriorityScorer(config(perfect=perfect, lolbas=["a1"], intelligence=["b1"], good=["c1"]))

    text = " ".join(perfect + ["a1", "b1", "c1"] * 5)
    assert scorer.score("", text + " " + " ".join(f"x{i}" for i in range(3))).score <= 99.9


@pytest.mark.parametrize(
    ("term", "text", "matches"),
    [
        ("hklm", "persisted under HKLM\\Software", True),
        ("hklm", "the hklmx field", False),
        ("\\pipe\\", "connected to \\\\.\\pipe\\msagent_1", True),
        ("==", "if a == b", True),
        ("powershell.exe", "launched PowerShell.exe -enc", True),
        ("powershell.exe", "launched powershell.exe2", False),
        ("iex", "used IEX(New-Object)", True),
        ("iex", "an index entry", False),
    ],
)
def test_word_boundaries_apply_only_at_word_character_ends(term, text, matches):
    assert bool(PriorityScorer(config(perfect=[term])).score("", text).buckets["perfect"]["terms"]) is matches


def test_prefix_terms_match_longer_words():
    scorer = PriorityScorer(config(good=[{"term": "hunting", "match": "prefix"}, {"term": "spawn", "match": "prefix"}]))

    assert scorer.score("", "Hunting queries that find spawned shells").buckets["good"]["terms"] == ["hunting", "spawn"]


def test_title_counts_too():
    scorer = PriorityScorer(config(perfect=["mshta.exe"]))

    assert scorer.score("Abuse of mshta.exe", "nothing else here").score == 37.5


@pytest.mark.parametrize(("score", "name"), [(99.9, "Excellent"), (80, "Excellent"), (79.9, "Good"), (40, "Fair"), (20, "Poor"), (0, "Very Poor")])
def test_bands(score, name):
    assert band(score) == name


def test_huntable_conversion_drops_noisy_terms_and_dedupes_across_buckets():
    priority = load_huntable_priority()

    terms = {
        name: {term.term.casefold() for term in bucket.terms} for name, bucket in priority.buckets.items()
    }
    assert not {"mz", "c:\\", "d:\\"} & terms["perfect"]
    assert "rundll32.exe" in terms["perfect"] and "rundll32.exe" not in terms["lolbas"]
    every = [term for bucket in terms.values() for term in bucket]
    assert len(every) == len(set(every))
    assert {name: bucket.max_points for name, bucket in priority.buckets.items()} == {
        "perfect": 75, "lolbas": 10, "intelligence": 10, "good": 5, "negative": 15,
    }
    assert any(term.match == "prefix" for term in priority.buckets["good"].terms)


def test_profile_validation_rejects_a_term_in_two_buckets():
    with pytest.raises(ValidationError, match="more than one bucket"):
        config(perfect=["certutil.exe"], lolbas=["CertUtil.exe"])


def test_a_profile_without_priority_still_validates():
    definition = ProfileDefinition.model_validate({
        "audience": "x", "relevant_if": "y", "min_score": 6, "output_kind": "sigma_kql",
        "extract_fields": [{"name": "cmdline", "description": "d"}],
    })

    assert definition.priority is None
