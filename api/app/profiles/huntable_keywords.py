"""Turn the vendored Huntable keyword registry into a profile PriorityConfig (see THIRD_PARTY_NOTICES.md).

The YAML stays verbatim; every correction lives here:
- Noisy terms that match almost any technical page are dropped.
- A term listed in several tiers is kept only in the strongest one (perfect > lolbas > intelligence > good), so it
  is not scored twice. Huntable's own scorer double-counts these.
- Terms Huntable's code matches as prefixes are marked `prefix`.
"""

from importlib.resources import files

import yaml

from app.profiles.schema import PriorityConfig

REGISTRY = files("app.profiles") / "seed_data" / "vendor" / "huntable_keyword_registry.yaml"
BUCKET_ORDER = ("perfect", "lolbas", "intelligence", "good", "negative")
MAX_POINTS = {"perfect": 75.0, "lolbas": 10.0, "intelligence": 10.0, "good": 5.0, "negative": 15.0}
# Huntable's newer filter already excludes these: they appear on nearly every Windows-related page.
DROPPED_TERMS = {"mz", "c:\\", "d:\\"}
# Matched as word prefixes in Huntable's ThreatHuntingScorer (partial/wildcard rules).
PREFIX_TERMS = {"hunting", "detection", "monitor", "alert", "executable", "parent-child", "defender query", "spawn"}


def load_huntable_priority(*, threshold: float | None = None, extra_dropped: set[str] | None = None) -> PriorityConfig:
    dropped = DROPPED_TERMS | {term.casefold() for term in extra_dropped or set()}
    entries = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["keywords"]
    tiers_by_term: dict[str, tuple[str, str]] = {}  # casefolded -> (original, strongest tier)
    for entry in entries:
        tier, term = entry.get("tier"), str(entry.get("match", "")).strip()
        if tier not in BUCKET_ORDER or not term or term.casefold() in dropped:
            continue
        key = term.casefold()
        current = tiers_by_term.get(key)
        if current is None or BUCKET_ORDER.index(tier) < BUCKET_ORDER.index(current[1]):
            tiers_by_term[key] = (term, tier)

    buckets: dict[str, dict] = {name: {"max_points": MAX_POINTS[name], "terms": []} for name in BUCKET_ORDER}
    for key, (term, tier) in sorted(tiers_by_term.items()):
        buckets[tier]["terms"].append({"term": term, "match": "prefix" if key in PREFIX_TERMS else "word"})
    return PriorityConfig.model_validate({"buckets": buckets, "threshold": threshold})
