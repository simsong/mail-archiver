# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

from collections.abc import Callable
from typing import ParamSpec

_P = ParamSpec("_P")

def callAfter(function: Callable[_P, object], *args: _P.args, **kwargs: _P.kwargs) -> None: ...
def callLater(delay: float, function: Callable[_P, object], *args: _P.args, **kwargs: _P.kwargs) -> None: ...
