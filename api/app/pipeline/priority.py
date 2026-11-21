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
MAX_SPANS = 2000
BANDS = ((80.0, "Excellent"), (60.0, "Good"), (40.0, "Fair"), (20.0, "Poor"), (0.0, "Very Poor"))
_WORD = re.compile(r"\w")


def geo(count: int, max_points: float) -> float:
    return 0.0 if count <= 0 else max_points * (1 - 0.5**count)


def band(score: float) -> str:
    return next(name for floor, name in BANDS if score >= floor)


@dataclass(frozen=True)
class _Matcher:
    core: str  # case-folded term, checked with a fast substring test before the regex
    pattern: re.Pattern[str]  # runs on case-folded text, for scoring and highlighting alike


@dataclass
class PriorityResult:
    score: float
    buckets: dict[str, dict] = field(default_factory=dict)  # {bucket: {"terms": [...], "points": float}}


def _compile(term: PriorityTerm) -> _Matcher:
    core = term.term.casefold()
    start = r"\b" if _WORD.match(core[0]) else ""
    end = r"\b" if term.match == "word" and _WORD.match(core[-1]) else ""
    return _Matcher(core=core, pattern=re.compile(start + re.escape(core) + end))


class FoldedText:
    """A case-folded copy of a text plus a map from each folded character back to its original index.

    Case-folding can lengthen a string ("ß" -> "ss"), so matching on the folded copy and mapping back is the only way
    to report offsets that agree with what case-insensitive matching found. With `collapse_whitespace`, runs of
    whitespace become one space (how evidence quotes are verified). Build once per text, then query many times.
    """

    def __init__(self, text: str, *, collapse_whitespace: bool = False) -> None:
        self.text = text
        folded = text.casefold()
        if not collapse_whitespace and len(folded) == len(text):
            # casefold never shortens a character, so equal length means every character maps to itself.
            self.folded, self._origin = folded, None
            return
        chars: list[str] = []
        origin: list[int] = []
        previous_space = False
        for index, char in enumerate(text):
            if collapse_whitespace and char.isspace():
                if not previous_space and chars:
                    chars.append(" ")
                    origin.append(index)
                previous_space = True
                continue
            previous_space = False
            for piece in char.casefold():
                chars.append(piece)
                origin.append(index)
        self.folded, self._origin = "".join(chars), origin

    def original(self, start: int, end: int) -> tuple[int, int]:
        """Original code-point range covering folded range [start, end); a split expansion rounds outward."""
        if self._origin is None:
            return start, end
        return self._origin[start], self._origin[end - 1] + 1

    def find(self, quote: str) -> tuple[int, int] | None:
        """UTF-16 offsets of a quote (whitespace collapsed, case-insensitive) in the original text, or None."""
        needle = " ".join(quote.split()).casefold()
        position = self.folded.find(needle) if needle else -1
        if position < 0:
            return None
        start, end = self.original(position, position + len(needle))
        return tuple(utf16_offsets(self.text, [start, end]))  # type: ignore[return-value]


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

    def spans(self, text: str, *, limit: int = MAX_SPANS) -> tuple[list[dict], bool]:
        """Keyword occurrences in `text` as UTF-16 offsets (what browser string indexes use), earliest first, and
        whether more than `limit` were found. Same patterns on the same folded text as `score`, so they agree."""
        folded = FoldedText(text)
        found: list[tuple[int, int, str, str]] = []
        for name, (_, matchers) in self._buckets.items():
            for term, matcher in matchers:
                if matcher.core not in folded.folded:
                    continue
                # The earliest `limit` overall are among each term's earliest `limit + 1`, so cap per term too.
                for count, match in enumerate(matcher.pattern.finditer(folded.folded)):
                    if count > limit:
                        break
                    start, end = folded.original(match.start(), match.end())
                    found.append((start, end, name, term.casefold()))
        found.sort()
        truncated = len(found) > limit
        found = found[:limit]
        indices = sorted({index for start, end, _, _ in found for index in (start, end)})
        lookup = dict(zip(indices, utf16_offsets(text, indices)))
        return [{"start": lookup[s], "end": lookup[e], "bucket": b, "term": t} for s, e, b, t in found], truncated


def utf16_offsets(text: str, indices: list[int]) -> list[int]:
    """Map sorted Python string indices (code points) to UTF-16 code-unit offsets."""
    result, units, position = [], 0, 0
    for index in indices:
        while position < index:
            units += 2 if ord(text[position]) > 0xFFFF else 1
            position += 1
        result.append(units)
    return result


def locate(evidence: str, text: str) -> tuple[int, int] | None:
    """One-off lookup; build a `FoldedText(text, collapse_whitespace=True)` yourself to look up many quotes."""
    return FoldedText(text, collapse_whitespace=True).find(evidence)
