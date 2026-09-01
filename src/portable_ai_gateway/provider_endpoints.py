# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import re
from urllib.parse import urlparse


VERTEX_PROJECT_PATTERN = r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$"
VERTEX_LOCATION_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"


def validate_provider_base_url(value: str) -> None:
    """Validate an upstream base URL structurally, never by string prefix."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain user information")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain a query or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("base_url must use HTTPS; plain HTTP is restricted to an exact loopback host")


def vertex_base_url(project: str, location: str) -> str:
    """Build the only default Vertex host shape accepted by the adapter."""
    if re.fullmatch(VERTEX_PROJECT_PATTERN, project) is None:
        raise ValueError("Vertex project is invalid")
    if re.fullmatch(VERTEX_LOCATION_PATTERN, location) is None:
        raise ValueError("Vertex location is invalid")
    return (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/"
        f"{project}/locations/{location}/endpoints/openapi"
    )
