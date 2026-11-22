"""Stage 4: one Sigma rule per (platform, telemetry category) group of verified observables.

Each rule is validated by the platform's Sigma validator (one repair attempt with the errors), gets its metadata
set by the platform, gets one lint repair when its converted KQL compares against a literal * (kept only if the
rewrite validates and converts clean), and gets Defender XDR query artifacts according to the profile's `query_strategy`:
`converted` (deterministic pySigma conversion, never a model call), `llm` (model-written), `both`, or the default
`converted_with_llm_fallback` (the model writes KQL only when conversion fails, e.g. DNS rules). Model-written KQL
is checked against the Defender XDR table and column catalog; unknown names make the artifact `failed`.
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
from app.detection.kql import KqlResult, to_defender_xdr_kql
from app.detection.kql_schema import check_kql
from app.detection.lint import lint_kql, literal_star_warnings
from app.detection.rules import finalize_sigma, identity_error
from app.detection.sigma import parse_sigma
from app.llm.client import LLMClient, LLMError, LLMRefusal, LLMUsage
from app.models import DetectionRule, Observable, RuleQuery
from app.profiles.compile import (
    compile_kql_generation,
    compile_rule_generation,
    escape_markup,
    render_kql_request,
    render_rule_request,
    unescape_markup,
)
from app.profiles.schema import KqlDraft, ProfileDefinition, SigmaDraft

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
    kql_system_prompt: str = ""  # set when the strategy can call the model for KQL
    query_strategy: str = "converted_with_llm_fallback"
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
    strategy = result.query_strategy = definition.query_strategy
    if strategy != "converted":
        result.kql_system_prompt = compile_kql_generation(definition)
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
            rule = _generate_group(llm, settings, result, group, items, outcome, totals, on_progress, strategy,
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
        elif outcome.get("lint_repair_systemic"):
            stop_reason = f"Skipped after an earlier group's lint repair failed: {outcome['lint_repair_error']}"
        elif outcome.get("kql_systemic"):
            # Only errors every call would hit (credentials, model access, outage) stop the remaining groups: a
            # one-off KQL failure (token limit, malformed output) must not cost them their Sigma rules.
            stop_reason = f"Skipped after an earlier group's KQL call failed: {outcome['kql_error']}"

    result.usage = LLMUsage(input_tokens=totals[0], output_tokens=totals[1], cache_read_tokens=totals[2])
    return result


def _generate_group(llm, settings, result, group, items, outcome, totals, on_progress, strategy, *, run_id, article_id, title, url, today):
    request = render_rule_request(
        title=title, url=url, group=group,
        observables=[{"field_name": item.field_name, "value": item.value, "evidence": item.evidence} for item in items],
    )
    rule_id = str(uuid.uuid4())
    user, errors = request, []
    for attempt in (1, 2):
        try:
            response = _sigma_call(llm, settings, result.system_prompt, user, totals, on_progress)
        except LLMError as error:
            outcome |= {"outcome": "llm_error", "error": str(error), "refusal": isinstance(error, LLMRefusal)}
            if errors:
                outcome["errors"] = errors  # keep why the previous attempt was rejected
            return None
        outcome["model"] = response.model
        if response.fallback_from:
            outcome["fallback_from"] = response.fallback_from
        draft = response.output
        sigma, parsed_rule, rule_errors = _validated(draft, rule_id=rule_id, url=url, today=today)
        if parsed_rule is not None:
            outcome |= {"outcome": "created", "rule_id": rule_id, "attempts": attempt}
            conversion = None
            if strategy != "llm":  # the repair targets the converted query, which `llm` doesn't produce
                draft, sigma, parsed_rule, conversion = _lint_repair(
                    llm, settings, result, request, outcome, totals, on_progress,
                    draft=draft, sigma=sigma, parsed_rule=parsed_rule, rule_id=rule_id, url=url, today=today,
                )
            rule = _rule(rule_id, run_id, article_id, group, items, draft, sigma, parsed_rule)
            rule.queries = _queries(llm, settings, result, strategy, outcome, totals, on_progress, parsed_rule=parsed_rule,
                                    sigma=sigma, sigma_hash=rule.sigma_hash, items=items, conversion=conversion)
            return rule
        errors = rule_errors[:MAX_REPAIR_ERRORS]
        user = _resubmission(request, "Your previous rule failed validation:", errors, draft.sigma_yaml)
    outcome |= {"outcome": "invalid", "errors": errors}
    return None


def _sigma_call(llm, settings, system, user, totals, on_progress):
    try:
        response = llm.complete_json(
            model=settings.rules_model, system=system, user=user, schema=SigmaDraft,
            effort=settings.rules_effort, max_tokens=16000, fallbacks=True,
        )
    finally:
        on_progress()
    totals[0] += response.usage.input_tokens
    totals[1] += response.usage.output_tokens
    totals[2] += response.usage.cache_read_tokens
    return response


def _resubmission(request: str, problem: str, details: list[str], previous_yaml: str) -> str:
    """A repair request. The previous rule may repeat article text, so it is escaped and tagged like the observables."""
    return (
        f"{request}\n\n{problem}\n"
        + "\n".join(f"- {escape_markup(detail)}" for detail in details)
        + f"\n\n<previous_rule>\n{escape_markup(previous_yaml)}\n</previous_rule>\n\nReturn a corrected rule."
    )


def _validated(draft: SigmaDraft, *, rule_id, url, today):
    """The draft with platform metadata applied, its parsed rule (None when invalid), and the validation errors."""
    # The request escaped "<" so article text couldn't pose as markup; values copied from it come back escaped.
    sigma = finalize_sigma(unescape_markup(draft.sigma_yaml), rule_id=rule_id, reference_url=url, today=today)
    parsed = parse_sigma(sigma)
    if parsed.rule is not None and (mismatch := identity_error(parsed.rule, rule_id)):
        return sigma, None, [*parsed.errors, mismatch]
    return sigma, parsed.rule, parsed.errors


def _lint_repair(llm, settings, result, request, outcome, totals, on_progress, *, draft, sigma, parsed_rule, rule_id, url, today):
    """One rewrite when the converted KQL compares against a literal *. The rewrite replaces the rule only if it
    validates and converts with no such warning; otherwise the original rule stands. A clean rule makes no call.
    Returns the rule to keep and its conversion, so the rule is converted once."""
    converted = to_defender_xdr_kql(parsed_rule)
    problems = literal_star_warnings(converted.kql) if converted.kql else []
    if not problems:
        return draft, sigma, parsed_rule, converted
    user = _resubmission(
        request, "Your previous rule is valid Sigma, but its Defender XDR conversion is likely wrong. Keep the same "
        "log source and detection intent:", problems[:MAX_REPAIR_ERRORS], draft.sigma_yaml,
    )
    try:
        response = _sigma_call(llm, settings, result.system_prompt, user, totals, on_progress)
    except LLMError as error:
        outcome |= {"lint_repair": "llm_error", "lint_repair_error": str(error), "lint_repair_systemic": error.systemic}
        return draft, sigma, parsed_rule, converted
    outcome["lint_repair_model"] = response.model
    try:
        repaired_sigma, repaired_rule, _ = _validated(response.output, rule_id=rule_id, url=url, today=today)
        if repaired_rule is None:
            reason = "The rewrite failed validation."
        elif _logsource(repaired_rule) != _logsource(parsed_rule):
            reason = "The rewrite changed the log source."
        elif not (repaired := to_defender_xdr_kql(repaired_rule)).kql:
            reason = "The rewrite could not be converted to KQL."
        elif literal_star_warnings(repaired.kql):
            reason = "The rewrite still compares against a literal *."
        else:
            # The saved rule is the rewrite, so the group's provenance is the call that wrote it.
            outcome |= {"lint_repair": "repaired", "model": response.model}
            outcome.pop("fallback_from", None)
            if response.fallback_from:
                outcome["fallback_from"] = response.fallback_from
            return response.output, repaired_sigma, repaired_rule, repaired
    except Exception:  # judging the rewrite must never cost the validated rule
        log.exception("Checking the lint repair failed")
        reason = "The rewrite could not be checked (internal error)."
    outcome |= {"lint_repair": "discarded", "lint_repair_reason": reason}
    return draft, sigma, parsed_rule, converted


def _logsource(parsed_rule) -> tuple:
    source = parsed_rule.logsource
    return source.category, source.product, source.service


def converted_query(parsed_rule, sigma: str, sigma_hash: str, converted: KqlResult | None = None) -> RuleQuery:
    """Deterministic Defender XDR artifact for a validated rule: the KQL or the conversion error, plus lint.
    `converted` reuses a conversion already made for this exact rule."""
    converted = converted or to_defender_xdr_kql(parsed_rule)
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


def _queries(llm, settings, result, strategy, outcome, totals, on_progress, *, parsed_rule, sigma, sigma_hash, items, conversion=None):
    """The rule's Defender XDR artifacts under the profile's query strategy (see the module docstring)."""
    queries: list[RuleQuery] = []
    converted = None if strategy == "llm" else converted_query(parsed_rule, sigma, sigma_hash, conversion)
    if converted is not None:
        queries.append(converted)
    wants_llm = strategy in ("llm", "both") or (strategy == "converted_with_llm_fallback" and converted.status == "failed")
    if wants_llm and not outcome.get("lint_repair_systemic"):  # that error would repeat on this call too
        queries.append(llm_query(
            llm, settings, result.kql_system_prompt, outcome, totals, on_progress, sigma=sigma, sigma_hash=sigma_hash,
            conversion_error=converted.error if converted is not None else None, items=items,
        ))
    return queries


def llm_query(llm, settings, system, outcome, totals, on_progress, *, sigma, sigma_hash, conversion_error, items) -> RuleQuery:
    """One model call for Defender XDR KQL, checked against the table and column catalog. Never raises an LLM error:
    a failed or refused call becomes a failed artifact, so the Sigma rule is always kept."""
    user = render_kql_request(
        sigma_yaml=sigma, conversion_error=conversion_error,
        observables=[{"field_name": item.field_name, "value": item.value, "evidence": item.evidence} for item in items],
    )
    try:
        response = llm.complete_json(
            model=settings.rules_model, system=system, user=user, schema=KqlDraft,
            effort=settings.rules_effort, max_tokens=16000, fallbacks=True,
        )
    except LLMError as error:
        outcome |= {"kql": "llm_error", "kql_error": str(error), "kql_systemic": error.systemic}
        return _failed_llm_query(f"The model could not write KQL: {error}", sigma_hash, settings.rules_model)
    finally:
        on_progress()
    totals[0] += response.usage.input_tokens
    totals[1] += response.usage.output_tokens
    totals[2] += response.usage.cache_read_tokens
    try:
        # The request escaped "<" so article text couldn't pose as markup; values copied from it come back escaped.
        kql = unescape_markup(_unfence(response.output.kql)).replace("\x00", "")  # PostgreSQL rejects NUL in text
        problems = check_kql(kql).errors if kql else ["The model returned an empty query."]
        warnings = lint_kql(kql, sigma_yaml=sigma) if kql else []
    except Exception:  # checking model output must never cost the validated Sigma rule
        log.exception("Checking model-written KQL failed")
        outcome["kql"] = "error"
        return _failed_llm_query("The model's KQL could not be checked (internal error).", sigma_hash, response.model)
    outcome["kql"] = "failed_schema_check" if problems else "llm"
    return RuleQuery(
        target="defender_xdr", origin="llm", query_text=kql or None, status="failed" if problems else "ok",
        error=" ".join(problems) or None, warnings=warnings, source_sigma_hash=sigma_hash, llm_model=response.model,
    )


def _failed_llm_query(error: str, sigma_hash: str, model: str) -> RuleQuery:
    return RuleQuery(
        target="defender_xdr", origin="llm", query_text=None, status="failed", error=error, warnings=[],
        source_sigma_hash=sigma_hash, llm_model=model,
    )


def _unfence(text: str) -> str:
    """Drop a markdown code fence if the model added one despite the instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _rule(rule_id, run_id, article_id, group, items, draft: SigmaDraft, sigma: str, parsed_rule) -> DetectionRule:
    sigma_hash = sigma_digest(sigma)
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
    )


def sigma_digest(sigma_yaml: str) -> str:
    return hashlib.sha256(sigma_yaml.encode()).hexdigest()
