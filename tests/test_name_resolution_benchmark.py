"""Verify the privacy and structural contract of the synthetic name corpus."""

from scripts.benchmark_name_resolution import CORPUS, load_cases


EXPECTED_PATTERNS = {
    "short-initials",
    "concatenated-full-name",
    "initials-with-numeric-suffix",
    "dotted-full-name",
}


def test_synthetic_corpus_is_anonymized_and_preserves_address_patterns() -> None:
    """Requirement: benchmark evidence must be synthetic while retaining the observed alias shapes."""
    cases = load_cases()

    assert len(cases) == 5
    assert {case.expected_group for case in cases} == {"synthetic-person-001"}
    assert {case.expected_name for case in cases} == {"Avery Morgan"}
    assert EXPECTED_PATTERNS <= {case.pattern for case in cases}
    assert all(case.address.rsplit("@", 1)[1].endswith(".test") for case in cases)


def test_fixture_is_yaml_with_one_case_per_address() -> None:
    """Requirement: benchmark cases remain independently consumable by future resolvers."""
    cases = load_cases()

    assert CORPUS.suffix == ".yaml"
    assert len({case.case_id for case in cases}) == len(cases)
    assert len({case.address for case in cases}) == len(cases)
    assert all("@" in case.address for case in cases)
