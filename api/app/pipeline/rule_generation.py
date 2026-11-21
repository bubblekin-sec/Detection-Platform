"""Stage 4: one Sigma rule per (platform, telemetry category) group of verified observables.

Each rule is validated by the platform's Sigma validator (one repair attempt with the errors), gets its metadata
set by the platform, and is converted deterministically to a Defender XDR query artifact with lint warnings.
Groups are independent: an error in one never loses the rules already created for the others.
"""

import hashlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.config import Settings
from app.detection.kql import to_defender_xdr_kql
from app.detection.lint import lint_kql
from app.detection.rules import finalize_sigma, identity_error
from app.detection.sigma import parse_sigma
from app.llm.client import LLMClient, LLMError, LLMRefusal, LLMUsage
from app.models import DetectionRule, Observable, RuleQuery
from app.profiles.compile import compile_rule_generation, render_rule_request
from app.profiles.schema import ProfileDefinition, SigmaDraft

log = logging.getLogger(__name__)
MAX_REPAIR_ERRORS = 8
# Groups the model could not place in a detectable category are not worth an Opus call.
SKIPPED_CATEGORIES = {"other", "unknown"}


@dataclass
class RuleGenerationResult:
    rules: list[DetectionRule] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    skipped_groups: int = 0
    system_prompt: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)
    first_error: str | None = None


def group_observables(observables: list[Observable]) -> tuple[list[tuple[dict[str, str], list[Observable]]], int]:
    """Verified observables grouped by platform and telemetry category, largest first; plus uncategorised count."""
    groups: dict[tuple[str, str], list[Observable]] = defaultdict(list)
    uncategorised = 0
    for item in observables:
        if not item.evidence_verified:
            continue
        category = str(item.tags.get("telemetry_category") or "unknown").strip().casefold()
        if category in SKIPPED_CATEGORIES:
            uncategorised += 1
            continue
        platform = str(item.tags.get("platform") or "unknown").strip().casefold()
        groups[(platform, category)].append(item)
    ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    return [({"platform": platform, "telemetry_category": category}, items) for (platform, category), items in ordered], uncategorised


def generate_rules(
    llm: LLMClient,
    settings: Settings,
    definition: ProfileDefinition,
    *,
    run_id: int,
    article_id: int,
    title: str,
    url: str,
    observables: list[Observable],
    today: date,
    on_progress: Callable[[], None] = lambda: None,
) -> RuleGenerationResult:
    """`on_progress` is called after every model call so the run's heartbeat keeps up with long stages."""
    result = RuleGenerationResult(system_prompt=compile_rule_generation(definition))
    groups, _ = group_observables(observables)
    result.skipped_groups = max(0, len(groups) - settings.max_rule_groups_per_run)
    totals = [0, 0, 0]
    stop_reason: str | None = None

    for position, (group, items) in enumerate(groups[: settings.max_rule_groups_per_run]):
        outcome: dict[str, Any] = {**group, "observables": len(items)}
        if stop_reason:
            result.groups.append(outcome | {"outcome": "not_attempted", "error": stop_reason})
            continue
        try:
            rule = _generate_group(llm, settings, result, group, items, outcome, totals, on_progress,
                                   run_id=run_id, article_id=article_id, title=title, url=url, today=today)
            if rule is not None:
                rule.position = position
                result.rules.append(rule)
        except Exception as error:  # a bug in one group must not lose the others or the tokens already spent
            log.exception("Rule generation failed for group %s", group)
            outcome |= {"outcome": "error", "error": f"Internal error ({type(error).__name__})."}
        result.groups.append(outcome)
        if outcome.get("outcome") == "llm_error" and not outcome.get("refusal"):
            # Auth, model, rate-limit or connection problems will repeat for every group: stop spending time.
            stop_reason = f"Skipped after an earlier group failed: {outcome['error']}"
            result.first_error = result.first_error or outcome["error"]

    result.usage = LLMUsage(input_tokens=totals[0], output_tokens=totals[1], cache_read_tokens=totals[2])
    return result


def _generate_group(llm, settings, result, group, items, outcome, totals, on_progress, *, run_id, article_id, title, url, today):
    request = render_rule_request(
        title=title, url=url, group=group,
        observables=[{"field_name": item.field_name, "value": item.value, "evidence": item.evidence} for item in items],
    )
    rule_id = str(uuid.uuid4())
    user, errors = request, []
    for attempt in (1, 2):
        try:
            response = llm.complete_json(
                model=settings.rules_model, system=result.system_prompt, user=user, schema=SigmaDraft,
                effort=settings.rules_effort, max_tokens=16000, fallbacks=True,
            )
        except LLMError as error:
            outcome |= {"outcome": "llm_error", "error": str(error), "refusal": isinstance(error, LLMRefusal)}
            if errors:
                outcome["errors"] = errors  # keep why the previous attempt was rejected
            return None
        finally:
            on_progress()
        totals[0] += response.usage.input_tokens
        totals[1] += response.usage.output_tokens
        totals[2] += response.usage.cache_read_tokens
        outcome["model"] = response.model
        if response.fallback_from:
            outcome["fallback_from"] = response.fallback_from
        draft = response.output
        sigma = finalize_sigma(draft.sigma_yaml, rule_id=rule_id, reference_url=url, today=today)
        parsed = parse_sigma(sigma)
        if parsed.rule is not None and (mismatch := identity_error(parsed.rule, rule_id)):
            parsed.errors.append(mismatch)
        elif parsed.rule is not None:
            outcome |= {"outcome": "created", "rule_id": rule_id, "attempts": attempt}
            return _rule(rule_id, run_id, article_id, group, items, draft, sigma, parsed.rule)
        errors = parsed.errors[:MAX_REPAIR_ERRORS]
        user = (
            f"{request}\n\nYour previous rule failed validation:\n"
            + "\n".join(f"- {error}" for error in errors)
            + f"\n\nPrevious rule:\n{draft.sigma_yaml}\n\nReturn a corrected rule."
        )
    outcome |= {"outcome": "invalid", "errors": errors}
    return None


def converted_query(parsed_rule, sigma: str, sigma_hash: str) -> RuleQuery:
    """Deterministic Defender XDR artifact for a validated rule: the KQL or the conversion error, plus lint."""
    converted = to_defender_xdr_kql(parsed_rule)
    return RuleQuery(
        target="defender_xdr",
        origin="converted",
        query_text=converted.kql,
        status="ok" if converted.kql else "failed",
        error=converted.error,
        warnings=lint_kql(converted.kql, sigma_yaml=sigma),
        source_sigma_hash=sigma_hash,
    )


def rule_level(parsed_rule) -> str | None:
    level = getattr(parsed_rule.level, "name", None)
    return level.lower() if level else None


def _rule(rule_id, run_id, article_id, group, items, draft: SigmaDraft, sigma: str, parsed_rule) -> DetectionRule:
    sigma_hash = sigma_digest(sigma)
    query = converted_query(parsed_rule, sigma, sigma_hash)
    return DetectionRule(
        id=rule_id,
        run_id=run_id,
        article_id=article_id,
        group_key=group,
        observable_ids=[item.id for item in items],
        title=parsed_rule.title or draft.title,
        level=rule_level(parsed_rule),
        sigma_yaml=sigma,
        sigma_hash=sigma_hash,
        rationale=draft.rationale,
        status="pending_review",
        queries=[query],
    )


def sigma_digest(sigma_yaml: str) -> str:
    return hashlib.sha256(sigma_yaml.encode()).hexdigest()
