"""What a profile is: plain-language settings that drive the generic pipeline stages.

A non-technical editor (later increment) edits these fields; `compile.py` turns them into prompts.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator

FIELD_NAME_PATTERN = r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"  # max length enforced separately (50)
RESERVED_ITEM_FIELDS = {"value", "evidence"}

# Known tags get a precise type and guidance; any other tag is free text described by its name.
KNOWN_TAGS: dict[str, tuple[Any, str]] = {
    "platform": (
        Literal["windows", "linux", "macos", "unknown"],
        "Operating system the observable applies to.",
    ),
    "telemetry_category": (
        Literal[
            "process_creation", "registry_set", "registry_add", "registry_event", "file_event",
            "network_connection", "dns_query", "image_load", "other",
        ],
        "Sigma logsource category whose telemetry records this behaviour, or 'other' if none fits. Prefer "
        "process_creation (schtasks.exe, sc.exe) or registry_set (TaskCache, Services keys) for scheduled tasks "
        "and services, because those convert to Defender XDR queries.",
    ),
    "logsource_hint": (
        str,
        "Short hint for where this appears, e.g. 'Sysmon EventID 1', 'DeviceProcessEvents', 'DeviceRegistryEvents'.",
    ),
}


class ExtractField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=FIELD_NAME_PATTERN, max_length=50)
    description: str = Field(min_length=1, max_length=1000)

    @field_validator("name")
    @classmethod
    def name_does_not_shadow_the_model_api(cls, name: str) -> str:
        if name.startswith("model_") or hasattr(BaseModel, name):
            raise ValueError(f"'{name}' is reserved; choose another field name")
        return name
    # Extra labels the extractor attaches to each value, e.g. platform, telemetry_category, logsource_hint.
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def tag_names_are_identifiers(cls, tags: list[str]) -> list[str]:
        import re

        for tag in tags:
            reserved = tag in RESERVED_ITEM_FIELDS or tag.startswith("model_") or hasattr(BaseModel, tag)
            if not re.fullmatch(FIELD_NAME_PATTERN, tag) or len(tag) > 50 or reserved:
                raise ValueError(f"invalid tag name '{tag}'")
        if len(tags) != len(set(tags)):
            raise ValueError("tag names must be unique")
        return tags


PRIORITY_BUCKETS = ("perfect", "lolbas", "intelligence", "good", "negative")
PriorityBucketName = Literal["perfect", "lolbas", "intelligence", "good", "negative"]


class PriorityTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str = Field(min_length=1, max_length=200)
    # word: whole-word match (boundaries only where the term starts/ends with a word character); prefix: also
    # matches longer words starting with the term (e.g. "hunting" matches "huntingly", "spawn" matches "spawned").
    match: Literal["word", "prefix"] = "word"


class PriorityBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_points: float = Field(ge=0, le=100)
    terms: list[PriorityTerm] = Field(default_factory=list)


class PriorityConfig(BaseModel):
    """Free, deterministic article scoring that decides which articles are worth any model call."""

    model_config = ConfigDict(extra="forbid")

    buckets: dict[PriorityBucketName, PriorityBucket]
    # Articles scoring at least this are auto-queued for the model pipeline; null turns auto-queueing off.
    threshold: float | None = Field(default=None, ge=0, le=99.9)

    @model_validator(mode="after")
    def terms_belong_to_one_bucket(self) -> "PriorityConfig":
        seen: dict[str, str] = {}
        for name, bucket in self.buckets.items():
            for term in bucket.terms:
                key = term.term.casefold()
                if key in seen and seen[key] != name:
                    raise ValueError(f"term '{term.term}' is in more than one bucket ({seen[key]}, {name})")
                seen[key] = name
        return self


class ProfileDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience: str = Field(min_length=1, max_length=200)  # who reads the output, in plain words
    relevant_if: str = Field(min_length=1, max_length=4000)
    ignore_if: str = Field(default="", max_length=4000)
    min_score: int = Field(ge=0, le=10)
    extract_fields: list[ExtractField] = Field(min_length=1)
    output_kind: Literal["sigma_kql"]  # later: "summary" for leadership, etc.
    query_strategy: Literal["converted", "llm", "converted_with_llm_fallback", "both"] = "converted_with_llm_fallback"
    # Optional: profiles without it (e.g. detection v1) are never scored or auto-queued.
    priority: PriorityConfig | None = None

    @field_validator("extract_fields")
    @classmethod
    def unique_names(cls, fields: list[ExtractField]) -> list[ExtractField]:
        names = [field.name for field in fields]
        if len(names) != len(set(names)):
            raise ValueError("extract field names must be unique")
        return fields


def extraction_schema(definition: ProfileDefinition) -> type[BaseModel]:
    """Structured-output model for the extraction stage: one list per profile field, each item with its tags."""
    fields: dict[str, Any] = {}
    for extract_field in definition.extract_fields:
        item_fields: dict[str, Any] = {
            "value": (str, Field(description="The observable exactly as written in the article.")),
            "evidence": (
                str,
                Field(description="An exact, contiguous quote from the article (at most 300 characters) containing the value."),
            ),
        }
        for tag in extract_field.tags:
            annotation, description = KNOWN_TAGS.get(tag, (str, f"The {tag.replace('_', ' ')} for this observable."))
            item_fields[tag] = (annotation, Field(description=description))
        item = create_model(f"{_camel(extract_field.name)}Item", **item_fields)
        fields[extract_field.name] = (list[item], Field(description=extract_field.description))
    return create_model("Extraction", **fields)


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


class SigmaDraft(BaseModel):
    """Structured output of the rule-generation stage (one rule per telemetry group)."""

    title: str = Field(description="Short rule title naming the behaviour detected.")
    sigma_yaml: str = Field(
        description="One complete Sigma rule in YAML: title, description, logsource, detection, falsepositives, level. "
        "Omit id, date, author, status and references; they are added automatically."
    )
    rationale: str = Field(description="One or two sentences: which observables the rule covers and why it is specific.")


class RelevanceVerdict(BaseModel):
    """Structured output of the relevance stage."""

    score: int = Field(description="0 (irrelevant) to 10 (exactly what the reader needs).")
    rationale: str = Field(description="Two to four sentences explaining the score against the criteria.")
    signals: list[str] = Field(description="Short phrases quoted or paraphrased from the article that drove the score.")
