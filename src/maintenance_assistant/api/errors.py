"""Stable errors returned by the application API."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    """An expected API failure with a safe public message."""

    status_code: int
    code: str
    message: str
