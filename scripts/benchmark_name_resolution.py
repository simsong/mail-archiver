"""Measure the current header-only baseline on the synthetic name corpus."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from pydantic import BaseModel, Field
import yaml


CORPUS = Path(__file__).parents[1] / "benchmarks" / "name_resolution" / "organization_aliases_synthetic.yaml"


class BenchmarkCase(BaseModel):
    case_id: str
    address: str
    pattern: str
    observed_names: list[str] = Field(min_length=1)
    expected_name: str
    expected_group: str


def load_cases(path: Path = CORPUS) -> list[BenchmarkCase]:
    """Load the YAML boundary into strict typed benchmark cases."""
    document = yaml.safe_load(path.read_text())
    if not isinstance(document, list):
        raise ValueError("name-resolution benchmark must be a YAML list")
    return [BenchmarkCase.model_validate(item) for item in document]


def header_only_name(case: BenchmarkCase) -> str:
    """Return the first non-empty RFC display name, without inference."""
    return next((name.strip() for name in case.observed_names if name.strip()), "")


def benchmark(cases: list[BenchmarkCase]) -> dict[str, int]:
    """Return exact-match and evidence coverage for the baseline."""
    exact = sum(header_only_name(case) == case.expected_name for case in cases)
    observed = sum(bool(header_only_name(case)) for case in cases)
    return {
        "cases": len(cases),
        "expected_groups": len({case.expected_group for case in cases}),
        "explicit_name_evidence": observed,
        "exact_name_matches": exact,
    }


def main() -> int:
    cases = load_cases()
    result = benchmark(cases)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
