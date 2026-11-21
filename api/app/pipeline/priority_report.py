"""Measure what a priority config would do to the stored articles, without writing anything.

Used to choose a threshold from real data and to find noisy terms (high document frequency, little signal).
"""

import statistics
import time
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, Source
from app.pipeline.priority import BANDS, PriorityScorer, band
from app.profiles.schema import PriorityConfig

CANDIDATE_THRESHOLDS = (20.0, 30.0, 37.5, 40.0, 50.0, 56.0, 60.0, 70.0)


def priority_report(session: Session, config: PriorityConfig, *, top_terms: int = 15) -> str:
    scorer = PriorityScorer(config)
    rows = session.execute(
        select(Article.id, Article.title, Article.content_text, Source.slug).join(Source, Source.id == Article.source_id)
    ).all()
    if not rows:
        return "No articles to score."

    started = time.perf_counter()
    results = [(row, scorer.score(row.title, row.content_text)) for row in rows]
    elapsed = time.perf_counter() - started
    scores = sorted(result.score for _, result in results)
    count = len(scores)

    def pct(p: float) -> float:
        return scores[min(count - 1, int(p / 100 * count))]

    lines = [
        f"Scored {count} articles in {elapsed:.2f}s ({elapsed / count * 1000:.1f} ms each).",
        f"Mean {statistics.mean(scores):.1f} | p50 {pct(50)} | p75 {pct(75)} | p90 {pct(90)} | p95 {pct(95)} | "
        f"p99 {pct(99)} | max {scores[-1]}",
        "",
        "Bands:",
    ]
    bands = Counter(band(score) for score in scores)
    lines += [f"  {name:<10} {bands.get(name, 0):>5}  ({bands.get(name, 0) / count:.0%})" for _, name in BANDS]
    lines += ["", "Articles at or above candidate thresholds (would be auto-queued for relevance):"]
    lines += [f"  >= {t:>5}: {sum(s >= t for s in scores):>5}  ({sum(s >= t for s in scores) / count:.0%})" for t in CANDIDATE_THRESHOLDS]

    lines += ["", f"Most frequent terms per bucket (document frequency, top {top_terms}):"]
    for name in config.buckets:
        frequency = Counter(term for _, result in results for term in result.buckets[name]["terms"])
        common = ", ".join(f"{term} {n / count:.0%}" for term, n in frequency.most_common(top_terms))
        lines.append(f"  {name}: {common or '-'}")

    by_source: dict[str, list[float]] = {}
    for row, result in results:
        by_source.setdefault(row.slug, []).append(result.score)
    lines += ["", "Average score by source:"]
    for slug, values in sorted(by_source.items(), key=lambda pair: -statistics.mean(pair[1])):
        lines.append(f"  {slug:<28} {statistics.mean(values):5.1f}  (n={len(values)})")

    lines += ["", "Top 15 articles:"]
    for row, result in sorted(results, key=lambda pair: -pair[1].score)[:15]:
        lines.append(f"  {result.score:5.1f}  [{row.slug}] {row.title[:90]}")
    return "\n".join(lines)
