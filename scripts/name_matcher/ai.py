# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""The sole provider boundary for optional local or remote LLM experiments.

Extraction and baseline matching never import a provider SDK. This module only
creates correlatable deferred requests and parses returned JSONL; transport is
intentionally left to a later, explicitly authorized experiment.
"""

from __future__ import annotations

import hashlib
import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AIProvider(StrEnum):
    LOCAL = "local"
    OPENAI = "openai"
    GEMINI = "gemini"


class AIRequest(BaseModel):
    request_id: str
    provider: AIProvider
    model: str
    prompt_version: str
    identity_1: str
    identity_2: str
    prompt: str
    payload_sha256: str


class AIResult(BaseModel):
    request_id: str
    provider: AIProvider
    status: str
    response: str = ""
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    error: str = ""


def make_request(
    *, provider: AIProvider, model: str, prompt_version: str, identity_1: str, identity_2: str, prompt: str
) -> AIRequest:
    payload_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    stable = "\0".join((provider.value, model, prompt_version, identity_1, identity_2, payload_hash))
    request_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]
    return AIRequest(
        request_id=request_id,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        identity_1=identity_1,
        identity_2=identity_2,
        prompt=prompt,
        payload_sha256=payload_hash,
    )


def write_batch_jsonl(path: Path, requests: list[AIRequest]) -> None:
    """Write provider batch input without making a network request."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        for request in requests:
            if request.provider is AIProvider.OPENAI:
                document: dict[str, Any] = {
                    "custom_id": request.request_id,
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": {"model": request.model, "input": request.prompt},
                }
            elif request.provider is AIProvider.GEMINI:
                document = {
                    "key": request.request_id,
                    "request": {"contents": [{"parts": [{"text": request.prompt}], "role": "user"}]},
                }
            else:
                document = {"key": request.request_id, "model": request.model, "prompt": request.prompt}
            output.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")


def correlate_results(requests: list[AIRequest], results: list[AIResult]) -> list[tuple[AIRequest, AIResult]]:
    """Return results in request order and reject missing, duplicate, or foreign IDs."""
    by_id: dict[str, AIResult] = {}
    expected = {request.request_id for request in requests}
    if len(expected) != len(requests):
        raise ValueError("duplicate request IDs")
    for result in results:
        if result.request_id not in expected:
            raise ValueError(f"unknown result request_id: {result.request_id}")
        if result.request_id in by_id:
            raise ValueError(f"duplicate result request_id: {result.request_id}")
        by_id[result.request_id] = result
    missing = expected - by_id.keys()
    if missing:
        raise ValueError(f"missing results for {len(missing)} request(s)")
    return [(request, by_id[request.request_id]) for request in requests]
