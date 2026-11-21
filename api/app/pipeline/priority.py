"""Priority ("hunt") scoring: free, deterministic keyword scoring that decides which articles get model calls.

Modelled on Huntable CTI Studio's ThreatHuntingScorer (MIT): per bucket, distinct matching terms earn points with
diminishing returns, geo(n, M) = M * (1 - 0.5**n); the final score is the positive buckets minus the negative
penalty, clamped to 0..99.9. Pure functions: no I/O, no database.
"""

import re
from dataclasses import dataclass, field

from app.profiles.schema import PriorityConfig, PriorityTerm

POSITIVE_BUCKETS = ("perfect", "lolbas", "intelligence", "good")
PENALTY_BUCKET = "negative"
MAX_SCORE = 99.9
SCORER_TAG = "regex-v1"  # stored with each score; bump when matching semantics change
BANDS = ((80.0, "Excellent"), (60.0, "Good"), (40.0, "Fair"), (20.0, "Poor"), (0.0, "Very Poor"))
_WORD = re.compile(r"\w")


def geo(count: int, max_points: float) -> float:
    return 0.0 if count <= 0 else max_points * (1 - 0.5**count)


def band(score: float) -> str:
    return next(name for floor, name in BANDS if score >= floor)


@dataclass(frozen=True)
class _Matcher:
    core: str  # case-folded term, checked with a fast substring test before the regex
    pattern: re.Pattern[str]


@dataclass
class PriorityResult:
    score: float
    buckets: dict[str, dict] = field(default_factory=dict)  # {bucket: {"terms": [...], "points": float}}


def _compile(term: PriorityTerm) -> _Matcher:
    core = term.term.casefold()
    start = r"\b" if _WORD.match(core[0]) else ""
    end = r"\b" if term.match == "word" and _WORD.match(core[-1]) else ""
    return _Matcher(core=core, pattern=re.compile(start + re.escape(core) + end))


class PriorityScorer:
    def __init__(self, config: PriorityConfig) -> None:
        self.config = config
        self._buckets = {
            name: (bucket.max_points, [(term.term, _compile(term)) for term in bucket.terms])
            for name, bucket in config.buckets.items()
        }

    def score(self, title: str, text: str) -> PriorityResult:
        haystack = f"{title}\n{text}".casefold()
        result = PriorityResult(score=0.0)
        total = 0.0
        for name, (max_points, matchers) in self._buckets.items():
            matched = sorted(
                {term.casefold() for term, m in matchers if m.core in haystack and m.pattern.search(haystack)}
            )
            points = geo(len(matched), max_points)
            result.buckets[name] = {"terms": matched, "points": round(points, 4)}
            total += -points if name == PENALTY_BUCKET else points
        result.score = round(max(0.0, min(MAX_SCORE, total)), 1)
        return result
