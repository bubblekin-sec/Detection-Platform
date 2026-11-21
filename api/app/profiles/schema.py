"""What a profile is: plain-language settings that drive the generic pipeline stages.

A non-technical editor (later increment) edits these fields; `compile.py` turns them into prompts.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,49}$"


class ExtractField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=FIELD_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1000)
    # Extra labels the extractor attaches to each value, e.g. platform, telemetry_category, logsource_hint.
    tags: list[str] = Field(default_factory=list)


class ProfileDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience: str = Field(min_length=1, max_length=200)  # who reads the output, in plain words
    relevant_if: str = Field(min_length=1, max_length=4000)
    ignore_if: str = Field(default="", max_length=4000)
    min_score: int = Field(ge=0, le=10)
    extract_fields: list[ExtractField] = Field(min_length=1)
    output_kind: Literal["sigma_kql"]  # later: "summary" for leadership, etc.
    query_strategy: Literal["converted", "llm", "converted_with_llm_fallback", "both"] = "converted_with_llm_fallback"

    @field_validator("extract_fields")
    @classmethod
    def unique_names(cls, fields: list[ExtractField]) -> list[ExtractField]:
        names = [field.name for field in fields]
        if len(names) != len(set(names)):
            raise ValueError("extract field names must be unique")
        return fields


class RelevanceVerdict(BaseModel):
    """Structured output of the relevance stage."""

    score: int = Field(description="0 (irrelevant) to 10 (exactly what the reader needs).")
    rationale: str = Field(description="Two to four sentences explaining the score against the criteria.")
    signals: list[str] = Field(description="Short phrases quoted or paraphrased from the article that drove the score.")
