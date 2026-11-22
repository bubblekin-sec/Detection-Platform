"""Flag a rule that duplicates an earlier one, so the reviewer sees it before approving (never dropped: its tokens are
spent and the reviewer decides). Rejected rules are never a duplicate's original."""

from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, lazyload, load_only

from app.detection.dedup import Fingerprint, duplicate_similarity, fingerprint
from app.models import DetectionRule

STATUS_PREFERENCE = {"approved": 0, "pending_review": 1}
DEDUP_LOCK = 0x5167_6D61  # pg advisory lock key for duplicate marking


def release_duplicates_of(session: Session, rule_id: str) -> None:
    """A rejected rule is never an original: clear the flag on rules that pointed at it (caller commits)."""
    session.execute(
        update(DetectionRule).where(DetectionRule.duplicate_of == rule_id)
        .values(duplicate_of=None, duplicate_similarity=None).execution_options(synchronize_session=False)
    )


def mark_duplicates(session: Session, rules: Iterable[DetectionRule]) -> int:
    """Set `duplicate_of` on each new rule from the existing rules and the new rules before it. Returns how many.

    Holds a transaction-scoped advisory lock until the caller commits, so two workers saving rules at once take turns
    and the second sees the first's committed rules (READ COMMITTED re-reads after the wait)."""
    session.execute(select(func.pg_advisory_xact_lock(DEDUP_LOCK)))
    rules = list(rules)
    new_ids = {rule.id for rule in rules}
    candidates = [
        (existing, fingerprint(existing.sigma_yaml))
        for existing in session.scalars(
            select(DetectionRule)
            .where(DetectionRule.status != "rejected", DetectionRule.id.not_in(new_ids))
            .order_by(DetectionRule.created_at, DetectionRule.run_id, DetectionRule.position)
            .options(load_only(DetectionRule.id, DetectionRule.status, DetectionRule.sigma_yaml), lazyload(DetectionRule.duplicate))
        )
    ]
    marked = 0
    for rule in rules:
        own = fingerprint(rule.sigma_yaml)
        match = _best(own, candidates)
        rule.duplicate_of, rule.duplicate_similarity = match if match else (None, None)
        marked += match is not None
        candidates.append((rule, own))
    return marked


def recompute_all(session: Session) -> int:
    """Batch pass over every rule in creation order (`cli dedup-rules`). Idempotent."""
    session.execute(select(func.pg_advisory_xact_lock(DEDUP_LOCK)))
    session.execute(update(DetectionRule).values(duplicate_of=None, duplicate_similarity=None))
    ordered = session.scalars(
        select(DetectionRule).order_by(DetectionRule.created_at, DetectionRule.run_id, DetectionRule.position)
    ).all()
    candidates: list[tuple[DetectionRule, Fingerprint | None]] = []
    marked = 0
    for rule in ordered:
        own = fingerprint(rule.sigma_yaml)
        match = _best(own, candidates)
        if match:
            rule.duplicate_of, rule.duplicate_similarity = match
            marked += 1
        if rule.status != "rejected":
            candidates.append((rule, own))
    session.commit()
    return marked


def _best(own: Fingerprint | None, candidates: list[tuple[DetectionRule, Fingerprint | None]]) -> tuple[str, float] | None:
    """An approved original first, then the most similar, then the oldest."""
    matches = [
        (STATUS_PREFERENCE.get(candidate.status, 1), -similarity, position, candidate.id, similarity)
        for position, (candidate, other) in enumerate(candidates)
        if (similarity := duplicate_similarity(own, other)) is not None
    ]
    if not matches:
        return None
    *_, rule_id, similarity = min(matches)
    return rule_id, similarity
