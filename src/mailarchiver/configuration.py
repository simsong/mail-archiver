"""Load validated packaged application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from yaml import safe_load


class GuiConfiguration(BaseModel):
    """Display policy supplied to the read-only GUI."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    search_highlight_background: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class ApplicationConfiguration(BaseModel):
    """Versioned application configuration contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    gui: GuiConfiguration


def load_configuration(path: Path) -> ApplicationConfiguration:
    """Read and validate one YAML configuration file."""
    with path.open(encoding="utf-8") as source:
        return ApplicationConfiguration.model_validate(safe_load(source))


@lru_cache(maxsize=1)
def application_configuration() -> ApplicationConfiguration:
    """Return the packaged application configuration."""
    return load_configuration(Path(__file__).with_name("configuration.yaml"))
