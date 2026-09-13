# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Explicit maintenance options for the complete source-corpus regression.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-corpus-expectations", action="store_true",
        help="Replace subject/hash expectations after a verified tests/data import; review the diff.",
    )
