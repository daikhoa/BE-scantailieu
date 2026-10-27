import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class ImageInfo(Contract):
    width: int = Field(gt=0, le=20000)
    height: int = Field(gt=0, le=20000)
    mime: Literal["image/jpeg", "image/png", "image/webp", "image/avif", "image/heic", "image/heif"]

    @model_validator(mode="after")
    def pixel_limit(self):
        if self.width * self.height > 80_000_000:
            raise ValueError("Image exceeds pixel limit")
        return self


class Point(Contract):
    x: float = Field(ge=0, le=20000)
    y: float = Field(ge=0, le=20000)


class FullFrame(Contract):
    kind: Literal["full-frame"]


class DocumentCrop(Contract):
    kind: Literal["document"]
    corners: tuple[Point, Point, Point, Point]


class Dewarp(Contract):
    mode: Literal["perspective-only", "auto"]
    policyVersion: str | None = Field(default=None, max_length=120)


class Filter(Contract):
    mode: Literal[
        "original", "ocr-color", "enhanced-color", "shadow-removal", "grayscale", "black-and-white"
    ]
    presetVersion: str = Field(min_length=1, max_length=120)
    parameters: dict[str, float] = Field(max_length=16)

    @model_validator(mode="after")
    def bounded_parameters(self):
        if any(
            len(key) > 80 or not math.isfinite(value) or abs(value) > 10000
            for key, value in self.parameters.items()
        ):
            raise ValueError("Invalid filter parameter")
        return self


class Recipe(Contract):
    version: int = Field(ge=1, le=100)
    crop: FullFrame | DocumentCrop = Field(discriminator="kind")
    rotation: Literal[0, 90, 180, 270]
    dewarp: Dewarp
    filter: Filter


class DewarpOutcome(Contract):
    kind: Literal["not-requested", "not-needed", "applied", "fallback"]
    modelVersion: str | None = Field(default=None, max_length=120)
    reason: Literal["unavailable", "timeout", "out-of-memory", "failed"] | None = None


class SavedPage(Contract):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    original: ImageInfo
    rendered: ImageInfo
    recipe: Recipe
    engineVersion: str = Field(min_length=1, max_length=160)
    recipeKey: str = Field(min_length=1, max_length=500)
    outcome: DewarpOutcome
    enhancement: Literal["local", "shadow-model", "model-unavailable"] | None = None
    enhancementFailure: (
        Literal["download", "model-invalid", "memory", "runtime", "inference", "timeout", "busy"]
        | None
    ) = None

    @model_validator(mode="after")
    def check_crop(self):
        if (
            self.rendered.mime not in ("image/jpeg", "image/png")
            or self.rendered.width * self.rendered.height > 16_000_000
        ):
            raise ValueError("Invalid rendered image")
        if isinstance(self.recipe.crop, DocumentCrop):
            points = self.recipe.crop.corners
            if any(
                point.x > self.original.width or point.y > self.original.height for point in points
            ):
                raise ValueError("Crop outside original")
            crosses = []
            for i in range(4):
                a, b, c = points[i], points[(i + 1) % 4], points[(i + 2) % 4]
                crosses.append((b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x))
            if not all(value > 0 for value in crosses):
                raise ValueError("Crop must be a clockwise convex quadrilateral")
        return self


class Manifest(Contract):
    schemaVersion: Literal[1]
    name: str = Field(min_length=1, max_length=160)
    revision: int | None = Field(default=None, ge=1)
    pages: list[SavedPage] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def check_pages(self):
        self.name = self.name.strip()
        if not self.name or any(ord(character) < 32 for character in self.name):
            raise ValueError("Invalid document name")
        if len({page.id for page in self.pages}) != len(self.pages):
            raise ValueError("Duplicate page IDs")
        return self
