# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Typed evidence exchanged by the experimental name-matcher stages."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class HeaderRole(StrEnum):
    FROM = "from"
    TO = "to"
    CC = "cc"
    BCC = "bcc"


class SignatureMethod(StrEnum):
    DELIMITER = "delimiter"
    MOBILE_FOOTER = "mobile-footer"
    CONTACT_BLOCK = "contact-block"
    SIGNOFF = "signoff"


class SignatureFactKind(StrEnum):
    EMAIL = "email"
    NAME_CANDIDATE = "name-candidate"
    PHONE = "phone"
    TEXT = "text"
    URL = "url"


class HeaderObservation(BaseModel):
    role: HeaderRole
    ordinal: int = Field(ge=0)
    address: str
    display_name: str
    normalized_name: str


class MessageMarker(BaseModel):
    header_name: str
    ordinal: int = Field(ge=0)
    value: str


class SignatureFact(BaseModel):
    ordinal: int = Field(ge=0)
    line_ordinal: int = Field(ge=0)
    kind: SignatureFactKind
    value: str
    normalized_value: str
    confidence: float = Field(ge=0.0, le=1.0)


class SignatureBlock(BaseModel):
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    method: SignatureMethod
    confidence: float = Field(ge=0.0, le=1.0)
    text: str
    facts: list[SignatureFact] = Field(default_factory=list)


class MessageEvidence(BaseModel):
    message_sha256: str
    catalog_message_pk: int
    observed_at: str
    mbox_filename: str
    header_observations: list[HeaderObservation] = Field(default_factory=list)
    markers: list[MessageMarker] = Field(default_factory=list)
    signature: SignatureBlock | None = None
