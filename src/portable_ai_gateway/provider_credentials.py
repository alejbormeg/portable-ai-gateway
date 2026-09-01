# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
import re


PROVIDER_CREDENTIAL_ENV_PATTERN = r"^PAG_PROVIDER_CREDENTIAL_[A-Z0-9_]{1,64}$"
DEFAULT_VERTEX_CREDENTIAL_ENV = "PAG_PROVIDER_CREDENTIAL_VERTEX_ACCESS_TOKEN"


def validate_provider_credential_env(name: str) -> str:
    """Keep provider-selectable secrets outside the gateway's own environment namespace."""
    if re.fullmatch(PROVIDER_CREDENTIAL_ENV_PATTERN, name) is None:
        raise ValueError(
            "provider credential variables must use the dedicated "
            "PAG_PROVIDER_CREDENTIAL_* namespace"
        )
    return name


def read_provider_credential(name: str) -> str | None:
    return os.getenv(validate_provider_credential_env(name))
