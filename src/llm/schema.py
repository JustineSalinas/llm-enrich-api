"""
The /enrich contract, straight off JOB-CARD.md: input shape, output shape,
and the closed lists nothing is allowed to escape.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EnrichInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)


class Category(str, Enum):
    fiction = "fiction"
    nonfiction = "nonfiction"
    poetry = "poetry"
    childrens = "childrens"
    biography = "biography"
    other = "other"


class QualityFlag(str, Enum):
    thin_description = "thin_description"
    generic_title = "generic_title"
    promotional_tone = "promotional_tone"
    none = "none"


class EnrichOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    summary: str = Field(..., min_length=1, max_length=240)
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
